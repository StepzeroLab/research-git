import sys

from rgit.runner import run_experiment
from rgit.segmenter import MockSegmenter
from rgit.store.store import Store
from rgit.store.models import Capsule, CodeSlice


def _seed_capsule(store, name="src"):
    return store.add_feature(Capsule(
        id="", name=name, intent="i", status="approved", base_commit="b",
        knobs={}, data_assumptions=None, resurrection_guide=None, result_summary=None,
        payload_hash=None, code_slices=[CodeSlice("model.py", "forward", "L1", "x", "wrap")]))


def test_run_experiment_freezes_records_and_segments(git_repo):
    store = Store.init(git_repo)
    # an experiment that mutates code AND emits a metric
    (git_repo / "model.py").write_text("def forward(x):\n    return x * 3\n")
    (git_repo / "train.py").write_text("print('RGIT_METRIC acc=0.95')\n")
    candidate = {"name": "triple", "intent": "scale by 3",
                 "code_slices": [{"file": "model.py", "symbol": "forward",
                                  "anchor": "L1", "code": "x*3", "kind": "wrap"}],
                 "knobs": {}, "data_assumptions": None,
                 "resurrection_guide": "x3", "confidence": 0.9}
    run_id, prop_id = run_experiment(
        store, cmd=[sys.executable, "train.py"], segmenter=MockSegmenter([candidate]),
        now="2026-06-16T00:00:00")

    run = store.get_run(run_id)
    assert run.metrics == {"acc": 0.95}
    assert run.artifact_hash                       # froze the worktree
    prop = store.get_proposal(prop_id)
    assert prop.run_id == run_id                   # run linked to its proposal
    assert prop.candidates[0]["name"] == "triple"


def test_run_experiment_handles_missing_metrics(git_repo):
    store = Store.init(git_repo)
    (git_repo / "train.py").write_text("print('hello')\n")
    run_id, _ = run_experiment(store, [sys.executable, "train.py"],
                               MockSegmenter([]), now="2026-06-16T00:00:00")
    assert store.get_run(run_id).metrics is None


def test_run_experiment_passes_env_to_subprocess(git_repo):
    # `env` is recorded on the run row AND must reach the child process.
    store = Store.init(git_repo)
    (git_repo / "train.py").write_text(
        "import os\nprint('RGIT_METRIC v=' + os.environ['RGIT_TEST_V'])\n")
    run_id, _ = run_experiment(store, [sys.executable, "train.py"],
                               MockSegmenter([]), now="2026-06-16T00:00:00",
                               env={"RGIT_TEST_V": "7"})
    assert store.get_run(run_id).metrics == {"v": 7.0}


def test_run_survives_malformed_metric_and_still_freezes(git_repo):
    # a single bad metric line must NOT discard the (already-spent) run/artifact
    store = Store.init(git_repo)
    (git_repo / "train.py").write_text("print('RGIT_METRIC acc=1.2.3')\n")
    run_id, _ = run_experiment(store, [sys.executable, "train.py"],
                               MockSegmenter([]), now="2026-06-16T00:00:00")
    run = store.get_run(run_id)
    assert run.artifact_hash          # artifact frozen despite bad metric
    assert run.metrics is None        # bad metric skipped, not crashed


def test_run_from_links_lineage_and_carries_to_proposal(git_repo):
    store = Store.init(git_repo)
    src = _seed_capsule(store)
    (git_repo / "train.py").write_text("print('hi')\n")
    run_id, prop_id = run_experiment(store, [sys.executable, "train.py"], MockSegmenter([]),
                                     now="2026-06-16T00:00:00", from_features=[src])
    assert store.neighbors(src, "produced") == [run_id]           # source -> this run
    assert store.get_proposal(prop_id).from_features == [src]      # lineage for approval


def test_failed_run_records_nonzero_returncode(git_repo):
    from rgit.runner import run_experiment
    from rgit.segmenter import HeuristicSegmenter
    from rgit.store.store import Store
    store = Store.init(git_repo)
    result = run_experiment(store, [sys.executable, "-c", "import sys; sys.exit(3)"],
                            HeuristicSegmenter(), now="t")
    run_id, _ = result
    assert store.get_run(run_id).returncode == 3
    assert result.returncode == 3


def test_missing_command_records_127_not_traceback(git_repo):
    store = Store.init(git_repo)
    result = run_experiment(store, ["definitely-not-a-real-rgit-command-xyz"],
                            MockSegmenter([]), now="t")
    run = store.get_run(result.run_id)
    assert result.returncode == 127
    assert run.returncode == 127
    assert "command not found" in result.stderr


def test_run_decodes_invalid_utf8_output_with_replacement(git_repo):
    store = Store.init(git_repo)
    result = run_experiment(
        store,
        [sys.executable, "-c",
         "import sys; sys.stdout.buffer.write(b'bad\\xffout\\n')"],
        MockSegmenter([]), now="t")
    assert result.returncode == 0
    assert "bad" in result.stdout
    assert "\ufffd" in result.stdout
