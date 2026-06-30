from __future__ import annotations
import json
import re
from pathlib import Path
from typing import Optional

_LINE = re.compile(r"(?:RGIT_METRIC\s+)?([A-Za-z_]\w*)\s*[=:]\s*(\S+)")


def parse_metrics(stdout: str, run_dir: Path) -> Optional[dict]:
    """JSON file wins; otherwise scrape ``key=value`` / ``key: value`` pairs from
    stdout (an optional ``RGIT_METRIC`` prefix still works for explicit marking).

    Tolerant by design: a malformed metric line must never abort the caller
    (the experiment has already run). Only values that parse as a float are
    kept, so prose like ``device: cuda`` or ``ver=1.2.3`` is skipped, and an
    empty result from either source coalesces to None (the "no metrics" state).
    """
    f = Path(run_dir) / "rgit_metrics.json"
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8-sig")) or None
    found = {}
    for key, val in _LINE.findall(stdout):
        try:
            found[key] = float(val)
        except ValueError:
            continue            # skip garbage like "1.2.3" or "0.9abc"
    return found or None
