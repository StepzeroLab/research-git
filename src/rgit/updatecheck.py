"""Background PyPI update check: cached, TTL-gated, silent on every failure.

State lives in ~/.rgit/update-check.json (user-level, distinct from a repo's
.rgit store). Nothing here may raise or block a CLI command: I/O errors read
as empty state and writes are best-effort atomic.
"""
from __future__ import annotations

import json
import os
import threading
import urllib.request
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


def _newer(latest: str, current: str) -> bool:
    """PEP 440 compare when `packaging` is importable, tolerant fallback else.

    Incomparable strings mean "no notice", never an exception.
    """
    try:
        from packaging.version import InvalidVersion, Version
        try:
            return Version(latest) > Version(current)
        except InvalidVersion:
            return False
    except ImportError:
        pass

    def parse(v: str) -> tuple | None:
        parts = []
        for tok in v.split("."):
            digits = "".join(ch for ch in tok if ch.isdigit())
            if not digits:
                return None
            parts.append(int(digits))
        return tuple(parts)

    a, b = parse(latest), parse(current)
    return a is not None and b is not None and a > b


def render_notice(current: str) -> str | None:
    """One-line upgrade notice from the *cached* check result, or None."""
    latest = load_state().get("latest_version")
    if not isinstance(latest, str) or not _newer(latest, current):
        return None
    return (f"research-git {latest} available (you have {current}) "
            f"— run `rgit update`")


def _fetch_once(now: float) -> None:
    try:
        with urllib.request.urlopen(PYPI_URL, timeout=2) as resp:
            latest = json.load(resp)["info"]["version"]
    except Exception:
        return
    if not isinstance(latest, str):
        return
    state = load_state()
    state.update({"last_check": now, "latest_version": latest})
    save_state(state)


def maybe_start_background_check(now: float) -> None:
    """Fire-and-forget PyPI check when the TTL has lapsed.

    `last_check` is stamped before the fetch so concurrent rgit processes do
    not all hit PyPI; a failed fetch simply waits out the next TTL.
    """
    if not should_check(now):
        return
    state = load_state()
    state["last_check"] = now
    save_state(state)
    threading.Thread(target=_fetch_once, args=(now,), daemon=True).start()


def hint_pending(path: str) -> bool:
    return path not in load_state().get("guidance_hints", [])


def mark_hint_shown(path: str) -> None:
    state = load_state()
    hints = state.setdefault("guidance_hints", [])
    if path not in hints:
        hints.append(path)
        save_state(state)
