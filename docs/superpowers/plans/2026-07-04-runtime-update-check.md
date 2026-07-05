# Runtime Update Check + `rgit update` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Notify users of new PyPI releases (background, cached, opt-out) and give them one command — `rgit update` — that upgrades the package and refreshes every installed platform surface, without ever clobbering user-customized guidance blocks.

**Architecture:** A new `updatecheck` module owns the `~/.rgit/update-check.json` state (TTL gate, disable flag, cached latest version, one-time hint ledger) and a daemon-thread PyPI fetch. A new `selfupdate` module delegates the upgrade to uv-tool/pipx/pip (detected from `sys.prefix`) and refreshes platforms in fresh subprocesses. `agent_guidance` gains a content fingerprint in the START marker plus historical hashes so the update path can tell pristine blocks (replace) from customized/removed/broken ones (skip + hint).

**Tech Stack:** Python 3.11 stdlib only (urllib, threading, hashlib, subprocess). `packaging.version` used opportunistically if importable, never required.

**Spec:** `docs/superpowers/specs/2026-07-04-runtime-update-check-design.md`

## Global Constraints

- Python >= 3.11; no new runtime dependencies in pyproject.
- Update check must never block, never raise, never print errors: every failure degrades to silence.
- Notice only when stdout is a TTY, `--json` not passed, command is not `mcp`/`update`.
- User-facing command surface is exactly: `rgit update`, `rgit update --off`, `rgit update --on`. Env `RGIT_UPDATE_CHECK=0` disables non-persistently. No other new visible flags (`--from-update` on install is hidden plumbing).
- Notice text (exact): ``research-git {latest} available (you have {current}) — run `rgit update` ``
- Explicit `rgit install` keeps today's replace/append guidance semantics; only the `--from-update` path is conservative.
- Historical canonical hashes (12-hex sha256, precomputed from git tags): `9e20fa27047f` (v0.0.1–v0.0.3), `c7d73fc2ba60` (v0.0.4).
- Tests run with `.venv/bin/python -m pytest` (pip is sandbox-blocked; use uv if the venv needs a refresh: `uv pip install -e ".[dev]"`).
- Windows compatibility: no POSIX-only APIs; state writes atomic via tmp + `Path.replace`.

---

### Task 1: `updatecheck` state, TTL gate, disable switches

**Files:**
- Create: `src/rgit/updatecheck.py`
- Test: `tests/test_updatecheck.py`

**Interfaces:**
- Produces: `state_path() -> Path`, `load_state() -> dict`, `save_state(dict) -> None`, `disabled() -> bool`, `set_disabled(bool) -> None`, `should_check(now: float) -> bool`, `TTL_SECONDS = 86400`, `ENV_FLAG = "RGIT_UPDATE_CHECK"`. Tests monkeypatch `updatecheck.state_path`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_updatecheck.py
import json

from rgit import updatecheck


def _use_tmp_state(monkeypatch, tmp_path):
    monkeypatch.setattr(updatecheck, "state_path",
                        lambda: tmp_path / "update-check.json")


def test_load_state_missing_file_is_empty(monkeypatch, tmp_path):
    _use_tmp_state(monkeypatch, tmp_path)
    assert updatecheck.load_state() == {}


def test_load_state_corrupted_json_self_heals(monkeypatch, tmp_path):
    _use_tmp_state(monkeypatch, tmp_path)
    (tmp_path / "update-check.json").write_text("{not json", encoding="utf-8")
    assert updatecheck.load_state() == {}
    updatecheck.save_state({"disabled": True})          # rewrite works
    assert updatecheck.load_state() == {"disabled": True}


def test_save_creates_parent_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(updatecheck, "state_path",
                        lambda: tmp_path / "deep" / "update-check.json")
    updatecheck.save_state({"last_check": 5})
    assert updatecheck.load_state() == {"last_check": 5}


def test_disabled_via_env(monkeypatch, tmp_path):
    _use_tmp_state(monkeypatch, tmp_path)
    monkeypatch.setenv(updatecheck.ENV_FLAG, "0")
    assert updatecheck.disabled() is True


def test_disabled_via_state_flag(monkeypatch, tmp_path):
    _use_tmp_state(monkeypatch, tmp_path)
    monkeypatch.delenv(updatecheck.ENV_FLAG, raising=False)
    updatecheck.set_disabled(True)
    assert updatecheck.disabled() is True
    updatecheck.set_disabled(False)
    assert updatecheck.disabled() is False


def test_should_check_ttl(monkeypatch, tmp_path):
    _use_tmp_state(monkeypatch, tmp_path)
    monkeypatch.delenv(updatecheck.ENV_FLAG, raising=False)
    assert updatecheck.should_check(now=1000.0) is True          # never checked
    updatecheck.save_state({"last_check": 1000.0})
    assert updatecheck.should_check(now=1000.0 + 3600) is False  # inside TTL
    assert updatecheck.should_check(
        now=1000.0 + updatecheck.TTL_SECONDS + 1) is True        # expired


