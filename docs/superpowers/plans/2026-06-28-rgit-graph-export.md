# rgit graph export (text + DOT) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a read-only `rgit graph` command that prints the capsule graph as a plain-text variant-cluster tree (default) or Graphviz DOT (`--dot`), with optional run nodes (`--runs`).

**Architecture:** One query-only module `src/rgit/graphview.py` with `_collect` (gather nodes+edges once) feeding two pure renderers `to_text` and `to_dot`. The CLI `graph` branch opens the store via the existing guarded `Store.open()` and prints to stdout. Reuses `_variant_cluster` from `compare.py` for clustering. No writes, no new dependencies.

**Tech Stack:** Python 3.11+, argparse, pytest. Tests run via `.venv/bin/pytest`.

**Reference spec:** `docs/superpowers/specs/2026-06-28-rgit-graph-export-design.md`

**Grounding facts (verified):**
- `depends_on` edge is `src→dst` meaning **src depends on dst** (`edges.py` writes `store.neighbors(x, "depends_on")` as outgoing). Render `→needs <dst-name>` on the src.
- `conflicts_with` is written symmetrically (both `a→b` and `b→a` exist). Text shows `⚔` on both endpoints naturally; DOT must **dedupe** to one `dir=none` edge per unordered pair.
- `compare._variant_cluster(store, fid) -> list[str]` returns the transitive `variant_of` closure (both directions); reuse it.
- `store.neighbors(x, "variant_of")` returns the **outgoing** targets: `x -variant_of-> parent` (x is a variant of parent). So a cluster **root** = a member with no outgoing `variant_of` to another member.

---

## File Structure

- `src/rgit/graphview.py` — NEW. `_collect`, `to_text`, `to_dot`, plus private helpers (`_esc`, `_fmt_metrics`, `_markers`, cluster/tree rendering).
- `src/rgit/cli.py` — add the `graph` subparser + dispatch branch.
- `tests/test_graphview.py` — NEW. Unit tests for `_collect`, `to_text`, `to_dot`.
- `tests/test_cli.py` — add CLI tests for `rgit graph` / `--dot` / `--runs` / missing-store.

---

## Task 1: `_collect` — gather nodes and edges once

**Files:**
- Create: `src/rgit/graphview.py`
- Test: `tests/test_graphview.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_graphview.py`:

```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/test_graphview.py -v`
Expected: FAIL (ModuleNotFoundError: rgit.graphview).

- [ ] **Step 3: Create `src/rgit/graphview.py` with `_collect`**

```python
# src/rgit/graphview.py
from __future__ import annotations

from .store.store import Store
from .compare import _variant_cluster

_CAP_EDGE_TYPES = ("variant_of", "conflicts_with", "depends_on")
_RUN_EDGE_TYPES = ("produced", "active")


def _collect(store: Store, include_runs: bool) -> dict:
    """Gather the graph once: capsule nodes, optional run nodes, typed edges.

    edges is a list of (src, dst, type). Capsule-edges are kept only when both
    endpoints are existing capsules. When include_runs, run nodes reachable by
    produced/active edges are added along with those edges.
    """
    caps = store.list_features()
    cap_ids = {c.id for c in caps}
    edges = [(r["src"], r["dst"], r["type"]) for r in store.conn.execute(
        "SELECT src, dst, type FROM edges WHERE type IN (?,?,?)", _CAP_EDGE_TYPES)]
    edges = [e for e in edges if e[0] in cap_ids and e[1] in cap_ids]
    runs = []
    if include_runs:
        run_edges = [(r["src"], r["dst"], r["type"]) for r in store.conn.execute(
            "SELECT src, dst, type FROM edges WHERE type IN (?,?)", _RUN_EDGE_TYPES)]
        # produced: capsule(src) -> run(dst); active: run(src) -> capsule(dst)
        run_edges = [(s, d, t) for (s, d, t) in run_edges
                     if (s in cap_ids if t == "produced" else d in cap_ids)]
        run_ids, kept = set(), []
        for s, d, t in run_edges:
            run_ids.add(d if t == "produced" else s)
            kept.append((s, d, t))
        runs = [store.get_run(rid) for rid in sorted(run_ids)]
        edges = edges + kept
    return {"capsules": caps, "edges": edges, "runs": runs}
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/pytest tests/test_graphview.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/rgit/graphview.py tests/test_graphview.py
git commit -m "feat(graphview): _collect gathers capsule/run nodes and typed edges"
```

