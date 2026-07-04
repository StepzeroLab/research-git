# Runtime update check & `rgit update` — design

Date: 2026-07-04
Status: approved pending final review

## Problem

Users have no way to learn that a newer research-git exists, and upgrading is a
two-step manual chore that is easy to half-do: `pip install -U research-git`
refreshes the CLI and the symlinked agent-CLI skills, but the Claude Code
plugin copy and the managed guidance blocks in AGENTS.md / CLAUDE.md stay
stale until `rgit install` is re-run. Most users never re-run it.

## Goals

- Detect a newer PyPI release at runtime and tell the user, with zero impact
  on command latency and zero errors when offline.
- One memorable command, `rgit update`, that upgrades the package **and**
  refreshes every installed platform surface (Claude Code plugin, MCP config,
  guidance blocks).
- A permanent opt-out.
- Never clobber user customizations in guidance files; never re-add a block
  the user deliberately removed.

## Non-goals

- No self-updating binary logic (we delegate to pip/pipx/uv).
- No update UI in agent contexts (MCP server, hooks): checks and notices are
  fully suppressed there.
- No auto-update without user action (user chose notify + one command).

## Command surface (complete)

- `rgit update` — upgrade the package, then refresh installed platforms.
- `rgit update --off` / `rgit update --on` — permanently disable/enable the
  update notice. Env `RGIT_UPDATE_CHECK=0` does the same non-persistently.

Nothing else. Recovery paths reuse existing commands (`rgit install`), and
hint messages name the exact command when relevant — nothing to memorize.

## Architecture

### 1. Background check — new module `src/rgit/updatecheck.py`

State file: `~/.rgit/update-check.json` (new user-level dir, distinct from a
repo's `.rgit` store): `{last_check, latest_version, disabled}`. Corrupted
JSON is treated as absent and rewritten (self-healing). Writes are atomic
(tmp + replace, same pattern as `agent_guidance._atomic_write`).

Trigger, evaluated once at CLI entry; ALL must hold:

- more than 24 h since `last_check`
- not disabled (state file flag or `RGIT_UPDATE_CHECK=0`)
- stdout is a TTY and the invocation is not `--json` (this alone silences all
  agent/hook/pipe contexts, whose stdout is never a TTY)
- command is not `mcp` (defense-in-depth on top of the TTY check)

When triggered, a **daemon thread** fetches
`https://pypi.org/pypi/research-git/json` with a 2 s timeout and writes the
cache on success; every failure (network, HTTP, JSON) is silently dropped.
The notice is rendered only from the **previously cached** result and appended
as one line after normal command output:

    research-git 0.0.5 available (you have 0.0.4) — run `rgit update`

So the in-flight check never delays or garbles the current command; the user
sees the notice on the next qualifying run. Version comparison uses
`packaging.version` if importable, else a tolerant numeric-tuple fallback
(never raises on weird versions; incomparable means "no notice").

### 2. `rgit update` pipeline

1. **Detect installer** from `sys.prefix` path markers:
   uv tools dir → `uv tool upgrade research-git`;
   pipx venvs dir → `pipx upgrade research-git`;
   otherwise → `sys.executable -m pip install -U research-git`.
2. **Run the upgrade** as a subprocess, streaming output. The command's exit
   code reflects this step only.
3. **Refresh platforms** — in a **fresh subprocess**: `rgit install` for each
   `detect_platforms()` result (`generic` is never returned by detection, so
   it is never auto-refreshed). A fresh process is mandatory: the running process still has pre-upgrade code and
   assets in memory; only a new interpreter picks up the new plugin files and
   guidance text. The refresh passes a flag (`--from-update`, hidden) so
   guidance handling uses the conservative policy in §3.
4. **Report**: upgraded version, per-platform refresh outcome, any hints.

Platform notes:

- **Windows exe lock**: pip may fail to replace a running `rgit.exe`
  (deleting a running exe is denied on Windows; POSIX allows unlink). v1
  surfaces the error verbatim plus one manual line:
  `python -m pip install -U research-git` (a python.exe process does not lock
  rgit.exe). A detached-child self-update (npm-style) is future work.
- **PEP 668 externally-managed environments** (Debian/Homebrew system
  Python): pip refuses to install. Detect the marker in pip's stderr and
  suggest `uv tool install research-git`. Never pass
  `--break-system-packages`.
- macOS/Linux venv/conda/pipx/uv: straightforward delegation.

### 3. Conservative guidance-block update (in `agent_guidance.py`)

Current behavior (kept for explicit `rgit install`): markers present →
replace block (carrying pinned mode); markers absent → append. Explicit
install is explicit consent.

The update path (`--from-update`) must not trust that consent. New mechanics:

- **Fingerprint**: START marker becomes
  `<!-- research-git:start h=<12-hex sha256> -->`, hashing the canonical
  block body (mode line excluded) at write time. `_managed_span` accepts both
  the old bare marker and the fingerprinted one.
- **Historical hashes**: a small in-code table of the official block bodies
  shipped in 0.0.1–0.0.4 (extracted from git history) classifies legacy
  blocks that predate fingerprinting.

Per guidance file, four cases:

| Found | Classification | Action |
|---|---|---|
| Block present, hash matches fingerprint or history | pristine official block | replace quietly (mode carried) |
| Block present, hash mismatch | user-customized | skip; hint: "customized research-git block in <file> left untouched — run `rgit install` to overwrite with the new official version" |
| No markers, file exists | possibly deliberately removed | never append; hint once ("run `rgit install` to restore"), record shown-flag in `~/.rgit` so it is not repeated |
| One marker of the pair | broken span | never touch; warn with file/line and ask for manual fix |

### 4. Error-handling principles

Everything degrades to a skipped step or a one-line hint. Network failures,
rate limits, unwritable guidance files, corrupted caches: never a traceback,
never a nonzero exit for anything except the package-upgrade step itself.

## Testing (developer-side pytest; no user-facing surface)

- `updatecheck`: TTL gating, disable via flag/env, TTY/`--json` suppression,
  corrupted-cache self-heal, notice rendering — network mocked throughout.
- `update` command: installer detection and command composition for the three
  paths (subprocess mocked / dry-run asserted; nothing actually installed);
  PEP 668 and exe-lock error paths produce the right hints.
- Guidance quadrants: pristine-replace, customized-skip, removed-no-append
  (hint shown exactly once), broken-markers-warn; legacy unfingerprinted
  block recognized via historical hash; old bare START marker still parsed.

## Future work

- Detached-child self-update on Windows (upgrade after parent exit).
- Changelog excerpt in the update notice.