def test_should_check_false_when_disabled(monkeypatch, tmp_path):
    _use_tmp_state(monkeypatch, tmp_path)
    monkeypatch.setenv(updatecheck.ENV_FLAG, "0")
    assert updatecheck.should_check(now=1e12) is False


def test_should_check_tolerates_garbage_last_check(monkeypatch, tmp_path):
    _use_tmp_state(monkeypatch, tmp_path)
    monkeypatch.delenv(updatecheck.ENV_FLAG, raising=False)
    updatecheck.save_state({"last_check": "yesterday"})
    assert updatecheck.should_check(now=0.0) is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_updatecheck.py -q`
Expected: FAIL/ERROR with `ModuleNotFoundError: No module named 'rgit.updatecheck'` (or ImportError).

- [ ] **Step 3: Write the implementation**

```python
# src/rgit/updatecheck.py
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
    last = load_state().get("last_check", 0)
    if not isinstance(last, (int, float)):
        return True
    return now - last > TTL_SECONDS
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_updatecheck.py -q`
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add src/rgit/updatecheck.py tests/test_updatecheck.py
git commit -m "feat(update): update-check state file with TTL gate and opt-out"
```

---

### Task 2: version compare, notice rendering, background fetch

**Files:**
- Modify: `src/rgit/updatecheck.py`
- Test: `tests/test_updatecheck.py` (append)

**Interfaces:**
- Consumes: Task 1's `load_state`/`save_state`/`should_check`.
- Produces: `render_notice(current: str) -> str | None`, `maybe_start_background_check(now: float) -> None`, `hint_pending(path: str) -> bool`, `mark_hint_shown(path: str) -> None`, private `_newer(latest, current) -> bool`, `_fetch_once(now) -> None`.

- [ ] **Step 1: Write the failing tests (append to tests/test_updatecheck.py)**

```python
import io
import threading


def test_newer_basic():
    assert updatecheck._newer("0.0.5", "0.0.4") is True
    assert updatecheck._newer("0.0.4", "0.0.4") is False
    assert updatecheck._newer("0.0.3", "0.0.4") is False
    assert updatecheck._newer("0.1.0", "0.0.9") is True


def test_newer_incomparable_is_false():
    assert updatecheck._newer("weird", "0.0.4") is False


def test_render_notice_from_cache(monkeypatch, tmp_path):
    _use_tmp_state(monkeypatch, tmp_path)
    updatecheck.save_state({"latest_version": "0.0.9"})
    notice = updatecheck.render_notice("0.0.4")
    assert notice == ("research-git 0.0.9 available (you have 0.0.4) "
                      "— run `rgit update`")


def test_render_notice_none_when_current(monkeypatch, tmp_path):
    _use_tmp_state(monkeypatch, tmp_path)
    updatecheck.save_state({"latest_version": "0.0.4"})
    assert updatecheck.render_notice("0.0.4") is None


def test_render_notice_none_when_no_cache(monkeypatch, tmp_path):
    _use_tmp_state(monkeypatch, tmp_path)
    assert updatecheck.render_notice("0.0.4") is None


class _FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_fetch_once_success(monkeypatch, tmp_path):
    _use_tmp_state(monkeypatch, tmp_path)
    body = b'{"info": {"version": "0.0.9"}}'
    monkeypatch.setattr(updatecheck.urllib.request, "urlopen",
                        lambda url, timeout: _FakeResp(body))
    updatecheck._fetch_once(now=42.0)
    st = updatecheck.load_state()
    assert st["latest_version"] == "0.0.9"
    assert st["last_check"] == 42.0


def test_fetch_once_failure_is_silent(monkeypatch, tmp_path):
    _use_tmp_state(monkeypatch, tmp_path)

    def boom(url, timeout):
        raise OSError("no network")

    monkeypatch.setattr(updatecheck.urllib.request, "urlopen", boom)
    updatecheck._fetch_once(now=42.0)          # must not raise
    assert "latest_version" not in updatecheck.load_state()


def test_maybe_start_stamps_and_spawns(monkeypatch, tmp_path):
    _use_tmp_state(monkeypatch, tmp_path)
    monkeypatch.delenv(updatecheck.ENV_FLAG, raising=False)
    calls = []
    monkeypatch.setattr(updatecheck, "_fetch_once",
                        lambda now: calls.append(now))

    class InlineThread:
        def __init__(self, target=None, args=(), daemon=None):
            self._target, self._args = target, args

        def start(self):
            self._target(*self._args)

    monkeypatch.setattr(updatecheck.threading, "Thread", InlineThread)
    updatecheck.maybe_start_background_check(now=1000.0)
    assert calls == [1000.0]
    # stamped immediately: a second call inside the TTL does nothing
    updatecheck.maybe_start_background_check(now=1001.0)
    assert calls == [1000.0]


def test_hint_ledger(monkeypatch, tmp_path):
    _use_tmp_state(monkeypatch, tmp_path)
    assert updatecheck.hint_pending("/x/AGENTS.md") is True
    updatecheck.mark_hint_shown("/x/AGENTS.md")
    assert updatecheck.hint_pending("/x/AGENTS.md") is False
    assert updatecheck.hint_pending("/y/CLAUDE.md") is True
```

