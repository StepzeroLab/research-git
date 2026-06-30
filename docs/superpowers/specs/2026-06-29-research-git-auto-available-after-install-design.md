# research-git agent default guidance — Design

**Status:** Approved in brainstorming; written for user review
**Date:** 2026-06-29
**Scope:** Installer/plugin UX. Make research-git feel available by default after install + agent restart, across Codex, Claude Code, Gemini CLI, and opencode.

---

## Problem

`rgit install <platform>` currently wires the plugin assets and MCP server, but it does not reliably teach a fresh agent session when to use research-git.

That leaves the first-use path too manual:

1. The user installs research-git.
2. The user restarts Codex / Claude Code / another agent.
3. The agent can technically see the skill or plugin.
4. But the agent may not proactively use it until the user mentions `research-git`, capsules, or explicitly calls a skill.

The desired product behavior is simpler: after install and restart, research-git should be a default capability. When the agent changes code, it should know that capture is available, when it is worth considering, and how to report what happened.

## Goals

- After `rgit install <platform>` and a new agent session, research-git is known as a default capability.
- The agent considers capture after meaningful code/research changes, without needing the user to `@` a skill the first time.
- Repo-level preferences override global defaults.
- Session/user instructions override both repo and global guidance.
- The first version covers Codex, Claude Code, Gemini CLI, and opencode. They do not need identical implementations, but each needs a clear adapter strategy.
- The installer remains safe, idempotent, dry-run friendly, and uninstallable.

## Non-goals

- No web dashboard.
- No local server.
- No editor extension.
- No structured preference file in v1.
- No automatic capture for every tiny mechanical edit.
- No hidden writes to repo guidance for one-off session preferences.

---

## 1. Product rule: research-git is default-on after install

The global guidance should not say:

> Only use research-git when the repo has `.rgit/` or when the user mentions capsules.

That framing is too passive. It makes research-git feel like a command the user must remember.

The global guidance should instead say:

> research-git is installed as a default agent capability. After code changes, consider whether a research-git capture is useful.

The agent should capture meaningful research/code ideas, experiments, failed attempts with useful findings, or reusable implementation decisions. It should skip purely mechanical changes like formatting, typo fixes, lockfile churn, and small edits with no reusable idea.

If the repo is not initialized, the agent should explain that `rgit init` is needed before capture can work. It should not silently initialize a repo unless the user asked for that.

## 2. Modes

Keep the mode model small.

| Mode | Meaning |
|---|---|
| `default` | After code changes, consider capture. Capture meaningful ideas; skip mechanical edits. |
| `manual-only` | Use research-git only when the user explicitly asks to capture, save, recall, resurrect, or bring back an idea. |
| `custom` | Inherit `default`, then apply repo-specific rules from the repo guidance file. |

There is no `off` mode in v1. If the user wants no proactive usage in a repo, record `manual-only`.

There is no `aggressive` mode in v1. If a repo wants stronger behavior, use `custom` with explicit rules.

If a repo has no research-git preference section, the current mode is `default`.

## 3. Preference priority

Priority is simple and explicit:

1. Current session/user instruction wins.
2. Repo-level guidance wins over global guidance.
3. Global guidance is the fallback.

This matters because the install-time global guidance should be broadly useful, but a repo may have different norms. For example, one repo might want docs-only changes captured if they describe experiment results; another might want docs-only changes skipped.

## 4. Repo preference recording

Repo preference recording is guidance, not a required CLI feature in v1.

The agent may update the repo guidance file only when the user clearly asks to remember a stable repo-level preference. Examples:

- "以后这个 repo 只手动 capture."
- "Remember for this repo: don't capture docs-only changes."
- "In this project, ask before approving capsules."

If the preference is ambiguous, the agent should ask before writing. Example:

> "我不太想每次都 capture"

This could mean session-only, repo-level `manual-only`, or a custom rule. The agent should clarify.

The agent must not persist one-off instructions. Example:

> "这次先别 capture"

That affects only the current session/task.

## 5. Guidance text model

The installer should write managed blocks, not free-form append-only text. This makes repeated install/uninstall safe.

Use markers:

```md
<!-- research-git:start -->
...
<!-- research-git:end -->
```

The global block should be short enough that it does not dominate the agent context.

Recommended global guidance content:

```md
## research-git

research-git is installed as a default agent capability.

Current mode: default

Mode options:
- `default`: After code changes, consider capture. Capture meaningful research/code ideas; skip mechanical changes.
- `manual-only`: Use research-git only when the user explicitly asks to capture, save, recall, resurrect, or bring back an idea.
- `custom`: Inherit `default`, then apply repo-specific rules from this repo.

Priority:
- Session/user instruction overrides repo and global guidance.
- Repo-level research-git preferences override global guidance.

Repo preference recording:
- If the user clearly asks to remember a stable repo-level research-git preference, update this repo's guidance file.
- For unclear preferences, ask first.
- Do not write repo preferences for one-off session instructions.

Use:
- After meaningful code/research changes, consider `rgit capture --trigger manual` and the `rgit-capture` skill.
- For recall/resurrection requests, use the `rgit-recall` skill.
- If `.rgit/` is missing, tell the user to run `rgit init`; do not initialize silently.
- In final feedback, mention any capsules created, approved, applied, or skipped, plus important graph relations.
```

Recommended repo override section, only when needed:

```md
## research-git

Current mode: custom

Rules:
- Do not capture docs-only changes.
- Ask before approving capsules.

User preferences:
- Capture failed experiments if they include useful findings.
```

## 6. Adapter strategy by agent

Each supported platform needs its own install behavior. The shared concept is "write a small managed guidance block to the platform's global instruction file", but the file and reload path differ.

| Platform | Current install behavior | v1 guidance target | Notes |
|---|---|---|---|
| Codex | Symlink skills into `~/.agents/skills`; print MCP config | `~/.codex/AGENTS.md` | Codex loads global + repo `AGENTS.md` at session start. New session/restart required. |
| Claude Code | Use official `claude plugin ...` and `claude mcp ...` commands | `~/.claude/CLAUDE.md` | Claude Code uses `CLAUDE.md` memory. Plugin changes may need `/reload-plugins` or restart. |
| Gemini CLI | Symlink skills into `~/.agents/skills`; print MCP config | `~/.gemini/GEMINI.md` | Gemini CLI uses `GEMINI.md` context files and user settings. New session required. |
| opencode | Symlink skills into `~/.agents/skills`; print MCP config | `~/.config/opencode/AGENTS.md` if present/supported, otherwise print fallback instructions | opencode supports `AGENTS.md` rules. Keep this adapter conservative and dry-run visible because global path support can vary by version/config. |
| generic | Symlink skills into `~/.agents/skills`; print MCP config | No automatic guidance write | Generic cannot safely know the host's global guidance file. Print the recommended block instead. |

The implementation should keep platform-specific paths in adapter helpers rather than scattering path logic through `installer.py`.

## 7. Installer behavior

`rgit install <platform>` should do three things when supported:

1. Install or link plugin/skill assets as it does today.
2. Provide MCP setup as it does today.
3. Add or update the research-git managed guidance block in the platform's global guidance file.

`--dry-run` should report:

- which skills/plugins would be installed,
- which MCP config would be used,
- which guidance file would be created or updated,
- the exact managed block that would be written.

`--uninstall` should:

- remove only research-git-owned skill links/plugin wiring,
- remove only the managed guidance block,
- leave all user-authored text untouched.

If a global guidance file exists and contains no managed block, installation appends the managed block. If it already contains the managed block, installation replaces only that block. If the file does not exist, installation creates it with the managed block.

If the file cannot be written, installation should not fail the whole platform setup after skills/plugins are installed. It should return a status field like:

```json
{
  "guidance": {
    "action": "skipped_error",
    "path": "...",
    "error": "..."
  }
}
```

The final CLI output should make the recovery step clear.

## 8. Final feedback contract

The guidance should teach agents to mention research-git activity in final feedback when it happened or was intentionally skipped.

Examples:

- "research-git: created capsules `entropy-loss` and `temperature-schedule`; added `depends_on` from `temperature-schedule` to `entropy-loss`."
- "research-git: skipped capture because this was a formatting-only change."
- "research-git: capture available, but `.rgit/` is missing. Run `rgit init` if you want this repo tracked."

The user should not need to inspect CLI output to know whether capsules were created or how they relate.

## 9. Component map

