# Non-TTY guidance prompt for agent installs — Design

## Context

Issue #18 tracks a gap in the `rgit install <platform>` guidance-mode prompt.
Issue #16 added a better interactive picker for capable terminals and kept the
old numbered prompt as a fallback, but non-TTY installs still skip prompting
entirely.

Today, when `--guidance` is omitted and stdin is not a TTY, `main()` does not
call the prompt path. It leaves `mode=None`, so the installer proceeds with the
implicit "preserve an existing managed mode, otherwise default" behavior. That
keeps automation from hanging, but it is too implicit for agent-led setup: the
agent never sees the available choices and cannot make an intentional selection.

## Goals

- Make non-TTY installs explicit instead of silently continuing with
  `mode=None`.
- Let agent command runners and other non-TTY callers select a guidance mode by
  sending `1`, `2`, `3`, or the mode name on stdin.
- Preserve the arrow-key selector for capable interactive terminals.
- Preserve the numbered prompt as the fallback for terminals that cannot run the
  selector.
- Keep `--guidance default|manual-only|none` as the fully non-interactive path.
- Keep stdout reserved for the JSON install result; prompt UI and errors write
  to stderr.

## Non-goals

- Do not add a dependency such as `questionary`, `prompt_toolkit`, or curses.
- Do not change the set of guidance modes.
- Do not change installer behavior when `--guidance` is provided.
- Do not make blank input mean `default`.
- Do not treat EOF as an implicit guidance choice.
- Do not change `rgit install --uninstall` behavior.

## User Experience

When `rgit install <platform>` runs without `--guidance`, it must ask for an
explicit guidance mode.

In a capable TTY, the existing arrow-key selector remains the preferred UI:

```text
research-git guidance for codex - how proactive should capture be?

> default      consider capture after meaningful changes (recommended)
  manual-only  only when you explicitly ask
  none         install skills + MCP only, write no guidance

Use ↑/↓ to move, Enter to select.
```

If the selector is unavailable, including in non-TTY execution, use the plain
numbered prompt:

```text
research-git guidance for codex - how proactive should capture be?

  1) default      consider capture after meaningful changes (recommended)
  2) manual-only  only when you explicitly ask
  3) none         install skills + MCP only, write no guidance

Select [1-3]:
```

The numbered prompt accepts either numbers or mode names:

- `1` or `default`
- `2` or `manual-only`
- `3` or `none`

Blank input is invalid and prompts again. EOF means no explicit selection was
made, so the install is cancelled instead of defaulting.

## Architecture

Keep this change inside `src/rgit/cli.py`, near the existing prompt helpers.
The feature is small and tightly coupled to the `install` subcommand, so it does
not need a new module.

The current helper boundary remains useful:

```python
def _prompt_guidance_mode(platform: str) -> str:
    try:
        return _prompt_guidance_mode_interactive(platform)
    except _InteractivePromptUnavailable:
        return _prompt_guidance_mode_numbered(platform)
```

The main behavior change is the call site in `main()`. Instead of prompting only
when `_stdin_is_tty()` is true, prompt whenever all of these are true:

- `args.guidance is None`
- `not args.uninstall`
- an install platform was provided

The interactive helper still performs capability detection and raises
`_InteractivePromptUnavailable` when the selector cannot run. The unified
prompt entrypoint then falls back to numbered input.

`_stdin_is_tty()` can be removed if no longer used.

## Numbered Prompt Semantics

`_prompt_guidance_mode_numbered()` becomes a strict explicit-choice prompt:

- valid input returns the selected mode;
- blank input is rejected and the prompt repeats;
- invalid input is rejected and the prompt repeats;
- `EOFError` raises a cancellation signal instead of returning `default`.

Use a small internal exception such as `_GuidancePromptCancelled` to distinguish
EOF/no-selection from `KeyboardInterrupt`.

The prompt should continue to write only to stderr. That keeps this contract
valid:

```bash
rgit install codex --guidance default > install.json
rgit install codex 2> prompt.txt > install.json
```

If the user or agent provides a valid selection, stdout contains only the final
JSON install result.

## Error Handling

`Ctrl+C` keeps the current behavior:

```text
install cancelled
```

and exits with status `130`.

EOF/no-selection prints a clear cancellation message and exits non-zero:

```text
install cancelled: no guidance mode selected
pass --guidance default, --guidance manual-only, or --guidance none
```

Use exit status `1` for EOF/no-selection. The command should not call the
installer after this cancellation.

The fallback should not print warnings like "selector unavailable"; it is a
normal path.

## Data Flow

The resulting install flow is:

1. Parse arguments.
2. If `--guidance` is present, use it and skip all prompts.
3. If installing and `--guidance` is omitted, call `_prompt_guidance_mode()`.
4. `_prompt_guidance_mode_interactive()` handles capable TTYs.
5. `_prompt_guidance_mode_numbered()` handles selector fallback and non-TTY
   input.
6. If a mode is selected, pass it to `installer.install()`.
7. If prompt cancellation occurs, print the cancellation message and return
   without calling the installer.
8. Print installer result JSON to stdout.

## Tests

Add or update focused tests in `tests/test_cli.py`:

- explicit `--guidance` bypasses all prompt helpers;
- non-TTY install without `--guidance` calls the numbered prompt;
- non-TTY input `2` selects `manual-only`;
- numbered prompt accepts `1`, `2`, `3`, `default`, `manual-only`, and `none`;
- numbered prompt rejects blank input and retries;
- numbered prompt rejects invalid input and retries;
- numbered prompt EOF raises cancellation and does not default;
- `main()` returns `1` and does not call the installer when prompt cancellation
  happens;
- `KeyboardInterrupt` still returns `130` and does not traceback;
- prompt text remains on stderr and stdout remains parseable JSON after a valid
  selection;
- arrow-key selector tests from issue #16 remain valid.

Tests should monkeypatch `input()`, prompt helpers, and installer calls rather
than depending on the host terminal.

## Compatibility

This changes behavior for scripts that run `rgit install <platform>` without
`--guidance` and without providing stdin. Those scripts will now fail with a
clear message instead of silently installing with implicit default/preserve
semantics.

That is intentional for #18: a guidance mode is a user-facing policy choice,
and non-TTY callers should either provide `--guidance` or send an explicit
numbered answer.

Scripts that need fully unattended installation should use:

```bash
rgit install codex --guidance default
```

or another explicit mode.

## Open Decisions

None. The agreed behavior is:

- non-TTY installs without `--guidance` use the numbered prompt;
- numbered input accepts both numbers and mode names;
- blank input is invalid;
- EOF cancels install with exit status `1`;
- `Ctrl+C` cancels install with exit status `130`;
- stdout remains JSON-only.