- [ ] **Step 2: Run tests to verify the new ones fail**

Run: `.venv/bin/python -m pytest tests/test_updatecheck.py -q`
Expected: prior 8 pass; new tests FAIL with `AttributeError: ... has no attribute '_newer'` (and similar).

- [ ] **Step 3: Implement (append to src/rgit/updatecheck.py)**

Add imports at the top of the file: `import threading` and `import urllib.request` (keep `import urllib.request` its own line so tests can monkeypatch `updatecheck.urllib.request.urlopen`).

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_updatecheck.py -q`
Expected: all pass (17).

- [ ] **Step 5: Commit**

```bash
git add src/rgit/updatecheck.py tests/test_updatecheck.py
git commit -m "feat(update): cached notice rendering and daemon-thread PyPI check"
```

---

### Task 3: guidance-block fingerprint + conservative refresh

**Files:**
- Modify: `src/rgit/agent_guidance.py`
- Test: `tests/test_agent_guidance.py` (modify two existing tests, append new ones)

**Interfaces:**
- Consumes: nothing new.
- Produces: `canonical_hash(block: str) -> str`, `classify_block(text: str) -> str` (returns `"absent" | "broken" | "pristine" | "customized"`), `refresh_managed_block(path: Path) -> dict` (actions: `absent_file`, `skipped_removed`, `skipped_customized`, `skipped_broken`, `updated`, `unchanged`; skip results carry a `"hint"` key), `HISTORICAL_HASHES: frozenset`, `_START_RE: re.Pattern`. `render_global_block` now emits `<!-- research-git:start h=<12hex> -->`; the legacy `START` constant stays for old-block parsing.

- [ ] **Step 1: Update the two existing marker assertions**

In `tests/test_agent_guidance.py`, `render_global_block` now emits a fingerprinted START. Update `test_render_global_block_contains_markers_and_default_mode`: replace the line `assert agent_guidance.START in block` with:

```python
    assert agent_guidance._START_RE.match(block)
```

In `test_upsert_creates_parent_and_file` replace `assert text.count(agent_guidance.START) == 1` with:

```python
    assert len(agent_guidance._START_RE.findall(text)) == 1
```

Apply the same `_START_RE.findall` replacement anywhere else the suite counts or asserts the bare `START` in *newly rendered* text (run the suite in Step 2 to catch every site; blocks *written by old versions* in fixtures keep the bare marker on purpose).

- [ ] **Step 2: Append the new failing tests**

```python
import hashlib


def test_render_emits_fingerprinted_start_marker():
    block = agent_guidance.render_global_block()
    m = agent_guidance._START_RE.match(block)
    assert m and m.group(1), "START marker must carry h=<12 hex>"
    assert m.group(1) == agent_guidance.canonical_hash(block)


def test_canonical_hash_ignores_mode_and_start_marker():
    default = agent_guidance.render_global_block("default")
    manual = agent_guidance.render_global_block("manual-only")
    assert agent_guidance.canonical_hash(default) == \
        agent_guidance.canonical_hash(manual)
    legacy = agent_guidance.START + "\n" + default.split("\n", 1)[1]
    assert agent_guidance.canonical_hash(legacy) == \
        agent_guidance.canonical_hash(default)


def test_classify_pristine_and_customized(tmp_path):
    fresh = agent_guidance.render_global_block()
    assert agent_guidance.classify_block("intro\n" + fresh) == "pristine"
    tampered = fresh.replace("Skip mechanical formatting",
                             "Always capture everything")
    assert agent_guidance.classify_block(tampered) == "customized"


def test_classify_absent_and_broken():
    assert agent_guidance.classify_block("# my notes\n") == "absent"
    only_end = f"# notes\n{agent_guidance.END}\n"
    assert agent_guidance.classify_block(only_end) == "broken"
    only_start = f"{agent_guidance.START}\nstuff\n"
    assert agent_guidance.classify_block(only_start) == "broken"


def test_classify_legacy_block_via_historical_hash(monkeypatch):
    # a legacy block: bare START, no fingerprint, body unknown to current render
    body = "## research-git\n\nold official text\n"
    legacy = f"{agent_guidance.START}\n{body}{agent_guidance.END}\n"
    h = agent_guidance.canonical_hash(legacy)
    assert agent_guidance.classify_block(legacy) == "customized"
    monkeypatch.setattr(agent_guidance, "HISTORICAL_HASHES", frozenset({h}))
    assert agent_guidance.classify_block(legacy) == "pristine"


def test_refresh_replaces_pristine_and_carries_mode(tmp_path):
    path = tmp_path / "AGENTS.md"
    old = agent_guidance.render_global_block("manual-only")
    path.write_text("# mine\n\n" + old, encoding="utf-8")
    res = agent_guidance.refresh_managed_block(path)
    assert res["action"] in ("updated", "unchanged")
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# mine\n")
    assert "Current mode: manual-only" in text


