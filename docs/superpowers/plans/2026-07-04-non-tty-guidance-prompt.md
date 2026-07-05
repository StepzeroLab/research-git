# Non-TTY Guidance Prompt Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `rgit install <platform>` require an explicit guidance-mode selection when `--guidance` is omitted, including in non-TTY agent installs.

**Architecture:** Keep the prompt code in `src/rgit/cli.py`. Reuse the existing arrow-key selector for capable TTYs, then fall back to a stricter numbered prompt that works for TTY and non-TTY stdin. Add a cancellation exception for EOF/no-selection so `main()` can return a clean non-zero status without calling the installer.

**Tech Stack:** Python standard library, argparse CLI, pytest, existing `rgit.cli` test helpers.

---

## File Structure

- Modify `src/rgit/cli.py`
  - Add `_GuidancePromptCancelled`.
  - Make `_prompt_guidance_mode_numbered()` require an explicit selection.
  - Make `main()` call `_prompt_guidance_mode()` for all installs without `--guidance`, not only TTY installs.
  - Return `1` on EOF/no-selection and `130` on `Ctrl+C`.
  - Remove `_stdin_is_tty()` if it becomes unused.

- Modify `tests/test_cli.py`
  - Update old numbered-prompt tests that expected blank/EOF to default.
  - Add non-TTY install tests that prove the numbered prompt is used.
  - Add cancellation tests proving the installer is not called after EOF.
  - Keep existing selector, explicit `--guidance`, and stdout JSON tests green.

## Task 1: Update prompt helper tests for explicit numbered input

**Files:**
- Modify: `tests/test_cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Replace the blank-default test with a blank-retry test**

Replace:

```python
def test_guidance_numbered_prompt_accepts_blank_default(monkeypatch):
    answers = iter([""])
    monkeypatch.setattr("builtins.input", lambda: next(answers))

    assert cli._prompt_guidance_mode_numbered("codex") == "default"
```

with:

```python
def test_guidance_numbered_prompt_rejects_blank_then_accepts_choice(monkeypatch):
    answers = iter(["", "2"])
    monkeypatch.setattr("builtins.input", lambda: next(answers))

    assert cli._prompt_guidance_mode_numbered("codex") == "manual-only"
```

- [ ] **Step 2: Replace the EOF-default assertion with an EOF cancellation assertion**

Replace:

```python
def test_guidance_numbered_prompt_retries_and_eof_defaults(monkeypatch):
    answers = iter(["nope", "3"])
    monkeypatch.setattr("builtins.input", lambda: next(answers))
    assert cli._prompt_guidance_mode_numbered("codex") == "none"

    monkeypatch.setattr("builtins.input", lambda: (_ for _ in ()).throw(EOFError))
    assert cli._prompt_guidance_mode_numbered("codex") == "default"
```

with:

```python
def test_guidance_numbered_prompt_retries_and_eof_cancels(monkeypatch):
    answers = iter(["nope", "3"])
    monkeypatch.setattr("builtins.input", lambda: next(answers))
    assert cli._prompt_guidance_mode_numbered("codex") == "none"

    monkeypatch.setattr("builtins.input", lambda: (_ for _ in ()).throw(EOFError))
    with pytest.raises(cli._GuidancePromptCancelled):
        cli._prompt_guidance_mode_numbered("codex")
```

- [ ] **Step 3: Update the prompt-level blank test**

Replace:

```python
def test_prompt_guidance_mode_empty_defaults_then_retries_on_garbage(monkeypatch):
    answers = iter([""])
    monkeypatch.setattr("builtins.input", lambda: next(answers))
    assert cli._prompt_guidance_mode("codex") == "default"

    answers = iter(["nope", "3"])
    monkeypatch.setattr("builtins.input", lambda: next(answers))
    assert cli._prompt_guidance_mode("codex") == "none"
```

with:

```python
def test_prompt_guidance_mode_blank_retries_and_garbage_retries(monkeypatch):
    answers = iter(["", "1"])
    monkeypatch.setattr("builtins.input", lambda: next(answers))
    assert cli._prompt_guidance_mode("codex") == "default"

    answers = iter(["nope", "3"])
    monkeypatch.setattr("builtins.input", lambda: next(answers))
    assert cli._prompt_guidance_mode("codex") == "none"
```

- [ ] **Step 4: Run the targeted prompt tests and verify they fail for the expected reason**

Run:

```bash
python -m pytest tests/test_cli.py -k "guidance_numbered_prompt or prompt_guidance_mode" -v
```

Expected: failures showing `_GuidancePromptCancelled` is not defined yet and blank/EOF still use the old default behavior.

## Task 2: Implement strict numbered prompt cancellation

**Files:**
- Modify: `src/rgit/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Add the cancellation exception**

