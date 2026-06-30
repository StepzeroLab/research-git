import subprocess
import sys
from rgit.runner import run_experiment
from rgit.curation import approve
from rgit.recall import recall
from rgit.compose import compose
from rgit.gitutil import materialize
from rgit.segmenter import MockSegmenter
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
