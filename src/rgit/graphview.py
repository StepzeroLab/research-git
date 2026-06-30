# src/rgit/graphview.py
from __future__ import annotations

from .store.store import Store
from .compare import _variant_cluster

_CAP_EDGE_TYPES = ("variant_of", "depends_on", "overlaps",
                   "alternative_to", "composable_with", "supersedes", "conflicts_with")
_RUN_EDGE_TYPES = ("produced", "active")

# same-region relationship types; overlaps is the deterministic baseline, the
# rest are richer relationships an agent assigns to upgrade an overlaps pair.
_RICHER_SAME_REGION = ("alternative_to", "composable_with", "supersedes", "conflicts_with")
_SYMMETRIC_EDGES = ("overlaps", "alternative_to", "composable_with", "conflicts_with")


def _suppressed_overlap_pairs(edges) -> set:
    """Unordered pairs that have a richer same-region edge — their baseline
    `overlaps` is hidden so we don't draw both."""
    return {frozenset((s, d)) for s, d, t in edges if t in _RICHER_SAME_REGION}


def _collect(store: Store, include_runs: bool) -> dict:
    """Gather the graph once: capsule nodes, optional run nodes, typed edges.

    edges is a list of (src, dst, type). Capsule-edges are kept only when both
    endpoints are existing capsules. When include_runs, run nodes reachable by
    produced/active edges are added along with those edges.
    """
    caps = store.list_features()
    cap_ids = {c.id for c in caps}
    ph = ",".join("?" * len(_CAP_EDGE_TYPES))
    edges = [(r["src"], r["dst"], r["type"]) for r in store.conn.execute(
        f"SELECT src, dst, type FROM edges WHERE type IN ({ph})", _CAP_EDGE_TYPES)]
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
    # de-dup per capsule: one capsule may both produce AND activate the same run
    return {cid: list({r.id: r for r in rs}.values()) for cid, rs in out.items()}


def _markers(cid, edges, by_id, suppressed) -> str:
    sym = {"overlaps": "≈", "alternative_to": "⇄",
           "composable_with": "+", "conflicts_with": "⚔"}
    labels: set = set()
    for s, d, t in edges:
        glyph = sym.get(t)
        if glyph is None or cid not in (s, d):
            continue
        if t == "overlaps" and frozenset((s, d)) in suppressed:
            continue
        other = d if s == cid else s
        if other in by_id:
            labels.add(f"{glyph} {by_id[other].name}")
    # supersedes is directed: show ⇒ on the src only
    sup = sorted({d for s, d, t in edges if t == "supersedes" and s == cid})
    deps = sorted({d for s, d, t in edges if t == "depends_on" and s == cid})
    extra = [f"⇒ {by_id[x].name}" for x in sup if x in by_id]
    extra += [f"→needs {by_id[x].name}" for x in deps if x in by_id]
    return "  ".join(sorted(labels) + extra)


def to_text(store: Store, *, include_runs: bool = False) -> str:
    g = _collect(store, include_runs)
    caps = g["capsules"]
    if not caps:
        return "(no capsules)"
    by_id = {c.id: c for c in caps}
    edges = g["edges"]
    suppressed = _suppressed_overlap_pairs(edges)
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
        mk = _markers(cid, edges, by_id, suppressed)
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


def _esc(s: str) -> str:
    """Escape backslashes then double-quotes for a DOT quoted string.

    Backslash first, so a name ending in `\\` can't escape the closing quote and
    break the digraph — the deterministic 'arbitrary names stay valid' guarantee.
    """
    return str(s).replace("\\", "\\\\").replace('"', '\\"')