---

## Task 2: `to_text` — variant-cluster tree (default view)

**Files:**
- Modify: `src/rgit/graphview.py`
- Test: `tests/test_graphview.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_graphview.py`:

```python
from rgit.graphview import to_text


def test_text_renders_variant_tree_with_markers(git_repo):
    store = Store.init(git_repo)
    a = _cap(store, "temp-0.7"); b = _cap(store, "temp-1.0"); c = _cap(store, "temp-1.3")
    e = _cap(store, "entropy"); tok = _cap(store, "tokenizer")
    store.add_edge(b, a, "variant_of")          # temp-1.0 is a variant of temp-0.7
    store.add_edge(c, b, "variant_of")          # temp-1.3 variant of temp-1.0
    store.add_edge(b, e, "conflicts_with"); store.add_edge(e, b, "conflicts_with")
    store.add_edge(e, tok, "depends_on")        # entropy depends on tokenizer
    out = to_text(store, include_runs=False)
    lines = out.splitlines()
    # root has no connector; children are indented under it
    assert any(l == "temp-0.7" for l in lines)
    assert any(l.lstrip().startswith("└─ temp-1.0") for l in lines)
    assert any("temp-1.3" in l and l.startswith("   ") for l in lines)
    # conflict marker shows on both endpoints, depends marker on the dependent
    assert any("temp-1.0" in l and "⚔ entropy" in l for l in lines)
    assert any(l.startswith("entropy") and "⚔ temp-1.0" in l and "→needs tokenizer" in l
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
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/test_graphview.py -k text -v`
Expected: FAIL (ImportError: cannot import name `to_text`).

- [ ] **Step 3: Add `to_text` and helpers to `src/rgit/graphview.py`**

```python
def _fmt_metrics(metrics) -> str:
    if not metrics:
        return ""
    return "{" + ", ".join(f"{k}: {v}" for k, v in metrics.items()) + "}"


def _runs_by_capsule(edges, runs) -> dict:
    run_by_id = {r.id: r for r in runs}
    out: dict = {}
    for s, d, t in edges:
        if t == "produced":          # s=capsule, d=run
            out.setdefault(s, []).append(run_by_id[d])
        elif t == "active":          # s=run, d=capsule
            out.setdefault(d, []).append(run_by_id[s])
    return out


def _markers(cid, edges, by_id) -> str:
    confl = sorted({(d if s == cid else s)
                    for s, d, t in edges if t == "conflicts_with" and cid in (s, d)})
    deps = sorted({d for s, d, t in edges if t == "depends_on" and s == cid})
    parts = [f"⚔ {by_id[x].name}" for x in confl if x in by_id]
    parts += [f"→needs {by_id[x].name}" for x in deps if x in by_id]
    return "  ".join(parts)


def to_text(store: Store, *, include_runs: bool = False) -> str:
    g = _collect(store, include_runs)
    caps = g["capsules"]
    if not caps:
        return "(no capsules)"
    by_id = {c.id: c for c in caps}
    edges = g["edges"]
    runs_by_cap = _runs_by_capsule(edges, g["runs"]) if include_runs else {}

    # children[parent] = capsules that are variant_of parent (within the graph)
    children: dict = {c.id: [] for c in caps}
    for s, d, t in edges:
        if t == "variant_of" and s in by_id and d in by_id:
            children[d].append(s)

    lines: list[str] = []
    seen: set = set()

    def emit(cid: str, depth: int) -> None:
        if cid in seen:
            return
        seen.add(cid)
        prefix = ("   " * (depth - 1) + "└─ ") if depth else ""
        node = f"{prefix}{by_id[cid].name}"
        mk = _markers(cid, edges, by_id)
        lines.append(f"{node:<20}{mk}".rstrip() if mk else node)
        if include_runs:
            for r in runs_by_cap.get(cid, []):
                lines.append("   " * depth + f"   • {r.id}  {_fmt_metrics(r.metrics)}".rstrip())
        for ch in sorted(children[cid], key=lambda i: by_id[i].name):
            emit(ch, depth + 1)

    # cluster by variant closure; within each cluster, roots first
    clustered: set = set()
    for c in sorted(caps, key=lambda c: c.name):
        if c.id in clustered:
            continue
        members = [m for m in _variant_cluster(store, c.id) if m in by_id]
        clustered.update(members)
        mset = set(members)
        roots = [m for m in members
                 if not any(p in mset for p in store.neighbors(m, "variant_of"))]
        for root in sorted(roots, key=lambda i: by_id[i].name):
            emit(root, 0)
        # cycle safety: any unreached member becomes its own root
        for m in sorted(members, key=lambda i: by_id[i].name):
            emit(m, 0)
    return "\n".join(lines)
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/pytest tests/test_graphview.py -v`
Expected: PASS (5 tests total).

