# src/rgit/ablation.py
from __future__ import annotations
from itertools import chain, combinations

from .metricdir import best_index
from .store.store import Store


def _powerset(items: list[str]):
    return chain.from_iterable(combinations(items, r) for r in range(len(items) + 1))


def _active_set(store: Store, run_id: str) -> frozenset[str]:
    """A run's active capsules; fall back to produced capsules when none declared."""
    active = store.active_features(run_id)
    if active:
        return frozenset(active)
    produced = [r["src"] for r in store.conn.execute(
        "SELECT src FROM edges WHERE dst=? AND type=?", (run_id, "produced"))]
    return frozenset(produced)


def ablation(store: Store, capsule_ids: list[str], metric: str | None = None) -> dict:
    """Build a base/+A/+A+B grid over the powerset of `capsule_ids`.

    `capsule_ids` accepts capsule ids or names (resolved to ids). Each subset cell
    is the latest run whose active set equals that subset *exactly* — a run that
    also had some capsule active outside the requested sweep is dropped, not
    folded into a smaller cell, so cells never compare confounded measurements.
    Returns {"rows": [{subset(names), run, cells{metric: value}}], "winners": {metric: subset}}.
    """
    capsule_ids = [store.resolve_feature(t) for t in capsule_ids]   # names -> ids
    caps = {c.id: c for c in store.list_features()}
    name = {cid: caps[cid].name for cid in capsule_ids}
    target = frozenset(capsule_ids)

    # All runs, newest first, bucketed by their EXACT active set (subsets of the
    # sweep only; a run with an extra active capsule outside `target` is skipped).
    rows_all = store.conn.execute("SELECT id FROM runs").fetchall()
    runs = sorted((store.get_run(r["id"]) for r in rows_all),
                  key=lambda r: r.created_at, reverse=True)
    latest_for: dict[frozenset, object] = {}
    for run in runs:
        aset = _active_set(store, run.id)
        if not aset <= target:                 # confounded by a feature outside the sweep
            continue
        latest_for.setdefault(aset, run)       # newest wins (we iterate desc)

    metric_names: list[str] = []
    rows = []
    for subset in _powerset(capsule_ids):
        run = latest_for.get(frozenset(subset))
        cells = dict(run.metrics) if (run and run.metrics) else {}
        for k in cells:
            if k not in metric_names:
                metric_names.append(k)
        rows.append({"subset": tuple(sorted(name[c] for c in subset)),
                     "run": run.id if run else None, "cells": cells})

    cols = [metric] if metric else metric_names
    for row in rows:                            # ensure every row has every column
        for m in cols:
            row["cells"].setdefault(m, None)

    winners: dict[str, tuple] = {}
    for m in cols:
        idx = best_index(store, m, [row["cells"].get(m) for row in rows])
        if idx is not None:
            winners[m] = rows[idx]["subset"]
    return {"rows": rows, "winners": winners}
