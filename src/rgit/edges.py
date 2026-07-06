from __future__ import annotations
import re
from typing import Optional

from .store.store import Store

_IDENT = re.compile(r"[A-Za-z_]\w*")


def _approved(store: Store):
    return [c for c in store.list_features() if c.status == "approved"]


def _used_names(code: str) -> set[str]:
    """Identifiers referenced in slice code. Tolerant of diff markers: a leading
    +/- (but not the +++/--- file headers) is stripped before scanning."""
    names: set[str] = set()
    for line in (code or "").splitlines():
        if line[:1] in "+-" and not line.startswith(("+++", "---")):
            line = line[1:]
        names.update(_IDENT.findall(line))
    return names


def _top_symbol(symbol: str) -> str:
    """Top-level container of a (possibly dotted) symbol, for overlap matching.

    The deterministic libcst mapping records a top-level def/class name
    (`CustomCrossEntropyLoss`), while an agent-written capsule may use a dotted
    member (`CustomCrossEntropyLoss.__call__`). Both touch the same region, so we
    compare on the first dotted component to avoid missing real overlaps.
    """
    return symbol.split(".", 1)[0]


def overlap_pairs(store: Store,
                  scope: Optional[set] = None) -> list[tuple[str, str]]:
    """Unordered capsule pairs sharing a (file, top-level symbol). Deterministic.
    `scope` keeps only pairs touching at least one of the given capsule ids —
    the incremental path after a digest batch (new x graph, never old x old)."""
    caps = _approved(store)
    keys = {c.id: {(s.file, _top_symbol(s.symbol)) for s in c.code_slices if s.symbol}
            for c in caps}
    pairs = []
    for i in range(len(caps)):
        for j in range(i + 1, len(caps)):
            if scope is not None and caps[i].id not in scope \
                    and caps[j].id not in scope:
                continue
            if keys[caps[i].id] & keys[caps[j].id]:
                pairs.append((caps[i].id, caps[j].id))
    return pairs


def apply_overlaps(store: Store, scope: Optional[set] = None) -> int:
    """Write overlaps for each same-region pair, symmetric. Idempotent. Returns
    the number of overlapping pairs."""
    pairs = overlap_pairs(store, scope)
    for a, b in pairs:
        store.add_edge(a, b, "overlaps")
        store.add_edge(b, a, "overlaps")
    return len(pairs)


def depends_candidates(store: Store, scope: Optional[set] = None,
                       limit: Optional[int] = None) -> list[dict]:
    """Emit depends_on CANDIDATES (writes nothing). X is a candidate to depend_on
    Y when a name used in X's slice code intersects the symbols Y defines. Skips
    pairs that already carry a depends_on edge. `scope` filters to pairs touching
    the given ids; `limit` is the edge-judge quota — strongest evidence first
    (shared-identifier count), deterministic tie-break, the rest stay unjudged."""
    caps = _approved(store)
    defines = {c.id: {s.symbol for s in c.code_slices if s.symbol} for c in caps}
    uses = {c.id: set().union(*[_used_names(s.code) for s in c.code_slices])
            if c.code_slices else set() for c in caps}
    out = []
    for x in caps:
        existing = set(store.neighbors(x.id, "depends_on"))
        for y in caps:
            if x.id == y.id or y.id in existing:
                continue
            if scope is not None and x.id not in scope and y.id not in scope:
                continue
            shared = uses[x.id] & defines[y.id]
            if shared:
                out.append({"src": x.id, "dst": y.id, "evidence": sorted(shared)})
    if limit is not None:
        out.sort(key=lambda c: (-len(c["evidence"]), c["src"], c["dst"]))
        out = out[:limit]
    return out