def test_refresh_skips_customized(tmp_path):
    path = tmp_path / "AGENTS.md"
    block = agent_guidance.render_global_block().replace(
        "Skip mechanical formatting", "my own rule")
    path.write_text(block, encoding="utf-8")
    before = path.read_text(encoding="utf-8")
    res = agent_guidance.refresh_managed_block(path)
    assert res["action"] == "skipped_customized"
    assert "rgit install" in res["hint"]
    assert path.read_text(encoding="utf-8") == before


def test_refresh_never_appends_when_removed(tmp_path):
    path = tmp_path / "AGENTS.md"
    path.write_text("# no block here\n", encoding="utf-8")
    res = agent_guidance.refresh_managed_block(path)
    assert res["action"] == "skipped_removed"
    assert "rgit install" in res["hint"]
    assert path.read_text(encoding="utf-8") == "# no block here\n"


def test_refresh_warns_on_broken_markers(tmp_path):
    path = tmp_path / "AGENTS.md"
    path.write_text(f"notes\n{agent_guidance.END}\n", encoding="utf-8")
    res = agent_guidance.refresh_managed_block(path)
    assert res["action"] == "skipped_broken"
    assert str(path) in res["hint"]


def test_refresh_missing_file(tmp_path):
    res = agent_guidance.refresh_managed_block(tmp_path / "nope.md")
    assert res["action"] == "absent_file"


def test_upsert_still_replaces_fingerprinted_block(tmp_path):
    path = tmp_path / "AGENTS.md"
    path.write_text(agent_guidance.render_global_block(), encoding="utf-8")
    res = agent_guidance.upsert_managed_block(path)
    assert res["action"] in ("updated", "unchanged")
    text = path.read_text(encoding="utf-8")
    assert len(agent_guidance._START_RE.findall(text)) == 1
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_agent_guidance.py -q`
Expected: new tests FAIL (`AttributeError: ... no attribute '_START_RE'` etc.); pre-existing ones still pass.

- [ ] **Step 4: Implement in src/rgit/agent_guidance.py**

Add `import hashlib` at the top. After the existing `START`/`END` constants add:

```python
# Fingerprinted START marker: h= is the canonical hash of the block body, so
# the update path can tell an official block (safe to replace) from one the
# user edited (never touch). The bare `START` form is what pre-0.0.5 releases
# wrote; _START_RE accepts both.
_START_RE = re.compile(r"<!-- research-git:start(?: h=([0-9a-f]{12}))? -->")

# canonical_hash of every official block body ever shipped without a
# fingerprint (see docs/superpowers/specs/2026-07-04-runtime-update-check-design.md).
HISTORICAL_HASHES = frozenset({
    "9e20fa27047f",   # v0.0.1 – v0.0.3
    "c7d73fc2ba60",   # v0.0.4
})


def canonical_hash(block: str) -> str:
    """12-hex digest of a block's body: markers and mode line excluded, so the
    hash survives mode pinning and marker-format changes."""
    lines = block.strip().splitlines()
    body = [l.rstrip() for l in lines[1:-1]
            if not l.startswith("Current mode:")]
    text = "\n".join(body).strip() + "\n"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
```

Change `render_global_block` to stamp the fingerprint: build the block exactly as today but with a placeholder start line, then swap it. Replace the current `return (f"{START}\n" ... )` structure with:

```python
def render_global_block(mode: str = "default") -> str:
    if mode not in KNOWN_MODES:
        mode = "default"
    body = (
        "## research-git\n"
        ...existing body lines UNCHANGED, everything currently between
        f"{START}\n" and f"{END}\n"...
    )
    provisional = f"{START}\n{body}{END}\n"
    h = canonical_hash(provisional)
    return f"<!-- research-git:start h={h} -->\n{body}{END}\n"
```

(Only the first and last lines of the function change; the body string literal stays byte-identical so the canonical hash of the *current* release content matches fresh stamps.)

Update `_managed_span` to find either marker form:

```python
def _managed_span(text: str) -> tuple[int, int] | None:
    m = _START_RE.search(text)
    if m is None:
        return None
    end = text.find(END, m.start())
    if end < 0:
        return None
    after_end = end + len(END)
    if text[after_end:after_end + 1] == "\n":
        after_end += 1
    return m.start(), after_end
```

Add classification and the conservative refresh:

```python
def classify_block(text: str) -> str:
    """absent | broken | pristine | customized (update-path policy input)."""
    span = _managed_span(text)
    if span is None:
        if _START_RE.search(text) or END in text:
            return "broken"
        return "absent"
    block = text[span[0]:span[1]]
    h = canonical_hash(block)
    stamped = _START_RE.match(block).group(1)
    if h == stamped or h in HISTORICAL_HASHES \
            or h == canonical_hash(render_global_block()):
        return "pristine"
    return "customized"


