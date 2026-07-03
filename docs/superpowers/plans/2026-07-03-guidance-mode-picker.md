# Guidance Mode Picker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the interactive `rgit install` guidance-mode number prompt with an arrow-key selector while keeping the old numbered prompt as a silent fallback.

**Architecture:** Keep the feature inside `src/rgit/cli.py`. Split the prompt path into small private helpers: a public-internal `_prompt_guidance_mode()` wrapper, an interactive selector, a numbered fallback, key normalization, and small POSIX/Windows key readers. Tests monkeypatch helper functions and stream objects rather than relying on a real terminal.

**Tech Stack:** Python standard library only: `sys`, `os`, `termios`, `tty`, `select`, and `msvcrt` behind platform-guarded imports. Test with pytest.

---

## File Structure

- Modify `src/rgit/cli.py`
  - Keep `GUIDANCE_MODES` unchanged.
  - Add private constants for guidance mode labels/descriptions.
  - Split `_prompt_guidance_mode()` into interactive selector plus numbered fallback.
  - Add small backend helpers for POSIX and Windows key reads.
  - Keep prompt output on stderr and JSON install output on stdout.
- Modify `tests/test_cli.py`
  - Add prompt-focused unit tests near `test_install_list_and_dry_run`.
  - Monkeypatch TTY detection, key reading, and installer calls.
  - Verify fallback, explicit `--guidance`, non-TTY behavior, and stdout JSON.

---

### Task 1: Preserve Numbered Prompt Behind a Helper

**Files:**
- Modify: `src/rgit/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write failing tests for numbered fallback behavior**

Add tests near the existing install CLI tests:

```python
def test_guidance_numbered_prompt_accepts_blank_default(monkeypatch):
    answers = iter([""])
    monkeypatch.setattr("builtins.input", lambda: next(answers))

    assert cli._prompt_guidance_mode_numbered("codex") == "default"


def test_guidance_numbered_prompt_accepts_numbers_and_names(monkeypatch):
    cases = [
        ("1", "default"),
        ("2", "manual-only"),
        ("3", "none"),
        ("default", "default"),
        ("manual-only", "manual-only"),
        ("none", "none"),
    ]

    for answer, expected in cases:
        answers = iter([answer])
        monkeypatch.setattr("builtins.input", lambda: next(answers))
        assert cli._prompt_guidance_mode_numbered("codex") == expected
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_cli.py -k guidance_numbered -v`

Expected: FAIL because `_prompt_guidance_mode_numbered` does not exist.

- [ ] **Step 3: Implement numbered helper**

In `src/rgit/cli.py`, replace the current `_prompt_guidance_mode()` body with:

```python
def _prompt_guidance_mode(platform: str) -> str:
    return _prompt_guidance_mode_numbered(platform)


def _prompt_guidance_mode_numbered(platform: str) -> str:
    """Fallback picker that accepts 1/2/3, mode names, or blank=default."""
    sys.stderr.write(
        f"\nresearch-git guidance for {platform} "
        "- how proactive should capture be?\n"
        "  1) default     - consider capture after meaningful changes (recommended)\n"
        "  2) manual-only - only when you explicitly ask\n"
        "  3) none        - install skills + MCP only, write no guidance\n"
    )
    choices = {"1": "default", "2": "manual-only", "3": "none", "": "default",
               "default": "default", "manual-only": "manual-only", "none": "none"}
    while True:
        sys.stderr.write("> ")
        sys.stderr.flush()
        try:
            answer = input().strip().lower()
        except EOFError:
            return "default"
        if answer in choices:
            return choices[answer]
        sys.stderr.write("Please enter 1, 2, or 3.\n")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_cli.py -k guidance_numbered -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/rgit/cli.py tests/test_cli.py
git commit -m "test: cover guidance mode numbered fallback"
```

---

### Task 2: Add Selector State and Rendering

**Files:**
- Modify: `src/rgit/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write failing tests for selector navigation**

Add tests:

