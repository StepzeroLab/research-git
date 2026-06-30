# tests/test_metricdir.py
from rgit.metricdir import suggest, best_index
from rgit.store.store import Store


def test_suggest_maps_names_by_heuristic():
    s = suggest(["eval_loss", "val_accuracy", "ppl", "f1", "reward", "mystery"])
    assert s["eval_loss"] == "lower"
    assert s["ppl"] == "lower"
    assert s["val_accuracy"] == "higher"
    assert s["f1"] == "higher"
    assert s["reward"] == "higher"
    assert "mystery" not in s            # no confident guess -> omitted


def test_best_index_lower_picks_minimum(git_repo):
    store = Store.init(git_repo)
    store.set_metric_direction("loss", "lower")
    # values aligned to rows; None means the row has no value for this metric
    assert best_index(store, "loss", [1.2, 0.9, 1.0]) == 1


def test_best_index_higher_picks_maximum(git_repo):
    store = Store.init(git_repo)
    store.set_metric_direction("acc", "higher")
    assert best_index(store, "acc", [0.7, 0.9, None]) == 1


def test_best_index_unknown_direction_returns_none(git_repo):
    store = Store.init(git_repo)
    assert best_index(store, "loss", [1.2, 0.9]) is None   # direction unset


def test_best_index_all_none_returns_none(git_repo):
    store = Store.init(git_repo)
    store.set_metric_direction("loss", "lower")
    assert best_index(store, "loss", [None, None]) is None
