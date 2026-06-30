from rgit.ranking import tokenize, lexical_score, score
from rgit.store.models import Capsule, CodeSlice


def _cap(name="f", intent="", guide="", knobs=None, slices=None):
    return Capsule(id="", name=name, intent=intent, status="approved",
                   base_commit="abc", knobs=knobs or {}, data_assumptions=None,
                   resurrection_guide=guide, result_summary=None, payload_hash=None,
                   code_slices=slices or [])


def test_tokenize_lowercases_and_splits():
    assert tokenize("Entropy-Reg Loss!") == ["entropy", "reg", "loss"]


def test_wildcard_query_is_safe():
    cap = _cap(intent="add entropy loss")
    # %/_ must not blow up or act as wildcards — they are just non-matching tokens
    assert lexical_score(cap, tokenize("%_%")) == 0.0


def test_intent_hit_outranks_guide_hit():
    in_intent = _cap(name="a", intent="entropy regularizer")
    in_guide = _cap(name="b", guide="entropy regularizer")
    toks = tokenize("entropy")
    assert lexical_score(in_intent, toks) > lexical_score(in_guide, toks)


def test_structural_boost_on_symbol_match():
    plain = _cap(intent="loss tweak")
    structural = _cap(intent="loss tweak",
                      slices=[CodeSlice("train.py", "loss", None, "code", "wrap")])
    toks = tokenize("loss")
    assert lexical_score(structural, toks) > lexical_score(plain, toks)


def test_score_adds_edge_boost():
    cap = _cap(intent="entropy")
    toks = tokenize("entropy")
    base = score(cap, toks, [])
    boosted = score(cap, toks, [10.0])
    assert boosted == base + 0.5 * 10.0
