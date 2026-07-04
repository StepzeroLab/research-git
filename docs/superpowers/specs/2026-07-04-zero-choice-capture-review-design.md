# Zero-choice capture & review defaults

**Date:** 2026-07-04
**Status:** approved (maintainer picked this over top-level command consolidation)

## Problem

The pain is not the number of subcommands — it is the number of choices a caller must make per invocation. `rgit capture` grew four source/label flags (`--commit`, `--range`, `--worktree`, `--trigger`), so both humans and agents must first classify their situation (uncommitted? one commit? several? hook?) before they can type anything. `rgit review --approve` requires copying a proposal id even when there is only one open proposal. Guidance and skills currently teach a three-branch decision tree for capture; every branch an agent must evaluate is a chance to pick wrong.

## Principle

**Zero-argument correctness.** The bare command does the right thing in the common case; parameters exist only for rare, precise control. Explicit context-holders (the post-commit hook) stay explicit; humans and agents typing without context get automatic behavior.

## Design

### 1. `rgit capture` auto-selects its source

Source resolution precedence:

1. **Explicit positional** `rgit capture <SOURCE>` — `A..B`/`A...B` (contains `..`) → `RangeDiffSource`; anything else → `CommitDiffSource(ref)`.
2. **Legacy explicit flags** `--commit [REF]` / `--range A..B` / `--worktree` — keep parsing forever, hidden from `--help`. Giving both a positional and a source flag is an error.
3. **Hook rule** — `--trigger commit` with no explicit source still means `CommitDiffSource("HEAD")`. The hook knows its context: right after a partially staged commit the worktree is dirty with *leftovers*, and auto would wrongly capture those instead of the commit.
4. **Auto (new default)** — worktree diff non-empty → capture the worktree; worktree clean → capture `HEAD`'s commit diff. "Dirty" means `diff_since` produces output, i.e. tracked changes *or* untracked non-ignored files — an untracked scratch file counts as worktree work and wins over the last commit. When auto falls through to the commit, print one line naming it (`capturing last commit <short-sha> ("<subject>")`) so capturing someone else's just-pulled commit is visible and dismissable rather than silent.

Idempotence: the content-addressed dedup (open proposal with identical diff bytes → "already exists") makes repeated bare `rgit capture` safe in every mode.

Out of scope: unborn-HEAD repos (no commits yet) keep today's behavior; merge commits under auto report the existing "merge commits are not captured" reason.

### 2. `rgit review` actions work without an id when unambiguous

- `rgit review --approve` (no id) → exactly one open proposal: approve it (with optional `--name`/`--index` as today). Zero open: friendly message, exit 1. More than one: error listing `id  [trigger]  candidates` so the retry is a copy-paste, exit 1.
- `rgit review --dismiss` (no id) → same resolution rule.
- With an explicit id, behavior is unchanged.

Implementation: `--approve` / `--dismiss` become `nargs="?"` with a sentinel const; a shared `_sole_open_proposal(store)` helper resolves or raises with the listing message.

### 3. Teaching text collapses to the bare commands

- **Guidance block** (`agent_guidance.render_global_block`): replace the three-branch capture decision tree with one line — run `rgit capture`; it captures uncommitted work, or the last commit when the tree is clean; use `rgit capture A..B` for a specific span. Keep the skip-mechanical-changes line and the hook double-capture warning. Drop `--trigger manual` from all examples (it is the default).
- **`rgit-capture` SKILL.md step 1**: same collapse; mention the positional span form once.
- **README**: quick-start shows bare `rgit capture`; the after-commit paragraph shrinks to "same command"; `More commands` row for capture updated.
- **Runtime hints**: CLI-printed strings that agents read as teaching material (`capture`/`run`/`watch` "0 candidates" hints, `pending --json` references) stay accurate but are not otherwise expanded.

### 4. `rgit install` detects its platforms and speaks human

Modeled on the Understand-Anything installer: near-zero flags, auto-detect first, numbered prompt as fallback, human-readable output.

- **Bare `rgit install`** (no platform argument) auto-detects which agent clients exist on this machine and installs for every one it finds. Detection: `claude-code` if the `claude` binary is on PATH; `codex` if `~/.codex/` exists; `gemini` if `~/.gemini/` exists; `opencode` if the `opencode` binary is on PATH or `~/.config/opencode/` exists. `generic` is never auto-detected. Nothing detected → on a TTY, the numbered platform picker (same machinery as the guidance picker); non-TTY, the platform list and exit 1. Bare `--uninstall` is symmetric (every detected client). Explicit platform argument and `--list` behave as today.
- **Human output by default.** Install prints Understand-Anything-style progress lines (`✓ skills linked → ~/.agents/skills/rgit-capture`, the MCP wiring or the one paste-in line, the guidance file touched, then `restart your CLI to pick up the skills` and the opt-in `rgit install-hooks` nudge). A hidden `--json` flag prints today's JSON document unchanged for machines and tests. This flips the default stdout format — 0.0.x, and nothing documented ever promised the JSON shape.
- **Visible flags shrink to `--uninstall` and `--list`.** `--guidance`, `--scope`, `--dry-run`, and `--json` keep parsing forever but leave `--help` (`--dry-run` exists mainly as the test seam; `--scope` has a correct default; guidance is handled below).
- **Non-interactive guidance stops failing.** Today a non-TTY `rgit install <platform>` without `--guidance` exits 1 with instructions to re-run — an agent-driven install cannot succeed on the first try by design. Under zero-choice it proceeds: keep a previously pinned mode if the managed block has one, else write `default`, and print a one-line notice (`guidance mode: default — change with --guidance <mode>`). The TTY picker is unchanged. *This deliberately reverses part of PR #19's "require explicit selection" behavior; the picker remains for humans, but automation gets a working default instead of homework.*

## Non-goals

- No top-level command consolidation (`pending`, `resegment`, `install-hooks`, `compare`, `ablation`, `metric-dir` all stay as they are).
- No hook template change (v2 line `rgit capture --trigger commit --commit HEAD` remains; v1 recognized via the legacy set).
- No `features` → `capsules` rename.
- No new interactive prompts outside `install` on a TTY; every capture/review path stays agent-safe and non-interactive.
- No curl one-liner installer scripts this round (`install.sh` / `install.ps1` are a follow-up).

## Compatibility

Every currently documented invocation keeps parsing and behaving identically: source flags become hidden but permanent; `--trigger` keeps its label semantics; guidance blocks already installed in users' agent files keep working. `review --approve <id>` / `--dismiss <id>` are untouched. `pending --json` schema unchanged.

## Testing

TDD per behavior:

- auto: dirty tree → worktree capture; clean tree → HEAD capture + note line naming the sha; clean tree + merge HEAD → "not captured" reason; repeat capture → "already exists".
- precedence: positional beats trigger rule; trigger rule beats auto; positional + source flag → error; `--worktree` still beats the trigger rule.
- positional parsing: ref form, `A..B` form, bad ref error unchanged.
- review: no-id approve with one/zero/many open proposals; no-id dismiss; explicit-id paths untouched (existing tests are the regression net).
- install: detection unit tests (patched PATH lookups and home dirs); bare install covers every detected platform; zero detected + non-TTY → platform list, exit 1; non-TTY without `--guidance` succeeds with `default` and prints the notice; human output shows the ✓ lines and the hooks nudge; hidden `--json` output byte-compatible with today's document (existing JSON-asserting tests migrate to `--json`).
- guidance/README text assertions updated; `test_guidance_coupling` re-validates every taught command against the real parser.
