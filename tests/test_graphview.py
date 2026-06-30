from rgit.graphview import _collect
from rgit.store.store import Store
from rgit.store.models import Capsule, CodeSlice, Run


def _cap(store, name):
    return store.add_feature(Capsule(
        id="", name=name, intent="i", status="approved", base_commit="abc",
        knobs={}, data_assumptions=None, resurrection_guide=None,
        result_summary=None, payload_hash=None,
        code_slices=[CodeSlice("m.py", "f", None, "x", "wrap")]))


def _run(store, metrics, at):
    return store.add_run(Run(id="", cmd="t", artifact_hash="h", metrics=metrics,
                             base_commit="abc", env=None, created_at=at))


def test_collect_capsules_and_capsule_edges(git_repo):
    store = Store.init(git_repo)
    a = _cap(store, "a"); b = _cap(store, "b")
    store.add_edge(b, a, "variant_of")
    g = _collect(store, include_runs=False)
    assert {c.id for c in g["capsules"]} == {a, b}
    assert (b, a, "variant_of") in g["edges"]
    assert g["runs"] == []


def test_collect_excludes_runs_unless_requested(git_repo):
    store = Store.init(git_repo)
    a = _cap(store, "a")
    r = _run(store, {"loss": 1.0}, "2026-01-01T00:00:00")
    store.add_edge(a, r, "produced")
    g0 = _collect(store, include_runs=False)
    assert all(t not in ("produced", "active") for _, _, t in g0["edges"])
    assert g0["runs"] == []
    g1 = _collect(store, include_runs=True)
    assert {x.id for x in g1["runs"]} == {r}
    assert (a, r, "produced") in g1["edges"]


from rgit.graphview import to_text


def test_text_renders_variant_tree_with_markers(git_repo):
    store = Store.init(git_repo)
    a = _cap(store, "temp-0.7"); b = _cap(store, "temp-1.0"); c = _cap(store, "temp-1.3")
    e = _cap(store, "entropy"); tok = _cap(store, "tokenizer")
    store.add_edge(b, a, "variant_of")          # temp-1.0 is a variant of temp-0.7
    store.add_edge(c, b, "variant_of")          # temp-1.3 variant of temp-1.0
    store.add_edge(b, e, "overlaps"); store.add_edge(e, b, "overlaps")
    store.add_edge(e, tok, "depends_on")        # entropy depends on tokenizer
    out = to_text(store, include_runs=False)
    lines = out.splitlines()
    # root has no connector; children are indented under it
    assert any(l == "temp-0.7" for l in lines)
    assert any(l.lstrip().startswith("└─ temp-1.0") for l in lines)
    assert any("temp-1.3" in l and l.startswith("   ") for l in lines)
    # overlap marker shows on both endpoints, depends marker on the dependent
    assert any("temp-1.0" in l and "≈ entropy" in l for l in lines)
    assert any(l.startswith("entropy") and "≈ temp-1.0" in l and "→needs tokenizer" in l
               for l in lines)


def test_text_singleton_and_empty(git_repo):
    store = Store.init(git_repo)
    assert to_text(store) == "(no capsules)"
    _cap(store, "solo")
    assert "solo" in to_text(store)


def test_text_runs_nested_under_capsule(git_repo):
    store = Store.init(git_repo)
    a = _cap(store, "a")
    r = _run(store, {"loss": 0.5}, "2026-01-01T00:00:00")
    store.add_edge(a, r, "produced")
    assert r not in to_text(store, include_runs=False)
    out = to_text(store, include_runs=True)
    assert r in out and "loss" in out


from rgit.graphview import to_dot


def test_dot_nodes_and_edge_styles(git_repo):
    store = Store.init(git_repo)
    a = _cap(store, "a"); b = _cap(store, "b"); e = _cap(store, "e")
    store.add_edge(b, a, "variant_of")
    store.add_edge(a, e, "overlaps"); store.add_edge(e, a, "overlaps")
    store.add_edge(a, e, "depends_on")
    dot = to_dot(store)
    assert dot.startswith("digraph rgit {")
    assert dot.rstrip().endswith("}")
    assert "shape=box" in dot
    assert 'label="variant_of"' in dot
    assert "color=gray style=dashed dir=none" in dot     # overlaps
    assert "color=blue" in dot                            # depends_on
    # symmetric overlaps collapses to ONE drawn edge
    assert dot.count("dir=none") == 1