- [ ] **Step 5: Commit**

```bash
git add src/rgit/graphview.py tests/test_graphview.py
git commit -m "feat(graphview): to_text variant-cluster tree with conflict/depends markers"
```

---

## Task 3: `to_dot` — Graphviz DOT view

**Files:**
- Modify: `src/rgit/graphview.py`
- Test: `tests/test_graphview.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_graphview.py`:

```python
from rgit.graphview import to_dot


def test_dot_nodes_and_edge_styles(git_repo):
    store = Store.init(git_repo)
    a = _cap(store, "a"); b = _cap(store, "b"); e = _cap(store, "e")
    store.add_edge(b, a, "variant_of")
    store.add_edge(a, e, "conflicts_with"); store.add_edge(e, a, "conflicts_with")
    store.add_edge(a, e, "depends_on")
    dot = to_dot(store)
    assert dot.startswith("digraph rgit {")
    assert dot.rstrip().endswith("}")
    assert "shape=box" in dot
    assert 'label="variant_of"' in dot
    assert "color=red style=dashed dir=none" in dot      # conflicts_with
    assert "color=blue" in dot                            # depends_on
    # symmetric conflicts_with collapses to ONE drawn edge
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
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/test_graphview.py -k dot -v`
Expected: FAIL (ImportError: cannot import name `to_dot`).

- [ ] **Step 3: Add `to_dot` and `_esc` to `src/rgit/graphview.py`**

```python
def _esc(s: str) -> str:
    """Escape double-quotes for a DOT quoted string (ids/labels stay valid)."""
    return str(s).replace('"', '\\"')


_EDGE_STYLE = {
    "variant_of":     'color=black label="variant_of"',
    "depends_on":     'color=blue label="depends_on"',
    "conflicts_with": 'color=red style=dashed dir=none label="conflicts_with"',
    "produced":       'color=gray style=dotted label="produced"',
    "active":         'color=green style=dashed label="active"',
}


def to_dot(store: Store, *, include_runs: bool = False) -> str:
    g = _collect(store, include_runs)
    lines = ["digraph rgit {", "  rankdir=LR;"]
    for c in sorted(g["capsules"], key=lambda c: c.name):
        lines.append(f'  "{_esc(c.id)}" [shape=box style=rounded label="{_esc(c.name)}"];')
    if include_runs:
        for r in sorted(g["runs"], key=lambda r: r.id):
            label = f"{r.id}\\n{_fmt_metrics(r.metrics)}"
            lines.append(f'  "{_esc(r.id)}" [shape=ellipse label="{_esc(label)}"];')
    seen_conflict: set = set()
    for s, d, t in g["edges"]:
        if t == "conflicts_with":
            key = frozenset((s, d))
            if key in seen_conflict:
                continue
            seen_conflict.add(key)
        style = _EDGE_STYLE[t]
        lines.append(f'  "{_esc(s)}" -> "{_esc(d)}" [{style}];')
    lines.append("}")
    return "\n".join(lines)
```