```python
class _TTYBuffer:
    def __init__(self):
        self.parts = []
    def write(self, text):
        self.parts.append(text)
    def flush(self):
        pass
    def isatty(self):
        return True
    def getvalue(self):
        return "".join(self.parts)


def test_guidance_selector_defaults_to_default_on_enter(monkeypatch):
    err = _TTYBuffer()
    keys = iter(["enter"])
    monkeypatch.setattr(cli, "_read_prompt_key", lambda: next(keys))

    assert cli._prompt_guidance_mode_interactive("codex", stderr=err) == "default"
    assert "> default" in err.getvalue()


def test_guidance_selector_moves_down_and_selects(monkeypatch):
    err = _TTYBuffer()
    keys = iter(["down", "enter"])
    monkeypatch.setattr(cli, "_read_prompt_key", lambda: next(keys))

    assert cli._prompt_guidance_mode_interactive("codex", stderr=err) == "manual-only"


def test_guidance_selector_accepts_numeric_shortcut(monkeypatch):
    err = _TTYBuffer()
    keys = iter(["3"])
    monkeypatch.setattr(cli, "_read_prompt_key", lambda: next(keys))

    assert cli._prompt_guidance_mode_interactive("codex", stderr=err) == "none"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_cli.py -k guidance_selector -v`

Expected: FAIL because selector helpers do not exist.

- [ ] **Step 3: Implement selector constants and render loop**

Add near `GUIDANCE_MODES`:

```python
_GUIDANCE_OPTIONS = [
    ("default", "consider capture after meaningful changes (recommended)"),
    ("manual-only", "only when you explicitly ask"),
    ("none", "install skills + MCP only, write no guidance"),
]


class _InteractivePromptUnavailable(Exception):
    pass
```

Add selector helpers:

```python
def _prompt_guidance_mode_interactive(platform: str, stderr=None) -> str:
    stderr = stderr or sys.stderr
    index = 0
    first_render = True
    while True:
        _render_guidance_selector(platform, index, stderr, first_render)
        first_render = False
        key = _read_prompt_key()
        if key == "ctrl-c":
            raise KeyboardInterrupt
        if key == "up":
            index = (index - 1) % len(_GUIDANCE_OPTIONS)
        elif key == "down":
            index = (index + 1) % len(_GUIDANCE_OPTIONS)
        elif key == "enter":
            return _GUIDANCE_OPTIONS[index][0]
        elif key in ("1", "2", "3"):
            return _GUIDANCE_OPTIONS[int(key) - 1][0]


def _render_guidance_selector(platform: str, index: int, stderr, first_render: bool) -> None:
    if not first_render:
        stderr.write("\x1b[6F\x1b[J")
    stderr.write(
        f"\nresearch-git guidance for {platform} - how proactive should capture be?\n\n"
    )
    for i, (mode, description) in enumerate(_GUIDANCE_OPTIONS):
        pointer = ">" if i == index else " "
        stderr.write(f"{pointer} {mode:<11} {description}\n")
    stderr.write("\nUse ↑/↓ to move, Enter to select.\n")
    stderr.flush()
```

Stub key reading for now:

```python
def _read_prompt_key() -> str:
    raise _InteractivePromptUnavailable
```

- [ ] **Step 4: Run selector tests**

Run: `.venv/bin/pytest tests/test_cli.py -k guidance_selector -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/rgit/cli.py tests/test_cli.py
git commit -m "feat: add guidance mode selector loop"
```

---

### Task 3: Wire Selector with Silent Fallback

**Files:**
- Modify: `src/rgit/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write failing tests for fallback and bypass behavior**

Add tests:

```python
def test_prompt_guidance_mode_falls_back_to_numbered(monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "_prompt_guidance_mode_interactive",
                        lambda platform: (_ for _ in ()).throw(cli._InteractivePromptUnavailable()))
    monkeypatch.setattr(cli, "_prompt_guidance_mode_numbered",
                        lambda platform: calls.append(platform) or "manual-only")

    assert cli._prompt_guidance_mode("codex") == "manual-only"
    assert calls == ["codex"]


