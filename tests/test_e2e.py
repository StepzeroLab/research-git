import subprocess
import sys
from conftest import make_candidate
from rgit.runner import run_experiment
from rgit.curation import approve, decide
from rgit.recall import recall
from rgit.compose import compose
from rgit.gitutil import materialize
from rgit.segmenter import MockSegmenter, segment_diff
from rgit.store.store import Store


def _commit(repo, msg):
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-q", "-m", msg], cwd=repo, check=True,
                   capture_output=True)


def test_full_memory_loop(git_repo):
    store = Store.init(git_repo)

    # 1. messy work: add a contrastive loss + emit a metric
    (git_repo / "model.py").write_text(
        "def forward(x):\n    return x\n\n"
        "def compute_loss(p, y):\n    return ((p - y) ** 2).mean() + 0.1 * aux(p)\n")
    (git_repo / "train.py").write_text("print('RGIT_METRIC acc=0.93')\n")
    candidate = {
        "name": "contrastive-loss-aux", "intent": "add aux contrastive loss term",
        "code_slices": [{"file": "model.py", "symbol": "compute_loss",
                         "anchor": "L4-L5", "code": "+ 0.1 * aux(p)", "kind": "insert"}],
        "knobs": {"lambda": 0.1}, "data_assumptions": "normalized embeddings",
        "resurrection_guide": "add 0.1*aux(p) inside compute_loss", "confidence": 0.95}

    # 2. rgit run -> freeze + run node + proposal
    run_id, prop_id = run_experiment(store, [sys.executable, "train.py"],
                                     MockSegmenter([candidate]), now="2026-06-16T00:00:00")
    assert store.get_run(run_id).metrics == {"acc": 0.93}
    frozen_hash = store.get_run(run_id).artifact_hash

    # 3. approve -> capsule with produced edge to the run
    fid = approve(store, prop_id, 0, name="contrastive-loss-aux")
    assert store.neighbors(fid, "produced") == [run_id]

    # commit so HEAD advances, then refactor infra under the feature
    _commit(git_repo, "feature + infra")
    (git_repo / "model.py").write_text(
        "def forward(x, scale=1):\n    return x * scale\n\n"
        "def compute_loss(pred, target):\n    return ((pred - target) ** 2).mean()\n")

    # 4. recall + compose against the *refactored* code
    hits = recall(store, "contrastive")
    assert hits[0]["capsule"].name == "contrastive-loss-aux"
    brief = compose(store, [fid])
    item = brief["features"][0]
    assert item["resurrection_guide"] == "add 0.1*aux(p) inside compute_loss"
    assert "pred" in item["current_source"]["compute_loss"]   # sees current, refactored code

    # 5. reproducibility: the frozen artifact replays byte-identically
    dest = git_repo / ".rgit" / "replay"
    materialize(store.objects, frozen_hash, dest)
    assert "0.1 * aux(p)" in (dest / "model.py").read_text()   # exact code that ran


def test_post_commit_hook_captures_the_commit(git_repo, tmp_path, monkeypatch):
    # Acceptance for issue #20: install hooks, make a commit, and the hook by
    # itself must leave a pending proposal carrying that commit's diff.
    import json
    import os
    import stat
    if os.name == "nt":
        import pytest
        pytest.skip("POSIX shim; the hook itself is /bin/sh")
    from rgit.hooks import install_hooks
    from rgit.gitutil import current_commit

    Store.init(git_repo)
    assert install_hooks(git_repo)["action"] == "installed"

    shim_dir = tmp_path / "bin"
    shim_dir.mkdir()
    shim = shim_dir / "rgit"
    shim.write_text(
        "#!/bin/sh\n"
        f'exec "{sys.executable}" -c '
        "'import sys; from rgit.cli import main; sys.exit(main(sys.argv[1:]))' "
        '"$@"\n')
    shim.chmod(shim.stat().st_mode | stat.S_IXUSR)

    (git_repo / "model.py").write_text("def forward(x):\n    return x * 2\n")
    env = {**os.environ, "PATH": f"{shim_dir}{os.pathsep}{os.environ['PATH']}",
           "PYTHONPATH": os.pathsep.join(p for p in sys.path if p)}
    subprocess.run(["git", "add", "."], cwd=git_repo, check=True,
                   capture_output=True)
    subprocess.run(["git", "commit", "-q", "-m", "double"], cwd=git_repo,
                   check=True, capture_output=True, env=env)

    props = Store.open(git_repo).list_proposals("open")
    assert len(props) == 1
    store = Store.open(git_repo)
    assert props[0].trigger == "commit"
    assert props[0].source_commit == current_commit(git_repo)
    assert "x * 2" in store.objects.get(props[0].diff_ref).decode()