Note: `_esc` only escapes `"`; the run label's `\\n` (a literal backslash-n for a DOT line break) is intentionally left intact.

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/pytest tests/test_graphview.py -v`
Expected: PASS (8 tests total).

- [ ] **Step 5: Commit**

```bash
git add src/rgit/graphview.py tests/test_graphview.py
git commit -m "feat(graphview): to_dot with edge-type styling and conflict dedupe"
```

---

## Task 4: `rgit graph` CLI subcommand

**Files:**
- Modify: `src/rgit/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli.py`:

```python
def test_graph_text_default(git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    cli.main(["init"])
    store = Store.open(git_repo)
    from rgit.store.models import Capsule, CodeSlice
    def cap(name):
        return store.add_feature(Capsule(
            id="", name=name, intent="i", status="approved", base_commit="abc",
            knobs={}, data_assumptions=None, resurrection_guide=None,
            result_summary=None, payload_hash=None,
            code_slices=[CodeSlice("m.py", "f", None, "x", "wrap")]))
    a = cap("temp-0.7"); b = cap("temp-1.0")
    store.add_edge(b, a, "variant_of")
    assert cli.main(["graph"]) == 0
    out = capsys.readouterr().out
    assert "temp-0.7" in out and "└─ temp-1.0" in out


def test_graph_dot_flag(git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    cli.main(["init"])
    assert cli.main(["graph", "--dot"]) == 0
    assert "digraph rgit {" in capsys.readouterr().out


def test_graph_without_store_is_clean_error(git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)                   # git repo, no `rgit init`
    assert cli.main(["graph"]) == 1
    assert "no .rgit/" in capsys.readouterr().out
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/test_cli.py -k graph -v`
Expected: FAIL (argparse: invalid choice `graph`).

- [ ] **Step 3a: Add the `graph` subparser in `src/rgit/cli.py`**

Add right after the `metric-dir` parser block (the `p_md` / `md_sub` block), before `args = parser.parse_args(argv)`:

```python
    p_graph = sub.add_parser("graph")          # render the graph (read-only)
    p_graph.add_argument("--dot", action="store_true", help="emit Graphviz DOT")
    p_graph.add_argument("--runs", action="store_true",
                         help="include run nodes + produced/active edges")
```

- [ ] **Step 3b: Add the dispatch branch in `src/rgit/cli.py`**

Add alongside the other store-backed branches (e.g. right after the `metric-dir` branch, before the final `return 1`):

```python
    if args.cmd == "graph":
        from . import graphview
        render = graphview.to_dot if args.dot else graphview.to_text
        print(render(store, include_runs=args.runs))
        return 0
```

(This is reached after the guarded `store = Store.open()`, so a missing `.rgit/` already yields the clean error + exit 1.)

- [ ] **Step 4: Run to verify pass + full suite**

Run: `.venv/bin/pytest tests/test_cli.py -k graph -v` → PASS (3 tests).
Run: `.venv/bin/pytest -q` → full suite green.

- [ ] **Step 5: Commit**

```bash
git add src/rgit/cli.py tests/test_cli.py
git commit -m "feat(cli): rgit graph prints text (default) or DOT (--dot), --runs"
```

---

## Self-Review Notes

- **Spec coverage:** §1 architecture → Tasks 1–3 (`_collect`/`to_text`/`to_dot`); §2 CLI → Task 4; §3 text view (clusters, roots, markers, runs nesting, empty) → Task 2; §4 DOT view (shapes, edge styles, conflict dedupe, escaping, empty) → Task 3; §5 error handling → Task 4 (reuses the guard) + empty-graph tests in 2/3.
- **Type/name consistency:** `_collect(store, include_runs) -> dict{capsules, edges, runs}` defined in Task 1 and consumed unchanged in Tasks 2–3. `_fmt_metrics` introduced in Task 2 and reused in Task 3. `to_text`/`to_dot` signatures `(store, *, include_runs=False)` match the CLI dispatch in Task 4.
- **Reuse:** `_variant_cluster` imported from `compare.py` (not reimplemented).
- **Known follow-up (out of scope):** add a `### graph` blurb to README under the research-layer section once the command lands; not gated by this plan.