_EDGE_STYLE = {
    "variant_of":      'color=black label="variant_of"',
    "depends_on":      'color=blue label="depends_on"',
    "supersedes":      'color=purple label="supersedes"',
    "overlaps":        'color=gray style=dashed dir=none label="overlaps"',
    "alternative_to":  'color=orange style=dashed dir=none label="alternative_to"',
    "composable_with": 'color=darkgreen style=dashed dir=none label="composable_with"',
    "conflicts_with":  'color=red style=dashed dir=none label="conflicts_with"',
    "produced":        'color=gray style=dotted label="produced"',
    "active":          'color=green style=dashed label="active"',
}


def to_dot(store: Store, *, include_runs: bool = False) -> str:
    g = _collect(store, include_runs)
    lines = ["digraph rgit {", "  rankdir=LR;"]
    for c in sorted(g["capsules"], key=lambda c: c.name):
        lines.append(f'  "{_esc(c.id)}" [shape=box style=rounded label="{_esc(c.name)}"];')
    if include_runs:
        for r in sorted(g["runs"], key=lambda r: r.id):
            # assemble from already-escaped parts so the literal \n separator
            # (a DOT line break) survives — escaping the whole label would kill it
            label = f"{_esc(r.id)}\\n{_esc(_fmt_metrics(r.metrics))}"
            lines.append(f'  "{_esc(r.id)}" [shape=ellipse label="{label}"];')
    suppressed = _suppressed_overlap_pairs(g["edges"])
    seen_pair: set = set()
    for s, d, t in g["edges"]:
        if t == "overlaps" and frozenset((s, d)) in suppressed:
            continue
        if t in _SYMMETRIC_EDGES:
            key = (t, frozenset((s, d)))
            if key in seen_pair:
                continue
            seen_pair.add(key)
        style = _EDGE_STYLE[t]
        lines.append(f'  "{_esc(s)}" -> "{_esc(d)}" [{style}];')
    lines.append("}")
    return "\n".join(lines)


def _mesc(s: str) -> str:
    """Escape double-quotes for a Mermaid quoted label (HTML #quot; entity)."""
    return str(s).replace('"', "#quot;")


# (link operator, edge label): overlaps is an open link (no arrowhead).
_MERMAID_EDGE = {
    "variant_of":      ("-->", "variant_of"),
    "depends_on":      ("-->", "depends_on"),
    "supersedes":      ("-->", "supersedes"),
    "overlaps":        ("---", "overlaps"),
    "alternative_to":  ("---", "alternative_to"),
    "composable_with": ("---", "composable_with"),
    "conflicts_with":  ("---", "conflicts_with"),
    "produced":        ("-->", "produced"),
    "active":          ("-->", "active"),
}


def to_mermaid(store: Store, *, include_runs: bool = False) -> str:
    """Mermaid `graph LR` flowchart from the same collected graph.

    Generated deterministically from code (never an LLM), so the syntax is
    always valid. Node ids are the capsule/run ids (safe Mermaid identifiers);
    labels are quoted with `"` escaped to `#quot;` so arbitrary names can't break
    the chart. Capsule = rectangle, run = stadium; symmetric overlaps deduped.
    """
    g = _collect(store, include_runs)
    lines = ["graph LR"]
    for c in sorted(g["capsules"], key=lambda c: c.name):
        lines.append(f'  {c.id}["{_mesc(c.name)}"]')
    if include_runs:
        for r in sorted(g["runs"], key=lambda r: r.id):
            label = f"{r.id}<br>{_mesc(_fmt_metrics(r.metrics))}"
            lines.append(f'  {r.id}(["{label}"])')
    suppressed = _suppressed_overlap_pairs(g["edges"])
    seen_pair: set = set()
    for s, d, t in g["edges"]:
        if t == "overlaps" and frozenset((s, d)) in suppressed:
            continue
        if t in _SYMMETRIC_EDGES:
            key = (t, frozenset((s, d)))
            if key in seen_pair:
                continue
            seen_pair.add(key)
        link, label = _MERMAID_EDGE[t]
        lines.append(f"  {s} {link}|{label}| {d}")
    return "\n".join(lines)
