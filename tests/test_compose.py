from rgit.compose import compose
from rgit.store.store import Store
from rgit.store.models import Capsule, CodeSlice


def _cap(name, symbol):
    return Capsule(id="", name=name, intent=f"{name} intent", status="approved",
                   base_commit="abc", knobs={"lambda": 0.1},
                   data_assumptions="normalized inputs",
                   resurrection_guide=f"reapply {name}", result_summary=None,
                   payload_hash=None,
                   code_slices=[CodeSlice("model.py", symbol, "L1",
                                          "original code", "wrap")])


def test_compose_includes_capsule_fields_and_current_source(git_repo):
    store = Store.init(git_repo)
    (git_repo / "model.py").write_text("def forward(x):\n    return x + 1\n")
    fid = store.add_feature(_cap("scale", "forward"))
    brief = compose(store, [fid])
    item = brief["features"][0]
    assert item["intent"] == "scale intent"
    assert item["resurrection_guide"] == "reapply scale"
    assert item["data_assumptions"] == "normalized inputs"
    assert "return x + 1" in item["current_source"]["forward"]   # current code, not stored
    assert brief["conflicts"] == []


def test_compose_flags_conflicts_on_shared_symbol(git_repo):
    store = Store.init(git_repo)
    (git_repo / "model.py").write_text("def forward(x):\n    return x\n")
    a = store.add_feature(_cap("a", "forward"))
    b = store.add_feature(_cap("b", "forward"))
    brief = compose(store, [a, b])
    assert brief["conflicts"] == [{"file": "model.py", "symbol": "forward",
                                   "features": ["a", "b"]}]


def _conflict_cap(name, code):
    return Capsule(id="", name=name, intent=f"{name} intent", status="approved",
                   base_commit="abc", knobs={name: 1}, data_assumptions=None,
                   resurrection_guide=None, result_summary=None, payload_hash=None,
                   code_slices=[CodeSlice("loss.py", "Loss", None, code, "wrap")])


def test_compose_builds_merge_context_for_colliding_region(git_repo):
    store = Store.init(git_repo)
    a = store.add_feature(_conflict_cap("entropy", "class Loss: a"))
    b = store.add_feature(_conflict_cap("temperature", "class Loss: b"))
    brief = compose(store, [a, b])
    assert len(brief["merge_context"]) == 1
    mc = brief["merge_context"][0]
    assert mc["file"] == "loss.py" and mc["symbol"] == "Loss"
    names = {c["capsule"] for c in mc["contributors"]}
    assert names == {"entropy", "temperature"}
    # each contributor carries its clean slice + intent + knobs for the merge
    contrib = next(c for c in mc["contributors"] if c["capsule"] == "entropy")
    assert contrib["clean_slice"] == "class Loss: a"
    assert contrib["intent"] == "entropy intent"
    assert contrib["knobs"] == {"entropy": 1}


def test_compose_no_merge_context_without_collision(git_repo):
    store = Store.init(git_repo)
    a = store.add_feature(Capsule(
        id="", name="solo", intent="x", status="approved", base_commit="abc",
        knobs={}, data_assumptions=None, resurrection_guide=None,
        result_summary=None, payload_hash=None,
        code_slices=[CodeSlice("a.py", "Foo", None, "code", "wrap")]))
    brief = compose(store, [a])
    assert brief["merge_context"] == []