def test_dot_runs_toggle_and_empty(git_repo):
    store = Store.init(git_repo)
    assert to_dot(store).startswith("digraph rgit {")     # empty is still valid
    a = _cap(store, "a")
    r = _run(store, {"loss": 0.5}, "2026-01-01T00:00:00")
    store.add_edge(a, r, "produced")
    assert "shape=ellipse" not in to_dot(store, include_runs=False)
    withruns = to_dot(store, include_runs=True)
    assert "shape=ellipse" in withruns and 'label="produced"' in withruns


def test_dot_escapes_quotes_in_name(git_repo):
    store = Store.init(git_repo)
    _cap(store, 'we"ird')
    dot = to_dot(store)
    assert '\\"' in dot                                   # quote escaped
    assert dot.count("digraph rgit {") == 1


def test_dot_escapes_backslash_in_name(git_repo):
    store = Store.init(git_repo)
    _cap(store, "we\\")                       # name ends with a backslash
    dot = to_dot(store)
    assert "\\\\" in dot                       # backslash doubled, closing quote not escaped
    assert dot.rstrip().endswith("}")
    assert dot.count('"') % 2 == 0             # quotes stay balanced


def test_text_run_not_duplicated_when_produced_and_active(git_repo):
    store = Store.init(git_repo)
    a = _cap(store, "a")
    r = _run(store, {"loss": 0.5}, "2026-01-01T00:00:00")
    store.add_edge(a, r, "produced")
    store.add_edge(r, a, "active")            # same capsule both produced AND activated r
    out = to_text(store, include_runs=True)
    assert out.count(r) == 1


from rgit.graphview import to_mermaid


def test_mermaid_nodes_and_edges(git_repo):
    store = Store.init(git_repo)
    a = _cap(store, "a"); b = _cap(store, "b"); e = _cap(store, "e")
    store.add_edge(b, a, "variant_of")
    store.add_edge(a, e, "overlaps"); store.add_edge(e, a, "overlaps")
    store.add_edge(a, e, "depends_on")
    m = to_mermaid(store)
    assert m.startswith("graph LR")
    assert f'{a}["a"]' in m
    assert "-->|variant_of|" in m
    assert "---|overlaps|" in m
    assert m.count("---|overlaps|") == 1     # symmetric deduped
    assert "-->|depends_on|" in m


def test_mermaid_runs_and_empty(git_repo):
    store = Store.init(git_repo)
    assert to_mermaid(store) == "graph LR"          # empty is still valid
    a = _cap(store, "a")
    r = _run(store, {"loss": 0.5}, "2026-01-01T00:00:00")
    store.add_edge(a, r, "produced")
    assert "([" not in to_mermaid(store, include_runs=False)
    withruns = to_mermaid(store, include_runs=True)
    assert f"{r}([" in withruns and "-->|produced|" in withruns


def test_mermaid_escapes_quotes(git_repo):
    store = Store.init(git_repo)
    _cap(store, 'we"ird')
    assert "#quot;" in to_mermaid(store)


# --- richer same-region edges --------------------------------------------

def test_text_renders_richer_same_region_markers(git_repo):
    store = Store.init(git_repo)
    a = _cap(store, "alt-a"); b = _cap(store, "alt-b")
    c = _cap(store, "comp-a"); d = _cap(store, "comp-b")
    s = _cap(store, "new"); o = _cap(store, "old")
    store.add_edge(a, b, "alternative_to"); store.add_edge(b, a, "alternative_to")
    store.add_edge(c, d, "composable_with"); store.add_edge(d, c, "composable_with")
    store.add_edge(s, o, "supersedes")
    out = to_text(store)
    lines = out.splitlines()
    # alternative_to symmetric: marker on both endpoints
    assert any(l.startswith("alt-a") and "⇄ alt-b" in l for l in lines)
    assert any(l.startswith("alt-b") and "⇄ alt-a" in l for l in lines)
    # composable_with symmetric
    assert any(l.startswith("comp-a") and "+ comp-b" in l for l in lines)
    assert any(l.startswith("comp-b") and "+ comp-a" in l for l in lines)
    # supersedes directed: only on src
    assert any(l.startswith("new") and "⇒ old" in l for l in lines)
    assert not any(l.startswith("old") and "⇒ new" in l for l in lines)


