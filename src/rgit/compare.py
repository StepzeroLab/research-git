# src/rgit/compare.py
from __future__ import annotations
from collections import Counter
from typing import Optional

from .store.store import Store


def _variant_cluster(store: Store, fid: str) -> list[str]:
    """Transitive closure over variant_of edges in BOTH directions."""
    seen, frontier = {fid}, [fid]
    while frontier:
        cur = frontier.pop()
        out = store.neighbors(cur, "variant_of")
        inc = [r["src"] for r in store.conn.execute(
            "SELECT src FROM edges WHERE dst=? AND type=?", (cur, "variant_of"))]
        for nxt in out + inc:
            if nxt not in seen:
                seen.add(nxt)
                frontier.append(nxt)
    return list(seen)


def _resolve_targets(store: Store, target: str) -> list[str]:
    """A target is `file:symbol`, a bare symbol, or a capsule name/id."""
    caps = store.list_features()
    by_id = {c.id: c for c in caps}
    by_name = {c.name: c for c in caps}
    if target in by_id:
        return _variant_cluster(store, target)
    if target in by_name:
        return _variant_cluster(store, by_name[target].id)
    # symbol target: "file:symbol" or bare "symbol"
    file, _, symbol = target.partition(":")
    want_file, want_sym = (file, symbol) if symbol else (None, target)
    hits = [c.id for c in caps for s in c.code_slices
            if s.symbol == want_sym and (want_file is None or s.file == want_file)]
    if not hits:
        raise KeyError(f"no capsule/symbol matching '{target}'")
    return hits


def _runs_of(store: Store, fid: str) -> list:
    return [store.get_run(rid) for rid in store.neighbors(fid, "produced")]


def _dominant_metric(runs: list) -> Optional[str]:
    names: Counter = Counter()
    for r in runs:
        for k in (r.metrics or {}):
            names[k] += 1
    return names.most_common(1)[0][0] if names else None


def compare(store: Store, target: str, metric: Optional[str] = None,
            direction: Optional[str] = None) -> dict:
    """Rank a variant cluster (or symbol's touchers) by a single metric.

    Returns {"metric", "rows": [{feature, run, value, delta, winner}]}. One metric
    is resolved up front (explicit `metric`, else the most common across the
    cluster's runs) and used for every row, so values and the Δ column are
    comparable. Each capsule is represented by its *best* run for that metric (per
    the direction), not just its first — so a later, better repeat experiment can
    still win. Δ is value minus the cluster's earliest-run value.

    Direction comes from the stored config; `direction` overrides it *for this
    call only* (it is not written back to the store). An unknown direction yields
    no winner (values still shown).
    """
    fids = _resolve_targets(store, target)
    caps = {c.id: c for c in store.list_features()}
    fids = sorted(fids, key=lambda i: caps[i].name)
    runs_by_fid = {fid: sorted(_runs_of(store, fid), key=lambda r: r.created_at)
                   for fid in fids}
    # Dedup by id: one run can be `produced` by two capsules in the cluster.
    all_runs = sorted({r.id: r for fid in fids for r in runs_by_fid[fid]}.values(),
                      key=lambda r: r.created_at)
    chosen_metric = metric or _dominant_metric(all_runs)
    resolved_dir = direction or (store.get_metric_direction(chosen_metric)
                                 if chosen_metric else None)

    baseline = next((r.metrics[chosen_metric] for r in all_runs
                     if chosen_metric and chosen_metric in (r.metrics or {})), None)
    rows, values = [], []
    for fid in fids:
        run = _best_run(runs_by_fid[fid], chosen_metric, resolved_dir)
        val = (run.metrics or {}).get(chosen_metric) if run and chosen_metric else None
        delta = round(val - baseline, 6) if (val is not None and baseline is not None) else None
        rows.append({"feature": caps[fid].name, "run": run.id if run else None,
                     "value": val, "delta": delta, "winner": False})
        values.append(val)
    win = _best_value_index(values, resolved_dir)
    if win is not None:
        rows[win]["winner"] = True
    return {"metric": chosen_metric, "rows": rows}


def _best_run(runs: list, metric: Optional[str], direction: Optional[str]):
    """The run with the best value of `metric` per `direction`.

    With a known direction, pick min (lower) / max (higher); otherwise fall back
    to the latest run (runs are sorted ascending by created_at) so a capsule with
    an unrankable metric still shows its most recent measurement.
    """
    if not metric:
        return None
    have = [r for r in runs if metric in (r.metrics or {})]
    if not have:
        return None
    if direction == "lower":
        return min(have, key=lambda r: r.metrics[metric])
    if direction == "higher":
        return max(have, key=lambda r: r.metrics[metric])
    return have[-1]


def _best_value_index(values: list, direction: Optional[str]) -> Optional[int]:
    """Index of the best value per `direction` (override-aware), or None."""
    if direction is None:
        return None
    present = [(i, v) for i, v in enumerate(values) if v is not None]
    if not present:
        return None
    pick = min if direction == "lower" else max
    return pick(present, key=lambda iv: iv[1])[0]