def refresh_managed_block(path: Path) -> dict:
    """Update-path guidance refresh: replace only pristine official blocks.

    Unlike upsert_managed_block (explicit-install semantics), this never
    appends a missing block and never overwrites user edits — it skips and
    explains instead.
    """
    if not path.exists():
        return {"action": "absent_file", "path": str(path)}
    text = path.read_text(encoding="utf-8")
    kind = classify_block(text)
    if kind == "absent":
        return {"action": "skipped_removed", "path": str(path),
                "hint": (f"no research-git block in {path} (removed on "
                         f"purpose?) — run `rgit install` to restore it")}
    if kind == "broken":
        return {"action": "skipped_broken", "path": str(path),
                "hint": (f"research-git markers in {path} look damaged (one "
                         f"of the start/end pair is missing) — fix or remove "
                         f"them manually, then run `rgit install`")}
    if kind == "customized":
        return {"action": "skipped_customized", "path": str(path),
                "hint": (f"customized research-git block in {path} left "
                         f"untouched — run `rgit install` to overwrite it "
                         f"with the new official version")}
    start, end = _managed_span(text)
    block = _carry_mode(text[start:end], render_global_block("default"))
    new_text = text[:start] + block + text[end:]
    if new_text == text:
        return {"action": "unchanged", "path": str(path)}
    _atomic_write(path, new_text)
    return {"action": "updated", "path": str(path)}
```

- [ ] **Step 5: Run the full guidance + installer suites**

Run: `.venv/bin/python -m pytest tests/test_agent_guidance.py tests/test_installer.py -q`
Expected: all pass. If an installer test asserts the bare START marker in rendered output, update it with the `_START_RE.findall` pattern from Step 1.

- [ ] **Step 6: Commit**

```bash
git add src/rgit/agent_guidance.py tests/test_agent_guidance.py tests/test_installer.py
git commit -m "feat(guidance): fingerprinted managed block + conservative refresh"
```

---

### Task 4: `selfupdate` module + `python -m rgit`

**Files:**
- Create: `src/rgit/selfupdate.py`
- Create: `src/rgit/__main__.py`
- Test: `tests/test_selfupdate.py`

**Interfaces:**
- Consumes: `installer.detect_platforms()`.
- Produces: `detect_installer() -> str` (`"uv-tool" | "pipx" | "pip"`), `upgrade_command(installer: str) -> list[str]`, `run_update(refresh: bool = True) -> int`. Task 5's CLI calls `selfupdate.run_update()`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_selfupdate.py
import subprocess
import sys
import types

from rgit import selfupdate


def test_detect_installer_uv_tool(monkeypatch):
    monkeypatch.setattr(selfupdate.sys, "prefix",
                        "/home/u/.local/share/uv/tools/research-git")
    assert selfupdate.detect_installer() == "uv-tool"


def test_detect_installer_uv_tool_windows(monkeypatch):
    monkeypatch.setattr(selfupdate.sys, "prefix",
                        r"C:\Users\u\AppData\Roaming\uv\tools\research-git")
    assert selfupdate.detect_installer() == "uv-tool"


def test_detect_installer_pipx(monkeypatch):
    monkeypatch.setattr(selfupdate.sys, "prefix",
                        "/home/u/.local/pipx/venvs/research-git")
    assert selfupdate.detect_installer() == "pipx"


def test_detect_installer_pip_fallback(monkeypatch):
    monkeypatch.setattr(selfupdate.sys, "prefix", "/repo/.venv")
    assert selfupdate.detect_installer() == "pip"


def test_upgrade_command_mapping():
    assert selfupdate.upgrade_command("uv-tool") == \
        ["uv", "tool", "upgrade", "research-git"]
    assert selfupdate.upgrade_command("pipx") == \
        ["pipx", "upgrade", "research-git"]
    assert selfupdate.upgrade_command("pip") == \
        [sys.executable, "-m", "pip", "install", "--upgrade", "research-git"]


def _completed(rc=0, out="", err=""):
    return subprocess.CompletedProcess(args=[], returncode=rc,
                                       stdout=out, stderr=err)


def test_run_update_success_refreshes_platforms(monkeypatch, capsys):
    ran = []

    def fake_run(cmd, **kw):
        ran.append(cmd)
        return _completed(0, out="ok")

    monkeypatch.setattr(selfupdate.subprocess, "run", fake_run)
    monkeypatch.setattr(selfupdate.shutil, "which",
                        lambda name: "/usr/bin/rgit" if name == "rgit" else None)
    import rgit.installer as installer
    monkeypatch.setattr(installer, "detect_platforms",
                        lambda: ["claude-code", "codex"])
    assert selfupdate.run_update() == 0
    # first call: the upgrade; then one refresh subprocess per platform
    assert ran[0][-1] == "research-git" or "research-git" in ran[0]
    assert ["/usr/bin/rgit", "install", "claude-code", "--from-update"] in ran
    assert ["/usr/bin/rgit", "install", "codex", "--from-update"] in ran


def test_run_update_failure_skips_refresh(monkeypatch, capsys):
    ran = []

    def fake_run(cmd, **kw):
        ran.append(cmd)
        return _completed(1, err="boom")

    monkeypatch.setattr(selfupdate.subprocess, "run", fake_run)
    assert selfupdate.run_update() == 1
    assert len(ran) == 1                      # no refresh after failed upgrade


def test_run_update_pep668_hint(monkeypatch, capsys):
    monkeypatch.setattr(
        selfupdate.subprocess, "run",
        lambda cmd, **kw: _completed(1, err="error: externally-managed-environment"))
    assert selfupdate.run_update() == 1
    err = capsys.readouterr().err
    assert "uv tool install research-git" in err


def test_run_update_windows_lock_hint(monkeypatch, capsys):
    monkeypatch.setattr(
        selfupdate.subprocess, "run",
        lambda cmd, **kw: _completed(1, err="[WinError 5] Access is denied: rgit.exe"))
    assert selfupdate.run_update() == 1
    err = capsys.readouterr().err
    assert "python -m pip install -U research-git" in err


def test_run_update_missing_tool_binary(monkeypatch, capsys):
    def fake_run(cmd, **kw):
        raise FileNotFoundError(cmd[0])

    monkeypatch.setattr(selfupdate.subprocess, "run", fake_run)
    monkeypatch.setattr(selfupdate, "detect_installer", lambda: "uv-tool")
    assert selfupdate.run_update() == 1
    assert "manually" in capsys.readouterr().err


def test_python_dash_m_rgit_entrypoint():
    p = subprocess.run([sys.executable, "-m", "rgit", "install", "--list"],
                       capture_output=True, text=True)
    assert p.returncode == 0
    assert "platforms:" in p.stdout
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_selfupdate.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'rgit.selfupdate'`.

