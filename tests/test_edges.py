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
