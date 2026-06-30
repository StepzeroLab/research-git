from __future__ import annotations
import re

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


def overlap_pairs(store: Store) -> list[tuple[str, str]]:
    """Unordered capsule pairs sharing a (file, top-level symbol). Deterministic."""
    caps = _approved(store)
    keys = {c.id: {(s.file, _top_symbol(s.symbol)) for s in c.code_slices if s.symbol}
            for c in caps}
    pairs = []
    for i in range(len(caps)):
        for j in range(i + 1, len(caps)):
            if keys[caps[i].id] & keys[caps[j].id]:
                pairs.append((caps[i].id, caps[j].id))
    return pairs


def apply_overlaps(store: Store) -> int:
    """Write overlaps for each same-region pair, symmetric. Idempotent. Returns
    the number of overlapping pairs."""
    pairs = overlap_pairs(store)
    for a, b in pairs:
        store.add_edge(a, b, "overlaps")
        store.add_edge(b, a, "overlaps")
    return len(pairs)


def depends_candidates(store: Store) -> list[dict]:
    """Emit depends_on CANDIDATES (writes nothing). X is a candidate to depend_on
    Y when a name used in X's slice code intersects the symbols Y defines. Skips
    pairs that already carry a depends_on edge."""
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
            shared = uses[x.id] & defines[y.id]
            if shared:
                out.append({"src": x.id, "dst": y.id, "evidence": sorted(shared)})
    return out