In `src/rgit/cli.py`, after `_InteractivePromptUnavailable`, add:

```python
class _GuidancePromptCancelled(Exception):
    pass
```

- [ ] **Step 2: Make the numbered prompt explicit-only**

Replace `_prompt_guidance_mode_numbered()` with:

```python
def _prompt_guidance_mode_numbered(platform: str) -> str:
    """Fallback picker that accepts 1/2/3 or mode names."""
    sys.stderr.write(
        f"\nresearch-git guidance for {platform} "
        "- how proactive should capture be?\n\n"
        "  1) default      consider capture after meaningful changes (recommended)\n"
        "  2) manual-only  only when you explicitly ask\n"
        "  3) none         install skills + MCP only, write no guidance\n\n"
        "Select [1-3]: "
    )
    choices = {"1": "default", "2": "manual-only", "3": "none",
               "default": "default", "manual-only": "manual-only", "none": "none"}
    while True:
        sys.stderr.flush()
        try:
            answer = input().strip().lower()
        except EOFError as e:
            raise _GuidancePromptCancelled from e
        if answer in choices:
            return choices[answer]
        sys.stderr.write("Please enter 1, 2, or 3: ")
```

- [ ] **Step 3: Run the targeted prompt tests**

Run:

```bash
python -m pytest tests/test_cli.py -k "guidance_numbered_prompt or prompt_guidance_mode" -v
```

Expected: prompt helper tests pass. Main install tests may still fail until Task 3.

- [ ] **Step 4: Commit the prompt helper change**

Run:

```bash
git add src/rgit/cli.py tests/test_cli.py
git commit -m "fix: require explicit guidance prompt selection"
```

## Task 3: Route non-TTY installs through the prompt

**Files:**
- Modify: `tests/test_cli.py`
- Modify: `src/rgit/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Replace the old non-TTY no-prompt test**

Replace:

```python
def test_cli_install_does_not_prompt_when_not_a_tty(
        tmp_path, monkeypatch, capsys):
    from rgit import agent_platforms, installer
    monkeypatch.setattr(agent_platforms, "home_dir", lambda: tmp_path)
    monkeypatch.setattr(installer, "_AGENTS_SKILLS_DIR",
                        tmp_path / ".agents" / "skills")
    monkeypatch.setattr(cli, "_stdin_is_tty", lambda: False)

    def explode(platform):
        raise AssertionError("must not prompt when stdin is not a TTY")

    monkeypatch.setattr(cli, "_prompt_guidance_mode", explode)

    assert cli.main(["install", "codex", "--dry-run"]) == 0

    res = json.loads(capsys.readouterr().out)
    assert "Current mode: default" in res["guidance"]["block"]
```

with:

```python
def test_cli_install_prompts_when_not_a_tty(
        tmp_path, monkeypatch, capsys):
    from rgit import agent_platforms, installer
    monkeypatch.setattr(agent_platforms, "home_dir", lambda: tmp_path)
    monkeypatch.setattr(installer, "_AGENTS_SKILLS_DIR",
                        tmp_path / ".agents" / "skills")
    monkeypatch.setattr(cli, "_stdin_is_tty", lambda: False)
    monkeypatch.setattr(cli, "_prompt_guidance_mode", lambda platform: "manual-only")

    assert cli.main(["install", "codex", "--dry-run"]) == 0

    res = json.loads(capsys.readouterr().out)
    assert "Current mode: manual-only" in res["guidance"]["block"]
```

- [ ] **Step 2: Add an integration-style stdin test for non-TTY numbered input**

Add near the install prompt tests:

```python
def test_cli_install_non_tty_numbered_input_selects_mode(
        tmp_path, monkeypatch, capsys):
    from rgit import agent_platforms, installer
    monkeypatch.setattr(agent_platforms, "home_dir", lambda: tmp_path)
    monkeypatch.setattr(installer, "_AGENTS_SKILLS_DIR",
                        tmp_path / ".agents" / "skills")
    monkeypatch.setattr(cli, "_stdin_is_tty", lambda: False)
    monkeypatch.setattr(cli, "_prompt_guidance_mode_interactive",
                        lambda platform: (_ for _ in ()).throw(
                            cli._InteractivePromptUnavailable))
    answers = iter(["2"])
    monkeypatch.setattr("builtins.input", lambda: next(answers))

    assert cli.main(["install", "codex", "--dry-run"]) == 0

    captured = capsys.readouterr()
    res = json.loads(captured.out)
    assert "Current mode: manual-only" in res["guidance"]["block"]
    assert "Select [1-3]" in captured.err
