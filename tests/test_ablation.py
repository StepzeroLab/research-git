# tests/test_ablation.py
from rgit.ablation import ablation
from rgit.store.store import Store
from rgit.store.models import Capsule, CodeSlice, Run


def _cap(store, name):
    return store.add_feature(Capsule(
        id="", name=name, intent="x", status="approved", base_commit="abc",
        knobs={}, data_assumptions=None, resurrection_guide=None,
        result_summary=None, payload_hash=None,
        code_slices=[CodeSlice("loss.py", "Loss", None, "c", "wrap")]))


def _run(store, metrics, at):
    return store.add_run(Run(id="", cmd="t", artifact_hash="h", metrics=metrics,
                             base_commit="abc", env=None, created_at=at))


def test_ablation_buckets_runs_by_active_subset(git_repo):
    store = Store.init(git_repo)
    store.set_metric_direction("eval_loss", "lower")
    a, b = _cap(store, "A"), _cap(store, "B")
    r_base = _run(store, {"eval_loss": 1.30}, "2026-01-01T00:00:00")    # {}
    r_a = _run(store, {"eval_loss": 1.18}, "2026-01-02T00:00:00")
    store.add_edge(r_a, a, "active")                                    # {A}
    r_ab = _run(store, {"eval_loss": 1.05}, "2026-01-03T00:00:00")
    store.add_edge(r_ab, a, "active"); store.add_edge(r_ab, b, "active")  # {A,B}

    grid = ablation(store, [a, b])
    subsets = {tuple(sorted(row["subset"])): row for row in grid["rows"]}
    assert subsets[()]["cells"]["eval_loss"] == 1.30
    assert subsets[("A",)]["cells"]["eval_loss"] == 1.18
    assert subsets[("A", "B")]["cells"]["eval_loss"] == 1.05
    # {B} alone had no run -> empty cell
    assert subsets[("B",)]["cells"]["eval_loss"] is None
    # winner column marks the lowest eval_loss row ({A,B})
    assert grid["winners"]["eval_loss"] == ("A", "B")


def test_ablation_falls_back_to_produced_edges(git_repo):
    store = Store.init(git_repo)
    a = _cap(store, "A")
    r = _run(store, {"eval_loss": 1.0}, "2026-01-01T00:00:00")
    store.add_edge(a, r, "produced")        # no active edge; produced is the fallback
    grid = ablation(store, [a])
    subsets = {tuple(sorted(row["subset"])): row for row in grid["rows"]}
    assert subsets[("A",)]["cells"]["eval_loss"] == 1.0


def test_ablation_latest_run_wins_a_cell(git_repo):
    store = Store.init(git_repo)
    a = _cap(store, "A")
    old = _run(store, {"eval_loss": 2.0}, "2026-01-01T00:00:00")
    new = _run(store, {"eval_loss": 1.0}, "2026-01-09T00:00:00")
    for r in (old, new):
        store.add_edge(r, a, "active")
    grid = ablation(store, [a])
    subsets = {tuple(sorted(row["subset"])): row for row in grid["rows"]}
    assert subsets[("A",)]["cells"]["eval_loss"] == 1.0     # latest by created_at