- [ ] **Step 3: Implement**

```python
# src/rgit/__main__.py
import sys

from .cli import main

sys.exit(main())
```

```python
# src/rgit/selfupdate.py
"""`rgit update`: delegate the package upgrade to whichever installer owns
this environment, then refresh installed platforms so the Claude Code plugin
copy and guidance blocks track the new version.

We never touch site-packages ourselves — uv/pipx/pip own their environments.
The platform refresh runs in a *fresh subprocess* because this process still
has the pre-upgrade code and plugin assets in memory.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

_PEP668_MARKER = "externally-managed-environment"
_WIN_LOCK_MARKERS = ("WinError 5", "Access is denied")


def detect_installer() -> str:
    prefix = Path(sys.prefix).as_posix().lower()
    if "/uv/tools/" in prefix:
        return "uv-tool"
    if "/pipx/venvs/" in prefix:
        return "pipx"
    return "pip"


def upgrade_command(installer: str) -> list[str]:
    return {
        "uv-tool": ["uv", "tool", "upgrade", "research-git"],
        "pipx": ["pipx", "upgrade", "research-git"],
        "pip": [sys.executable, "-m", "pip", "install", "--upgrade",
                "research-git"],
    }[installer]


def _rgit_cmd() -> list[str]:
    exe = shutil.which("rgit")
    return [exe] if exe else [sys.executable, "-m", "rgit"]


def _refresh_platforms() -> None:
    from . import installer as inst
    platforms = inst.detect_platforms()
    if not platforms:
        print("no agent platforms detected; nothing to refresh")
        return
    for pf in platforms:
        cmd = _rgit_cmd() + ["install", pf, "--from-update"]
        p = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        print(f"{pf}: " + ("refreshed" if p.returncode == 0
                           else "refresh FAILED"))
        tail = ((p.stdout or "") + (p.stderr or "")).strip()
        if tail:
            print("  " + "\n  ".join(tail.splitlines()))


def run_update(refresh: bool = True) -> int:
    """Upgrade the package; on success refresh installed platforms.

    Exit code reflects the upgrade step only (spec: everything else degrades
    to hints).
    """
    cmd = upgrade_command(detect_installer())
    print("upgrading research-git via: " + " ".join(cmd))
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
    except FileNotFoundError:
        print(f"`{cmd[0]}` not found — upgrade manually: "
              f"{sys.executable} -m pip install -U research-git",
              file=sys.stderr)
        return 1
    if p.stdout:
        sys.stdout.write(p.stdout)
    if p.stderr:
        sys.stderr.write(p.stderr)
    if p.returncode != 0:
        out = (p.stdout or "") + (p.stderr or "")
        if _PEP668_MARKER in out:
            print("this Python forbids pip installs (PEP 668) — try: "
                  "uv tool install research-git", file=sys.stderr)
        elif any(m in out for m in _WIN_LOCK_MARKERS):
            print("Windows locked rgit.exe while it was running — run "
                  "manually: python -m pip install -U research-git",
                  file=sys.stderr)
        return p.returncode
    if refresh:
        _refresh_platforms()
    return 0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_selfupdate.py -q`
Expected: 11 passed.

- [ ] **Step 5: Commit**

```bash
git add src/rgit/selfupdate.py src/rgit/__main__.py tests/test_selfupdate.py
git commit -m "feat(update): rgit update pipeline — installer detection, delegated upgrade, platform refresh"
```

---

### Task 5: CLI wiring — `update` command, hidden `--from-update`, notice hook

**Files:**
- Modify: `src/rgit/cli.py` (parser: after the `install` subparser block ~line 468; dispatch: inside `main`), `src/rgit/installer.py` (`install()`, `_install_guidance`, adapters)
- Test: `tests/test_cli_update.py` (new)