```

- [ ] **Step 3: Add a cancellation test for EOF/no-selection**

Add near `test_install_prompt_ctrl_c_exits_without_traceback`:

```python
def test_install_prompt_eof_cancels_without_running_installer(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_stdin_is_tty", lambda: False)
    monkeypatch.setattr(cli, "_prompt_guidance_mode",
                        lambda platform: (_ for _ in ()).throw(
                            cli._GuidancePromptCancelled))

    import rgit.installer as installer
    monkeypatch.setattr(installer, "install",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("install must not run after prompt cancellation")))

    assert cli.main(["install", "codex"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "install cancelled: no guidance mode selected" in captured.err
    assert "--guidance default" in captured.err
```

- [ ] **Step 4: Update `main()` to prompt regardless of TTY**

In `src/rgit/cli.py`, replace:

```python
        if mode is None and not args.uninstall and _stdin_is_tty():
            try:
                mode = _prompt_guidance_mode(args.platform)
            except KeyboardInterrupt:
                print("\ninstall cancelled", file=sys.stderr)
                return 130
```

with:

```python
        if mode is None and not args.uninstall:
            try:
                mode = _prompt_guidance_mode(args.platform)
            except KeyboardInterrupt:
                print("\ninstall cancelled", file=sys.stderr)
                return 130
            except _GuidancePromptCancelled:
                print("\ninstall cancelled: no guidance mode selected",
                      file=sys.stderr)
                print("pass --guidance default, --guidance manual-only, "
                      "or --guidance none", file=sys.stderr)
                return 1
```

- [ ] **Step 5: Remove `_stdin_is_tty()` if unused**

If `rg _stdin_is_tty src tests` shows it is only used by tests, delete this helper from `src/rgit/cli.py`:

```python
def _stdin_is_tty() -> bool:
    try:
        return sys.stdin.isatty()
    except Exception:
        return False
```

Then remove test monkeypatches that only set `_stdin_is_tty` and are no longer needed:

```python
monkeypatch.setattr(cli, "_stdin_is_tty", lambda: True)
monkeypatch.setattr(cli, "_stdin_is_tty", lambda: False)
```

Keep a monkeypatch only if the test still references the helper; ideally none should.

- [ ] **Step 6: Run the install prompt tests**

Run:

```bash
python -m pytest tests/test_cli.py -k "install and prompt or non_tty or guidance" -v
```

Expected: all selected install/prompt tests pass.

- [ ] **Step 7: Commit the install routing change**

Run:

```bash
git add src/rgit/cli.py tests/test_cli.py
git commit -m "fix: prompt for guidance in non-tty installs"
```

## Task 4: Full verification and cleanup

**Files:**
- Verify: `src/rgit/cli.py`
- Verify: `tests/test_cli.py`
- Verify: `docs/superpowers/specs/2026-07-04-non-tty-guidance-prompt-design.md`

- [ ] **Step 1: Run all CLI tests**

Run:

```bash
python -m pytest tests/test_cli.py -q
```

Expected: all `tests/test_cli.py` tests pass.

- [ ] **Step 2: Run the full test suite**

Run:

```bash
python -m pytest -q
```

Expected: full suite passes.

- [ ] **Step 3: Manually verify non-TTY valid input**

Run:

```bash
printf '2\n' | python -m rgit.cli install codex --dry-run
```

Expected:

- stderr includes the numbered prompt.
- stdout is JSON.
- JSON guidance block contains `Current mode: manual-only`.

- [ ] **Step 4: Manually verify EOF cancellation**

Run:

```bash
python -m rgit.cli install codex --dry-run < /dev/null
```

Expected:

- exit status is `1`;
- stderr includes `install cancelled: no guidance mode selected`;
- stdout is empty.

- [ ] **Step 5: Review git status**

Run:

```bash
git status -sb
```

Expected: only intentional branch commits plus the pre-existing untracked `.DS_Store`.

- [ ] **Step 6: Push the branch**

Run:

```bash
git push
```

Expected: `origin/issue-18-improve-non-tty-guidance-prompt` updates successfully.

## Self-Review

- Spec coverage: the plan covers non-TTY prompting, explicit numbered input, blank invalid, EOF cancellation, `Ctrl+C`, stdout/stderr separation, and `--guidance` bypass.
- Placeholder scan: no TBD/TODO/fill-in steps remain.
- Type consistency: `_GuidancePromptCancelled`, `_InteractivePromptUnavailable`, and prompt helper names match existing `src/rgit/cli.py` conventions.