def test_text_suppresses_overlaps_when_richer_edge(git_repo):
    store = Store.init(git_repo)
    a = _cap(store, "aa"); b = _cap(store, "bb")
    store.add_edge(a, b, "overlaps"); store.add_edge(b, a, "overlaps")
    store.add_edge(a, b, "alternative_to"); store.add_edge(b, a, "alternative_to")
    out = to_text(store)
    assert "⇄ bb" in out and "⇄ aa" in out      # alternative_to shown
    assert "≈" not in out                          # baseline overlaps suppressed


def test_text_alternative_to_deduped_per_neighbor(git_repo):
    store = Store.init(git_repo)
    a = _cap(store, "aa"); b = _cap(store, "bb")
    store.add_edge(a, b, "alternative_to"); store.add_edge(b, a, "alternative_to")
    out = to_text(store)
    assert out.count("⇄ bb") == 1
    assert out.count("⇄ aa") == 1


def test_dot_renders_richer_same_region(git_repo):
    store = Store.init(git_repo)
    a = _cap(store, "a"); b = _cap(store, "b")
    c = _cap(store, "c"); d = _cap(store, "d")
    s = _cap(store, "s"); o = _cap(store, "o")
    store.add_edge(a, b, "alternative_to"); store.add_edge(b, a, "alternative_to")
    store.add_edge(c, d, "composable_with"); store.add_edge(d, c, "composable_with")
    store.add_edge(s, o, "supersedes")
    dot = to_dot(store)
    assert "color=orange style=dashed dir=none" in dot          # alternative_to
    assert 'label="alternative_to"' in dot
    assert "color=darkgreen style=dashed dir=none" in dot       # composable_with
    assert 'color=purple' in dot and 'label="supersedes"' in dot
    # symmetric types deduped to one drawn edge each
    assert dot.count('label="alternative_to"') == 1
    assert dot.count('label="composable_with"') == 1
    assert dot.count('label="supersedes"') == 1


def test_dot_suppresses_overlaps_when_richer_edge(git_repo):
    store = Store.init(git_repo)
    a = _cap(store, "a"); b = _cap(store, "b")
    store.add_edge(a, b, "overlaps"); store.add_edge(b, a, "overlaps")
    store.add_edge(a, b, "alternative_to"); store.add_edge(b, a, "alternative_to")
    dot = to_dot(store)
    assert 'label="alternative_to"' in dot
    assert 'label="overlaps"' not in dot


def test_mermaid_renders_richer_same_region(git_repo):
    store = Store.init(git_repo)
    a = _cap(store, "a"); b = _cap(store, "b")
    c = _cap(store, "c"); d = _cap(store, "d")
    s = _cap(store, "s"); o = _cap(store, "o")
    store.add_edge(a, b, "alternative_to"); store.add_edge(b, a, "alternative_to")
    store.add_edge(c, d, "composable_with"); store.add_edge(d, c, "composable_with")
    store.add_edge(s, o, "supersedes")
    m = to_mermaid(store)
    assert "---|alternative_to|" in m
    assert "---|composable_with|" in m
    assert "-->|supersedes|" in m
    assert m.count("---|alternative_to|") == 1     # symmetric deduped
    assert m.count("---|composable_with|") == 1
    assert m.count("-->|supersedes|") == 1


def test_mermaid_suppresses_overlaps_when_richer_edge(git_repo):
    store = Store.init(git_repo)
    a = _cap(store, "a"); b = _cap(store, "b")
    store.add_edge(a, b, "overlaps"); store.add_edge(b, a, "overlaps")
    store.add_edge(a, b, "alternative_to"); store.add_edge(b, a, "alternative_to")
    m = to_mermaid(store)
    assert "---|alternative_to|" in m
    assert "---|overlaps|" not in m
