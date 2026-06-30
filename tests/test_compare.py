# tests/test_compare.py
from rgit.compare import compare
from rgit.store.store import Store
from rgit.store.models import Capsule, CodeSlice, Run


def _cap(name, intent="x"):
    return Capsule(id="", name=name, intent=intent, status="approved",
                   base_commit="abc", knobs={}, data_assumptions=None,
                   resurrection_guide=None, result_summary=None, payload_hash=None,
                   code_slices=[CodeSlice("loss.py", "Loss", None, "code", "wrap")])


def _run_with(store, metric_val, at):
    rid = store.add_run(Run(id="", cmd="train", artifact_hash="h", metrics=metric_val,
                            base_commit="abc", env=None, created_at=at))
    return rid


def test_compare_ranks_variant_cluster_by_direction(git_repo):
    store = Store.init(git_repo)
    store.set_metric_direction("eval_loss", "lower")
    a = store.add_feature(_cap("temperature"))
    b = store.add_feature(_cap("label-smoothing"))
    store.add_edge(b, a, "variant_of")              # b is a variant of a
    ra = _run_with(store, {"eval_loss": 1.18}, "2026-01-01T00:00:00")
    rb = _run_with(store, {"eval_loss": 1.10}, "2026-01-02T00:00:00")
    store.add_edge(a, ra, "produced")
    store.add_edge(b, rb, "produced")

    result = compare(store, "temperature")          # target is the cluster anchor
    assert {r["feature"] for r in result["rows"]} == {"temperature", "label-smoothing"}
    assert result["metric"] == "eval_loss"
    winner = [r for r in result["rows"] if r["winner"]]
    assert len(winner) == 1 and winner[0]["feature"] == "label-smoothing"
    # Δ is vs the cluster's earliest run (temperature @ 1.18): label-smoothing = 1.10
    by_name = {r["feature"]: r for r in result["rows"]}
    assert by_name["temperature"]["delta"] == 0.0
    assert by_name["label-smoothing"]["delta"] == -0.08


def test_compare_by_symbol_gathers_touchers(git_repo):
    store = Store.init(git_repo)
    store.add_feature(_cap("a"))
    store.add_feature(_cap("b"))
    result = compare(store, "loss.py:Loss")         # both capsules touch Loss
    assert {r["feature"] for r in result["rows"]} == {"a", "b"}


def test_compare_unknown_direction_no_winner(git_repo):
    store = Store.init(git_repo)
    a = store.add_feature(_cap("temperature"))
    ra = _run_with(store, {"eval_loss": 1.18}, "2026-01-01T00:00:00")
    store.add_edge(a, ra, "produced")
    result = compare(store, "temperature")          # no direction set
    assert all(not r["winner"] for r in result["rows"])
    assert result["metric"] == "eval_loss"


def test_compare_unknown_target_raises(git_repo):
    store = Store.init(git_repo)
    import pytest
    with pytest.raises(KeyError):
        compare(store, "nonexistent")