**Interfaces:**
- Consumes: `updatecheck.set_disabled/maybe_start_background_check/render_notice/hint_pending/mark_hint_shown`, `selfupdate.run_update`, `agent_guidance.refresh_managed_block`.
- Produces: `rgit update [--off|--on]`; `rgit install <platform> --from-update` (hidden); installer signatures gain `conservative: bool = False` keyword: `install(platform, *, scope="user", dry_run=False, mode=None, conservative=False)`, adapters `_install_claude_code(scope, dry_run, mode=None, conservative=False)` and `_install_agents_cli(platform, scope, dry_run, mode=None, conservative=False)`, `_install_guidance(platform, dry_run, mode=None, conservative=False)`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_cli_update.py
import sys

from rgit import cli, selfupdate, updatecheck


def _use_tmp_state(monkeypatch, tmp_path):
    monkeypatch.setattr(updatecheck, "state_path",
                        lambda: tmp_path / "update-check.json")


def test_update_off_on(monkeypatch, tmp_path, capsys):
    _use_tmp_state(monkeypatch, tmp_path)
    monkeypatch.delenv(updatecheck.ENV_FLAG, raising=False)
    assert cli.main(["update", "--off"]) == 0
    assert updatecheck.disabled() is True
    assert "disabled" in capsys.readouterr().out
    assert cli.main(["update", "--on"]) == 0
    assert updatecheck.disabled() is False


def test_update_dispatches_to_selfupdate(monkeypatch, tmp_path):
    _use_tmp_state(monkeypatch, tmp_path)
    called = []
    monkeypatch.setattr(selfupdate, "run_update",
                        lambda: called.append(True) or 0)
    assert cli.main(["update"]) == 0
    assert called == [True]


def test_notice_printed_after_command_on_tty(monkeypatch, tmp_path, capsys):
    _use_tmp_state(monkeypatch, tmp_path)
    monkeypatch.delenv(updatecheck.ENV_FLAG, raising=False)
    updatecheck.save_state({"latest_version": "99.0.0",
                            "last_check": 9e12})
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    assert cli.main(["install", "--list"]) == 0
    err = capsys.readouterr().err
    assert "99.0.0 available" in err
    assert "rgit update" in err


def test_notice_suppressed_when_not_tty(monkeypatch, tmp_path, capsys):
    _use_tmp_state(monkeypatch, tmp_path)
    monkeypatch.delenv(updatecheck.ENV_FLAG, raising=False)
    updatecheck.save_state({"latest_version": "99.0.0",
                            "last_check": 9e12})
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
    assert cli.main(["install", "--list"]) == 0
    assert "99.0.0" not in capsys.readouterr().err


def test_notice_suppressed_for_update_cmd(monkeypatch, tmp_path, capsys):
    _use_tmp_state(monkeypatch, tmp_path)
    monkeypatch.delenv(updatecheck.ENV_FLAG, raising=False)
    updatecheck.save_state({"latest_version": "99.0.0",
                            "last_check": 9e12})
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(selfupdate, "run_update", lambda: 0)
    cli.main(["update"])
    assert "available" not in capsys.readouterr().err


