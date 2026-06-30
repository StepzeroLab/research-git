# src/rgit/metricdir.py
from __future__ import annotations
import re
from typing import Optional

from .store.store import Store

_LOWER = re.compile(r"loss|err|nll|ppl|perplex", re.I)
_HIGHER = re.compile(r"acc|f1|reward|score|bleu|rouge", re.I)


def suggest(metric_names: list[str]) -> dict[str, str]:
    """Heuristic direction guess by metric name. Confident matches only.

    A name matching a 'lower-is-better' token (loss/err/nll/ppl/perplex) maps to
    'lower'; a 'higher-is-better' token (acc/f1/reward/score/bleu/rouge) maps to
    'higher'. Anything unrecognized is omitted so the caller never writes a guess
    it isn't sure about.
    """
    out: dict[str, str] = {}
    for name in metric_names:
        if _LOWER.search(name):
            out[name] = "lower"
        elif _HIGHER.search(name):
            out[name] = "higher"
    return out


def best_index(store: Store, metric: str, values: list[Optional[float]]) -> Optional[int]:
    """Index of the best value per the stored direction, or None.

    Returns None when the metric has no configured direction (never guess) or
    when every value is None. `values` is positional (aligned to the caller's
    rows); None entries are skipped.
    """
    direction = store.get_metric_direction(metric)
    if direction is None:
        return None
    present = [(i, v) for i, v in enumerate(values) if v is not None]
    if not present:
        return None
    pick = min if direction == "lower" else max
    return pick(present, key=lambda iv: iv[1])[0]