| File | New/Edit | Responsibility |
|---|---|---|
| `src/rgit/installer.py` | edit | Call platform adapters, include guidance status in install/uninstall/dry-run results. |
| `src/rgit/agent_guidance.py` | new | Managed-block helpers: render guidance, insert/update/remove block, classify file state. |
| `src/rgit/agent_platforms.py` | new | Platform-specific global guidance paths and reload notes. |
| `tests/test_installer.py` | edit | Cover install/uninstall/dry-run guidance status per platform. |
| `tests/test_agent_guidance.py` | new | Unit tests for block rendering, append, replace, remove, no clobber. |
| `README.md` | edit | Explain restart/new-session behavior and platform-specific guidance files. |

The exact module names can change during planning, but the boundary should stay: one module for safe managed-block editing, one module for platform facts, and `installer.py` as orchestration.

## 10. Data flow

```
rgit install codex
  cli → installer.install("codex")
        ├─ existing skill symlink install
        ├─ existing MCP config result
        ├─ agent_platforms.guidance_target("codex") → ~/.codex/AGENTS.md
        ├─ agent_guidance.upsert_managed_block(path, render_global_block("codex"))
        └─ print JSON result with guidance status + restart note

rgit install codex --dry-run
  cli → installer.install("codex", dry_run=True)
        └─ return planned skill links + MCP config + planned guidance write; write nothing

rgit install codex --uninstall
  cli → installer.uninstall("codex")
        ├─ existing skill symlink removal
        ├─ agent_guidance.remove_managed_block(~/.codex/AGENTS.md)
        └─ leave non-research-git text intact
```

## 11. Error handling

- Unknown platform: keep the current `ValueError` behavior with known platform list.
- Guidance file missing: create it on install.
- Parent directory missing: create it on install when the platform path is known.
- Existing managed block: replace it, do not append a duplicate.
- Existing user text: preserve byte-for-byte except for insertion/removal of the managed block.
- Permission error: return a structured guidance error; do not hide it.
- Uninstall with no managed block: report `absent`; do not treat as failure.
- Generic platform: do not guess a global file; print instructions only.

## 12. Testing

Use TDD and `.venv/bin/pytest`.

- `tests/test_agent_guidance.py`
  - render global block includes `Current mode: default`;
  - append block to existing file without changing existing text;
  - replace existing managed block without duplication;
  - remove only managed block;
  - uninstall reports `absent` when no block exists;
  - dry-run writes nothing.
- `tests/test_installer.py`
  - Codex dry-run reports `~/.codex/AGENTS.md`;
  - Claude Code dry-run reports `~/.claude/CLAUDE.md`;
  - Gemini dry-run reports `~/.gemini/GEMINI.md`;
  - opencode dry-run reports its guidance target or conservative fallback;
  - generic dry-run prints guidance text but does not plan a write;
  - uninstall removes only research-git-managed guidance;
  - existing skill install tests still pass.
- README/doc check:
  - install docs mention new session/restart requirement;
  - docs mention repo guidance overrides global guidance.

## 13. Open questions

These are intentionally left as product follow-ups, not blockers for v1:

- Should a future `rgit config agent-mode <mode>` write repo preferences for users who prefer CLI over editing guidance files?
- Should future repo preferences move to a structured file such as `.rgit/agent-preferences.json`, with `AGENTS.md` / `CLAUDE.md` as human-readable summaries?
- Should install expose `--no-guidance` for users who want skills/MCP only?
- Should opencode's global guidance path be version-detected before writing, rather than using one fixed path?

## 14. Why no parameter file in v1

A parameter file sounds cleaner because it is machine-readable, but it adds a second source of truth before there is a real reader for it.

For v1, the agent needs guidance, not a config API. The simplest useful design is:

- global guidance for default behavior,
- repo guidance for overrides,
- managed blocks for safe install/uninstall.

If later the CLI, a viewer, or an editor UI needs to read/write preferences directly, then add `.rgit/agent-preferences.json` as the source of truth and let guidance files point to it. That should be a separate design, because it changes ownership and migration behavior.

## 15. References

- Codex `AGENTS.md` global/project guidance: https://developers.openai.com/codex/guides/agents-md
- Claude Code memory and plugins: https://code.claude.com/docs/en/memory.md and https://code.claude.com/docs/en/plugins.md
- Gemini CLI `GEMINI.md`: https://google-gemini.github.io/gemini-cli/docs/cli/gemini-md.html
- opencode rules: https://opencode.ai/docs/rules/
