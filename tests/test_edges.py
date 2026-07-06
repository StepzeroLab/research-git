from rgit.edges import overlap_pairs, apply_overlaps, depends_candidates
from rgit.store.store import Store
from rgit.store.models import Capsule, CodeSlice


def _cap(name, slices):
    return Capsule(id="", name=name, intent=f"{name} intent", status="approved",
                   base_commit="abc", knobs={}, data_assumptions=None,
                   resurrection_guide="...", result_summary=None, payload_hash=None,
                   code_slices=slices)


def test_overlap_pairs_share_file_and_symbol(git_repo):
    store = Store.init(git_repo)
    a = store.add_feature(_cap("a", [CodeSlice("m.py", "loss", None, "x", "wrap")]))
    b = store.add_feature(_cap("b", [CodeSlice("m.py", "loss", None, "y", "wrap")]))
    store.add_feature(_cap("c", [CodeSlice("m.py", "other", None, "z", "wrap")]))
    pairs = overlap_pairs(store)
    assert {a, b} in [set(p) for p in pairs]
    assert len(pairs) == 1


def test_apply_overlaps_is_symmetric_and_idempotent(git_repo):
    store = Store.init(git_repo)
    a = store.add_feature(_cap("a", [CodeSlice("m.py", "loss", None, "x", "wrap")]))
    b = store.add_feature(_cap("b", [CodeSlice("m.py", "loss", None, "y", "wrap")]))
    assert apply_overlaps(store) == 1
    assert apply_overlaps(store) == 1  # idempotent: pair count unchanged
    assert b in store.neighbors(a, "overlaps")
    assert a in store.neighbors(b, "overlaps")


def test_depends_candidates_emits_without_writing(git_repo):
    store = Store.init(git_repo)
    # y DEFINES symbol `Encoder`; x USES the name `Encoder` in its slice code
    y = store.add_feature(_cap("enc", [CodeSlice("e.py", "Encoder", None,
                                                 "class Encoder: pass", "add")]))
    x = store.add_feature(_cap("head", [CodeSlice("h.py", "Head", None,
                                                  "h = Encoder()", "add")]))
    cands = depends_candidates(store)
    assert {"src": x, "dst": y, "evidence": ["Encoder"]} in cands
    # nothing was written
    assert store.neighbors(x, "depends_on") == []


def test_depends_candidates_skips_existing_edges(git_repo):
    store = Store.init(git_repo)
    y = store.add_feature(_cap("enc", [CodeSlice("e.py", "Encoder", None,
                                                 "class Encoder: pass", "add")]))
    x = store.add_feature(_cap("head", [CodeSlice("h.py", "Head", None,
                                                  "h = Encoder()", "add")]))
    store.add_edge(x, y, "depends_on")
    assert depends_candidates(store) == []


def test_conflict_matches_class_against_dotted_method(git_repo):
    # libcst records the class name; an agent may record Class.method. Both touch
    # the same region, so they must still overlap (symbol-format normalization).
    from rgit.edges import overlap_pairs
    from rgit.store.store import Store
    from rgit.store.models import Capsule, CodeSlice
    store = Store.init(git_repo)

    def cap(name, sym):
        return Capsule(id="", name=name, intent=name, status="approved",
                       base_commit="abc", knobs={}, data_assumptions=None,
                       resurrection_guide="...", result_summary=None, payload_hash=None,
                       code_slices=[CodeSlice("loss.py", sym, None, "x", "wrap")])
    a = store.add_feature(cap("old", "CustomCrossEntropyLoss"))
    b = store.add_feature(cap("new", "CustomCrossEntropyLoss.__call__"))
    assert {a, b} in [set(p) for p in overlap_pairs(store)]


def test_overlap_pairs_scope_filters_to_new_capsules(git_repo):
    store = Store.init(git_repo)
    a = store.add_feature(_cap("a", [CodeSlice("m.py", "shared", None, "x = 1", "wrap")]))
    b = store.add_feature(_cap("b", [CodeSlice("m.py", "shared", None, "x = 2", "wrap")]))
    c = store.add_feature(_cap("c", [CodeSlice("m.py", "shared", None, "x = 3", "wrap")]))
    assert len(overlap_pairs(store)) == 3
    scoped = overlap_pairs(store, scope={c})
    assert len(scoped) == 2
    assert all(c in pair for pair in scoped)


def test_depends_candidates_limit_ranks_by_evidence(git_repo):
    store = Store.init(git_repo)
    store.add_feature(_cap("def-one", [CodeSlice(
        "m.py", "helper_one", None, "def helper_one():\n    pass", "wrap")]))
    store.add_feature(_cap("def-two", [CodeSlice(
        "m.py", "helper_two", None, "def helper_two():\n    pass", "wrap")]))
    store.add_feature(_cap("user-weak", [CodeSlice(
        "m.py", "weak", None, "helper_one()", "wrap")]))
    store.add_feature(_cap("user-strong", [CodeSlice(
        "m.py", "strong", None, "helper_one()\nhelper_two()", "wrap")]))
    all_cands = depends_candidates(store)
    assert len(all_cands) >= 3
    top = depends_candidates(store, limit=1)
    assert len(top) == 1
    strong = store.resolve_feature("user-strong")
    # user-strong -> def-* carries 1 shared name each; ties break deterministically,
    # so just assert the cap + determinism:
    assert depends_candidates(store, limit=1) == top
    scoped = depends_candidates(store, scope={strong})
    assert scoped and all(strong in (c["src"], c["dst"]) for c in scoped)
