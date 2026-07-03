# `rgit install` guidance mode picker — Design

## Context

`rgit install <platform>` currently asks interactive users to choose a guidance
mode by typing `1`, `2`, or `3`. The behavior is reliable, script-friendly, and
keeps install results as JSON on stdout, but the first-run terminal experience
feels rough compared with modern CLI selectors.

Issue #16 tracks improving this prompt with arrow-key selection while avoiding a
new runtime dependency such as `questionary`.

## Goals

- Make the interactive guidance-mode choice feel like a small terminal picker:
  move with up/down, confirm with Enter.
- Keep the existing numbered prompt as the fallback path.
- Keep `--guidance default|manual-only|none` unchanged for scripts and docs.
- Keep non-interactive installs from blocking.
- Keep stdout reserved for the JSON install result; prompt UI writes to stderr.
- Avoid new package dependencies and avoid a larger module split for this small
  feature.

## Non-goals

- Do not add `questionary`, `prompt_toolkit`, curses, or another runtime
  dependency.
- Do not create a reusable TUI framework.
- Do not change guidance modes, managed guidance semantics, installer output
  shape, or platform install behavior.
- Do not support canceling with Escape or `q` in this version.

## User Experience

When `rgit install <platform>` runs in an interactive terminal and `--guidance`
is omitted, show a selector like:

```text
research-git guidance for codex - how proactive should capture be?

> default      consider capture after meaningful changes (recommended)
  manual-only  only when you explicitly ask
  none         install skills + MCP only, write no guidance

Use ↑/↓ to move, Enter to select.
```

The default highlighted choice is `default`, so pressing Enter accepts the
recommended mode. Users may also press `1`, `2`, or `3` to select directly:

1. `default`
2. `manual-only`
3. `none`

`Ctrl+C` exits the command after restoring terminal state. Escape and `q` are
ordinary unsupported keys and do not cancel.

## Architecture

Keep the implementation in `src/rgit/cli.py`. This feature is narrow and is
only used by the `install` subcommand, so a separate `terminal_ui.py` module is
not necessary yet.

Split the current `_prompt_guidance_mode()` path into small private helpers:

```python
def _prompt_guidance_mode(platform: str) -> str:
    try:
        return _prompt_guidance_mode_interactive(platform)
    except _InteractivePromptUnavailable:
        return _prompt_guidance_mode_numbered(platform)

def _prompt_guidance_mode_interactive(platform: str) -> str:
    ...

def _prompt_guidance_mode_numbered(platform: str) -> str:
    ...

def _read_prompt_key() -> str:
    ...
```

The existing numbered prompt logic moves into
`_prompt_guidance_mode_numbered()`. `_prompt_guidance_mode()` remains the
single call site used by `main()`.

## Terminal Backends

Implement two standard-library key-reading backends:

- POSIX/macOS/Linux: `termios`, `tty`, `select`, and `sys.stdin.fileno()`.
- Windows: `msvcrt.getwch()`.

The backend returns normalized key names such as:

- `"up"`
- `"down"`
- `"enter"`
- `"1"`
- `"2"`
- `"3"`
- `"ctrl-c"`
- `"other"`

POSIX raw mode must always be restored in a `finally` block. Windows does not
need the same raw-mode restoration, but `Ctrl+C` must still behave consistently.

If the platform-specific backend cannot be initialized or cannot safely read
keys, it raises `_InteractivePromptUnavailable` and the caller falls back to the
numbered prompt.

## Fallback Rules

The old `1`/`2`/`3` prompt is retained as the fallback. It is used when:

- stdin is not a TTY;
- stderr is not a TTY;
- `TERM=dumb`;
- file descriptor access fails;
- POSIX raw mode setup fails;
- Windows key reading is unavailable;
- any unexpected terminal-control exception occurs before a selection is made.

Fallback is silent. Do not print an extra warning such as "selector unavailable";
the user only needs a working prompt.

Non-interactive behavior remains unchanged. If `main()` determines that stdin
is not a TTY, it does not call `_prompt_guidance_mode()` and keeps the existing
default/install behavior.

## Rendering

Render the selector only to stderr. Keep stdout clean so this remains valid:

```bash
rgit install codex > install.json
```

Use simple ANSI cursor control for redraws when the interactive picker is active.
The first render writes the full prompt. Later renders clear/redraw the selector
block. If ANSI control is not safe, the picker should not run and should fall
back to numbered input.

Keep copy close to the existing prompt so this is an interaction upgrade, not a
content rewrite.

## Error Handling

- `Ctrl+C`: restore terminal state and raise `KeyboardInterrupt`, letting the
  command exit instead of silently choosing a mode.
- EOF in the numbered fallback: return `default`, matching current behavior.
- Unknown keys in the picker: ignore them and keep waiting.
- Backend setup/read exceptions: restore terminal state, then silently fall back
  to the numbered prompt.

## Tests

Add focused tests around the prompt helpers rather than trying to drive a real
terminal:

- explicit `--guidance` bypasses prompting;
- non-TTY install behavior does not prompt;
- numbered fallback still accepts `1`, `2`, `3`, mode names, and blank/default;
- picker starts on `default`;
- picker handles down/up navigation and Enter;
- picker accepts numeric shortcuts;
- picker turns `Ctrl+C` into command interruption and restores state;
- backend-unavailable path calls numbered fallback without warning;
- stdout remains JSON-only for install output.

Tests should monkeypatch key readers and stream objects instead of depending on
the host terminal.

## Packaging Impact

No new runtime dependency is added. The wheel size increase should be limited to
the small amount of Python code added to `cli.py`; expected impact is only a few
kilobytes.

## Open Decisions

None. The agreed behavior is:

- implement POSIX and Windows backends;
- keep numbered input as fallback;
- default selection is `default`;
- support up/down, Enter, and `1`/`2`/`3`;
- do not support Escape or `q` cancellation;
- `Ctrl+C` exits;
- fallback does not print a warning.