def test_decide_multi_capsule_end_to_end(git_repo):
    store = Store.init(git_repo)
    (git_repo / "model.py").write_text(
        "def forward(x):\n    return rerank(cache(x))\n")

    def cand(name, intent):
        return make_candidate(name, intent, anchor="L2", guide=f"re-add {name}")
    # the CaptureResult return value IS the proposal id (str subclass)
    pid = segment_diff(store, "manual", MockSegmenter([
        cand("rerank-retrieval", "re-rank retrieved candidates"),
        cand("query-cache", "cache query embeddings"),
        cand("debug-logging", "temporary logging"),
    ]), None)

    approved, dropped = decide(store, pid, ["rerank-retrieval", "query-cache"])
    assert len(approved) == 2
    assert dropped == ["debug-logging"]

    hits = recall(store, "rerank retrieved")
    assert hits and hits[0]["capsule"].name == "rerank-retrieval"
    hits = recall(store, "cache query embeddings")
    assert hits and hits[0]["capsule"].name == "query-cache"
    assert "debug-logging" not in {c.name for c in store.list_features()}


def test_history_digest_backfill_loop(history_repo):
    from conftest import commit_file, make_candidate, revert_head
    from rgit import digestqueue
    from rgit.curation import validate_candidates
    from rgit.recall import recall as recall_fn
    from rgit.segmenter import HeuristicSegmenter

    T0 = 1_700_000_000
    DAY = 86_400
    # scripted mature history: a landed feature, a reverted experiment, docs noise
    commit_file(history_repo, "model.py",
                "def forward(x):\n    return x * 2\n", "feat: scaled forward",
                when=T0)
    commit_file(history_repo, "exp.py",
                "def trick(x):\n    return x + 1\n", "exp: additive trick",
                when=T0 + 5 * DAY, author="u2")
    revert_head(history_repo, when=T0 + 6 * DAY, author="u2")
    commit_file(history_repo, "README.md", "# readme\n", "docs", when=T0 + 9 * DAY)

    store = Store.init(history_repo)

    # 1. plan (free) — the reverted experiment is a dead unit, docs pre-skipped
    plan = digestqueue.scan_into_store(store, now="2026-07-05T00:00:00")
    assert plan["pending"] == 2                     # landed + dead; docs skipped

    # 2. stage — proposals pinned to historical commits, trigger=backfill
    items = digestqueue.next_batch(store, segmenter=HeuristicSegmenter(),
                                   now="2026-07-05T00:00:01")
    assert {i["kind"] for i in items} == {"landed", "dead"}

    # 3. simulated capsule-segmenter output -> resegment -> accept (no gate)
    fids = {}
    for item in items:
        name = "scaled-forward" if item["kind"] == "landed" else "additive-trick"
        file = "model.py" if item["kind"] == "landed" else "exp.py"
        cands = [make_candidate(name, file=file, symbol="forward"
                                if item["kind"] == "landed" else "trick")]
        validate_candidates(cands)
        store.set_proposal_candidates(item["proposal_id"], cands)
        res = digestqueue.accept(store, item["proposal_id"],
                                 now="2026-07-05T00:00:02")
        (n, fid), = res["capsules"]
        fids[n] = fid

    dead_cap = store.get_feature(fids["additive-trick"])
    assert dead_cap.origin == "backfill"
    assert "reverted by" in dead_cap.result_summary.notes
    assert dead_cap.base_commit                     # pinned in history

    # 4. recall sees backfill; the filter hides it
    hits = {r["capsule"].name for r in recall_fn(store, "additive trick")}
    assert "additive-trick" in hits
    hits_live = {r["capsule"].name
                 for r in recall_fn(store, "additive trick", exclude_backfill=True)}
    assert "additive-trick" not in hits_live

    # 5. queue is drained; clear is the regret channel
    assert digestqueue.status(store)["pending_in_mode"] == 0
    cleared = digestqueue.clear(store)
    assert cleared["capsules_removed"] == 2
    assert digestqueue.status(store)["pending_in_mode"] == 2
