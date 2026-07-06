from rgit.recall import recall
from rgit.store.store import Store
from rgit.store.models import Capsule, CodeSlice


def _cap(name, intent, origin="live"):
    return Capsule(id="", name=name, intent=intent, status="approved",
                   base_commit="abc", knobs={}, data_assumptions=None,
                   resurrection_guide="...", result_summary=None, payload_hash=None,
                   code_slices=[CodeSlice("model.py", "forward", "L1", "x", "wrap")],
                   origin=origin)


def test_recall_returns_match_with_depends_on_subgraph(git_repo):
    store = Store.init(git_repo)
    base = store.add_feature(_cap("projection-head", "add projection head"))
    loss = store.add_feature(_cap("contrastive-loss", "add aux contrastive loss"))
    store.add_edge(loss, base, "depends_on")
    results = recall(store, "contrastive")
    assert len(results) == 1
    assert results[0]["capsule"].name == "contrastive-loss"
    assert results[0]["depends_on"][0].name == "projection-head"
    assert "score" in results[0]
    assert "overlaps" in results[0]


def test_recall_no_match_returns_empty(git_repo):
    store = Store.init(git_repo)
    store.add_feature(_cap("dropout", "raise dropout"))
    assert recall(store, "transformer") == []


def test_recall_ranks_by_score(git_repo):
    store = Store.init(git_repo)
    store.add_feature(_cap("weak", "mentions entropy once"))
    store.add_feature(_cap("entropy-strong", "entropy entropy regularizer entropy"))
    results = recall(store, "entropy")
    assert [r["capsule"].name for r in results][0] == "entropy-strong"


def test_recall_includes_overlaps_subgraph(git_repo):
    store = Store.init(git_repo)
    a = store.add_feature(_cap("alpha", "entropy a"))
    b = store.add_feature(_cap("beta", "unrelated b"))
    store.add_edge(a, b, "overlaps")
    results = recall(store, "entropy")
    assert results[0]["overlaps"][0].name == "beta"


def test_recall_includes_richer_same_region_under_overlaps(git_repo):
    store = Store.init(git_repo)
    a = store.add_feature(_cap("alpha", "entropy a"))
    b = store.add_feature(_cap("beta", "unrelated b"))
    store.add_edge(a, b, "alternative_to")
    results = recall(store, "entropy")
    assert results[0]["overlaps"][0].name == "beta"


def test_recall_skips_non_approved(git_repo):
    store = Store.init(git_repo)
    cap = _cap("proposed-one", "entropy proposed")
    cap.status = "proposed"
    store.add_feature(cap)
    assert recall(store, "entropy") == []


def test_recall_exclude_backfill(git_repo):
    store = Store.init(git_repo)
    store.add_feature(_cap("live-rerank", "rerank results"))
    store.add_feature(_cap("old-rerank", "rerank results", origin="backfill"))
    names = {r["capsule"].name for r in recall(store, "rerank")}
    assert names == {"live-rerank", "old-rerank"}
    filtered = {r["capsule"].name
                for r in recall(store, "rerank", exclude_backfill=True)}
    assert filtered == {"live-rerank"}
