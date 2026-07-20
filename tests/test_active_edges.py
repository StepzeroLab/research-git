# tests/test_active_edges.py
import sys

from rgit.store.store import Store
from rgit.store.models import Capsule, CodeSlice
from rgit.runner import run_experiment
from rgit.segmenter import MockSegmenter


def _cap(store, name):
    return store.add_feature(Capsule(
        id="", name=name, intent="x", status="approved", base_commit="abc",
        knobs={}, data_assumptions=None, resurrection_guide=None,
        result_summary=None, payload_hash=None,
        code_slices=[CodeSlice("model.py", "forward", None, "x", "wrap")]))


def test_active_edges_round_trip(git_repo):
    store = Store.init(git_repo)
    a, b = _cap(store, "a"), _cap(store, "b")
    store.add_edge("run_1", a, "active")
    store.add_edge("run_1", b, "active")
    assert set(store.active_features("run_1")) == {a, b}
    assert store.runs_with_active(a) == ["run_1"]


def test_run_experiment_writes_active_edges(git_repo):
    store = Store.init(git_repo)
    a = _cap(store, "a")
    run_id, _ = run_experiment(
        store, [sys.executable, "-c", "pass"], MockSegmenter([]),
        now="2026-01-01T00:00:00", active=[a])
    assert store.active_features(run_id) == [a]


def test_run_experiment_without_active_writes_none(git_repo):
    store = Store.init(git_repo)
    run_id, _ = run_experiment(
        store, [sys.executable, "-c", "pass"], MockSegmenter([]),
        now="2026-01-01T00:00:00")
    assert store.active_features(run_id) == []