def test_install_from_update_uses_conservative_guidance(monkeypatch, tmp_path):
    import rgit.installer as installer
    seen = {}

    def fake_install(platform, *, scope, dry_run, mode, conservative=False):
        seen["conservative"] = conservative
        return {"platform": platform, "ran": True, "results": [],
                "guidance": {"action": "skipped_customized",
                             "path": "/x", "hint": "left untouched"}}

    monkeypatch.setattr(installer, "install", fake_install)
    assert cli.main(["install", "codex", "--from-update"]) == 0
    assert seen["conservative"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_cli_update.py -q`
Expected: FAIL — `update` is an unknown command (argparse SystemExit 2; pytest reports errors), `--from-update` unrecognized.

- [ ] **Step 3: Implement the installer plumbing (src/rgit/installer.py)**

`_install_guidance` grows the conservative branch (imports `updatecheck` lazily to gate the removed-hint to once):

```python
def _install_guidance(platform: str, dry_run: bool, mode: str | None = None,
                      conservative: bool = False) -> dict:
    if mode == "none":
        return {"action": "disabled"}
    target = agent_platforms.guidance_target(platform)
    if target is None:
        return agent_guidance.manual_status(mode or "default")
    if conservative:
        res = agent_guidance.refresh_managed_block(target["path"])
        if res.get("action") == "skipped_removed":
            from . import updatecheck
            if updatecheck.hint_pending(res["path"]):
                updatecheck.mark_hint_shown(res["path"])
            else:
                res.pop("hint", None)
        res["reload"] = target["reload"]
        return res
    try:
        res = agent_guidance.upsert_managed_block(target["path"], mode=mode,
                                                  dry_run=dry_run)
    except OSError as e:
        return _guidance_error(target["path"], e, mode)
    res["reload"] = target["reload"]
    return res
```

Thread `conservative` through with a default so every existing caller is untouched:

- `_install_claude_code(scope, dry_run, mode=None, conservative=False)` — pass `conservative` to both `_install_guidance` calls in it.
- `_install_agents_cli(platform, scope, dry_run, mode=None, conservative=False)` — same for its two `_install_guidance` calls.
- `install(platform, *, scope="user", dry_run=False, mode=None, conservative=False)` — pass through: `return _INSTALL[platform](scope, dry_run, mode, conservative)`.
- `uninstall` is unchanged.

- [ ] **Step 4: Implement the CLI (src/rgit/cli.py)**

Parser — after the `p_inst` block (following line 467) add the hidden flag and the new subcommand:

```python
    p_inst.add_argument("--from-update", dest="from_update",
                        action="store_true", help=argparse.SUPPRESS)

    p_upd = sub.add_parser("update")   # upgrade package + refresh platforms
    upd_grp = p_upd.add_mutually_exclusive_group()
    upd_grp.add_argument("--off", action="store_true",
                         help="permanently disable the update notice")
    upd_grp.add_argument("--on", action="store_true",
                         help="re-enable the update notice")
```

Dispatch — in `main()` right after `args = parser.parse_args(argv)` (line 527), insert the notice bracket and the `update` command:

```python
    notice = None
    if args.cmd not in ("mcp", "update") and sys.stdout.isatty() \
            and not getattr(args, "json", False):
        import time
        from . import updatecheck
        from . import __version__
        updatecheck.maybe_start_background_check(time.time())
        notice = updatecheck.render_notice(__version__)

    try:
        return _dispatch(args, parser)
    finally:
        if notice:
            print(notice, file=sys.stderr)
```

Mechanical refactor this requires: move everything currently in `main()` after the `parse_args` line into a new `def _dispatch(args, parser) -> int:` directly below `main` (body unchanged, just re-indented under the new signature; `main` keeps `_force_utf8_stdio()`, parser construction, `parse_args`, then the bracket above).

At the top of `_dispatch` add the `update` command:

```python
    if args.cmd == "update":
        from . import selfupdate, updatecheck
        if args.off or args.on:
            updatecheck.set_disabled(bool(args.off))
            print("update notice " + ("disabled" if args.off else "enabled"))
            return 0
        return selfupdate.run_update()
```

In the `install` dispatch, pass the flag through (the `fn(...)` call at line 598). `installer.uninstall` does not accept `conservative`, so guard it:

```python
        extra = {}
        if not args.uninstall:
            extra["conservative"] = getattr(args, "from_update", False)
        results = [fn(p, scope=args.scope, dry_run=args.dry_run, mode=mode,
                      **extra)
                   for p in platforms]
```

Also skip the interactive guidance-mode prompt when refreshing (the `--from-update` path must never prompt) — extend the existing condition at line 571 from `if mode is None and not args.uninstall and not sys.stdin.isatty():` to:

```python
        if getattr(args, "from_update", False):
            pass                       # conservative refresh decides per-file
        elif mode is None and not args.uninstall and not sys.stdin.isatty():
```

(the `elif mode is None and not args.uninstall:` prompt branch stays as the next arm).

In `_render_install_result`, inside the `if g:` guidance section, surface hints — after the line printing the guidance action add:

```python
    if g.get("hint"):
        print(f"      hint: {g['hint']}")
```

- [ ] **Step 5: Run the new tests, then the whole suite**

Run: `.venv/bin/python -m pytest tests/test_cli_update.py -q`
Expected: 6 passed.
Run: `.venv/bin/python -m pytest -q`
Expected: all pass (386 pre-existing + new; 1 pre-existing skip). The `_dispatch` refactor must not change any existing behavior — investigate any regression before proceeding.

- [ ] **Step 6: Commit**

```bash
git add src/rgit/cli.py src/rgit/installer.py tests/test_cli_update.py
git commit -m "feat(cli): rgit update command, --off/--on switch, TTY update notice"
```

---

### Task 6: README + full verification

**Files:**
- Modify: `README.md` (after the install section)

**Interfaces:** none.

- [ ] **Step 1: Document updating**

Locate the installation section in README.md and append below it:

```markdown
## Updating

```bash
rgit update
```

Upgrades the package (via whichever of uv/pipx/pip installed it) and
refreshes every installed platform surface: the Claude Code plugin copy, MCP
config, and the managed guidance blocks. Guidance blocks you have customized
or removed are left alone — the command tells you how to restore them
instead.

rgit prints a one-line notice (at most once a day, terminal sessions only)
when a newer release is on PyPI. Turn it off for good with `rgit update
--off`, or per-environment with `RGIT_UPDATE_CHECK=0`.
```

- [ ] **Step 2: Full suite + smoke run**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass.

Run: `.venv/bin/python -m rgit update --off && .venv/bin/python -m rgit update --on`
Expected: prints `update notice disabled` then `update notice enabled`; exit 0.

Run: `RGIT_UPDATE_CHECK=0 .venv/bin/python -m rgit install --list`
Expected: platform list, no notice line, exit 0.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: updating section — rgit update and the update notice"
```
