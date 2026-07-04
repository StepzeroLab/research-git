"""Background PyPI update check: cached, TTL-gated, silent on every failure.

State lives in ~/.rgit/update-check.json (user-level, distinct from a repo's
.rgit store). Nothing here may raise or block a CLI command: I/O errors read
as empty state and writes are best-effort atomic.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

TTL_SECONDS = 24 * 3600
ENV_FLAG = "RGIT_UPDATE_CHECK"
PYPI_URL = "https://pypi.org/pypi/research-git/json"


def state_path() -> Path:
    return Path.home() / ".rgit" / "update-check.json"


def load_state() -> dict:
    try:
        data = json.loads(state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_state(state: dict) -> None:
    path = state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(state), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass


def disabled() -> bool:
    if os.environ.get(ENV_FLAG, "").strip() == "0":
        return True
    return bool(load_state().get("disabled"))


def set_disabled(value: bool) -> None:
    state = load_state()
    state["disabled"] = value
    save_state(state)


def should_check(now: float) -> bool:
    if disabled():
        return False
    last = load_state().get("last_check")
    if not isinstance(last, (int, float)):
        return True
    return now - last > TTL_SECONDS