def test_install_explicit_guidance_bypasses_prompt(monkeypatch, capsys):
    prompted = {"called": False}
    monkeypatch.setattr(cli, "_prompt_guidance_mode",
                        lambda platform: prompted.__setitem__("called", True) or "default")

    import rgit.installer as installer
    monkeypatch.setattr(installer, "install",
                        lambda platform, scope="user", dry_run=False, mode=None:
                        {"platform": platform, "mode": mode})

    assert cli.main(["install", "codex", "--guidance", "manual-only"]) == 0
    out = capsys.readouterr().out
    assert '"mode": "manual-only"' in out
    assert prompted["called"] is False


def test_install_non_tty_does_not_prompt(monkeypatch, capsys):
    prompted = {"called": False}
    monkeypatch.setattr(cli, "_stdin_is_tty", lambda: False)
    monkeypatch.setattr(cli, "_prompt_guidance_mode",
                        lambda platform: prompted.__setitem__("called", True) or "default")

    import rgit.installer as installer
    monkeypatch.setattr(installer, "install",
                        lambda platform, scope="user", dry_run=False, mode=None:
                        {"platform": platform, "mode": mode})

    assert cli.main(["install", "codex"]) == 0
    out = capsys.readouterr().out
    assert '"mode": null' in out
    assert prompted["called"] is False
```

- [ ] **Step 2: Run tests to verify fallback behavior fails where needed**

Run: `.venv/bin/pytest tests/test_cli.py -k "prompt_guidance_mode or install_explicit_guidance or install_non_tty" -v`

Expected: fallback test FAIL until `_prompt_guidance_mode()` catches `_InteractivePromptUnavailable`; bypass tests should pass or guide small fixes.

- [ ] **Step 3: Implement fallback wrapper**

Change `_prompt_guidance_mode()` to:

```python
def _prompt_guidance_mode(platform: str) -> str:
    try:
        return _prompt_guidance_mode_interactive(platform)
    except _InteractivePromptUnavailable:
        return _prompt_guidance_mode_numbered(platform)
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/test_cli.py -k "prompt_guidance_mode or install_explicit_guidance or install_non_tty" -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/rgit/cli.py tests/test_cli.py
git commit -m "feat: fall back to numbered guidance prompt"
```

---

### Task 4: Implement POSIX and Windows Key Readers

**Files:**
- Modify: `src/rgit/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write failing tests for key normalization**

Add tests:

```python
def test_decode_prompt_key_sequences():
    assert cli._decode_prompt_key("\r") == "enter"
    assert cli._decode_prompt_key("\n") == "enter"
    assert cli._decode_prompt_key("\x03") == "ctrl-c"
    assert cli._decode_prompt_key("1") == "1"
    assert cli._decode_prompt_key("\x1b[A") == "up"
    assert cli._decode_prompt_key("\x1b[B") == "down"
    assert cli._decode_prompt_key("x") == "other"


def test_read_prompt_key_dispatches_backend(monkeypatch):
    monkeypatch.setattr(cli.os, "name", "posix", raising=False)
    monkeypatch.setattr(cli, "_read_prompt_key_posix", lambda: "down")
    assert cli._read_prompt_key() == "down"

    monkeypatch.setattr(cli.os, "name", "nt", raising=False)
    monkeypatch.setattr(cli, "_read_prompt_key_windows", lambda: "up")
    assert cli._read_prompt_key() == "up"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_cli.py -k "decode_prompt_key or read_prompt_key_dispatches" -v`

Expected: FAIL because helpers do not exist or dispatch is stubbed.

- [ ] **Step 3: Implement imports and key decoding**

Add `import os` near the existing imports.

Add helpers:

```python
def _read_prompt_key() -> str:
    if os.name == "nt":
        return _read_prompt_key_windows()
    return _read_prompt_key_posix()


def _decode_prompt_key(seq: str) -> str:
    if seq in ("\r", "\n"):
        return "enter"
    if seq == "\x03":
        return "ctrl-c"
    if seq in ("1", "2", "3"):
        return seq
    if seq == "\x1b[A":
        return "up"
    if seq == "\x1b[B":
        return "down"
    return "other"
```

- [ ] **Step 4: Implement backend readers**

Add:

```python
def _read_prompt_key_posix() -> str:
    try:
        import select
        import termios
        import tty

        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
    except Exception as e:
        raise _InteractivePromptUnavailable from e
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        if ch == "\x1b":
            seq = ch
            while True:
                ready, _, _ = select.select([sys.stdin], [], [], 0.01)
                if not ready:
                    break
                seq += sys.stdin.read(1)
                if len(seq) >= 3:
                    break
            return _decode_prompt_key(seq)
        return _decode_prompt_key(ch)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _read_prompt_key_windows() -> str:
    try:
        import msvcrt
    except Exception as e:
        raise _InteractivePromptUnavailable from e
    ch = msvcrt.getwch()
    if ch == "\x03":
        return "ctrl-c"
    if ch in ("\r", "\n", "1", "2", "3"):
        return _decode_prompt_key(ch)
    if ch in ("\x00", "\xe0"):
        ch2 = msvcrt.getwch()
        if ch2 == "H":
            return "up"
        if ch2 == "P":
            return "down"
    return "other"
```

- [ ] **Step 5: Run key tests**

Run: `.venv/bin/pytest tests/test_cli.py -k "decode_prompt_key or read_prompt_key_dispatches" -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/rgit/cli.py tests/test_cli.py
git commit -m "feat: read guidance selector keys"
```

---

### Task 5: Add Terminal Capability Checks and Full Verification

**Files:**
- Modify: `src/rgit/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write failing tests for terminal capability checks and stdout JSON**

Add tests:

```python
def test_interactive_prompt_unavailable_when_stderr_not_tty(monkeypatch):
    class _NotTTY:
        def isatty(self):
            return False
        def write(self, text):
            pass
        def flush(self):
            pass

    with pytest.raises(cli._InteractivePromptUnavailable):
        cli._prompt_guidance_mode_interactive("codex", stderr=_NotTTY())


def test_interactive_prompt_unavailable_for_dumb_terminal(monkeypatch):
    err = _TTYBuffer()
    monkeypatch.setenv("TERM", "dumb")

    with pytest.raises(cli._InteractivePromptUnavailable):
        cli._prompt_guidance_mode_interactive("codex", stderr=err)


def test_install_stdout_remains_json_when_prompting(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(cli, "_prompt_guidance_mode", lambda platform: "default")

    import rgit.installer as installer
    monkeypatch.setattr(installer, "install",
                        lambda platform, scope="user", dry_run=False, mode=None:
                        {"platform": platform, "mode": mode})

    assert cli.main(["install", "codex"]) == 0
    captured = capsys.readouterr()
    assert captured.out.strip().startswith("{")
    data = json.loads(captured.out)
    assert data["mode"] == "default"
```

Add `import pytest` to `tests/test_cli.py` if it is not already imported.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_cli.py -k "interactive_prompt_unavailable or install_stdout_remains_json" -v`

Expected: capability tests FAIL until checks are added.

- [ ] **Step 3: Implement capability checks**

At the top of `_prompt_guidance_mode_interactive()` add:

```python
    if not getattr(stderr, "isatty", lambda: False)():
        raise _InteractivePromptUnavailable
    if os.environ.get("TERM") == "dumb":
        raise _InteractivePromptUnavailable
```

Keep `main()`'s existing `_stdin_is_tty()` gate unchanged.

- [ ] **Step 4: Run focused tests**

Run: `.venv/bin/pytest tests/test_cli.py -k "guidance or install_stdout_remains_json" -v`

Expected: PASS.

- [ ] **Step 5: Run broader test suite**

Run: `.venv/bin/pytest tests/test_cli.py tests/test_installer.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/rgit/cli.py tests/test_cli.py
git commit -m "test: verify guidance picker install behavior"
```

---

## Self-Review

- Spec coverage: POSIX and Windows readers, fallback rules, default selection,
  numeric shortcuts, Ctrl+C, no Esc/q cancel, stderr UI, stdout JSON, no new
  dependency, and no module split are covered.
- Placeholder scan: no TBD/TODO/fill-in-later steps remain.
- Type consistency: helper names match across tasks:
  `_prompt_guidance_mode`, `_prompt_guidance_mode_interactive`,
  `_prompt_guidance_mode_numbered`, `_read_prompt_key`,
  `_read_prompt_key_posix`, `_read_prompt_key_windows`,
  `_decode_prompt_key`, and `_InteractivePromptUnavailable`.
