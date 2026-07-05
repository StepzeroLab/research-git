---
name: rgit-capture
description: Use when the research-git proposal queue is non-empty (`rgit pending`) — after an `rgit run`, a commit, or the watch daemon leaves open proposals, or when the user wants to segment, capture, or save recent changes. Invoke proactively without being asked; unsegmented proposals cannot be recalled or queried.
---

# rgit-capture

Orchestrates the two-phase capture: a free, deterministic Phase 1 through the `rgit` CLI, then an agentic Phase 2 dispatched natively onto the host session's subscription — no paid API.

**Prerequisites:** the target repo has been `rgit init`-ed.

**Locating the agent definitions.** On Claude Code the plugin runtime resolves agent paths for you. On other CLIs (Codex, Gemini, opencode) this skill is symlinked into `~/.agents/skills/rgit-capture`, so resolve the plugin root once and reference the agents from there:

```bash
SKILL_REAL=$(realpath ~/.agents/skills/rgit-capture 2>/dev/null || readlink -f ~/.agents/skills/rgit-capture)
PLUGIN_ROOT=$(dirname "$(dirname "$SKILL_REAL")")    # the bundled _plugin/ directory
```

Every `agents/<name>.md` reference below (`agents/capsule-segmenter.md`, `agents/edge-judge.md`) lives at `$PLUGIN_ROOT/agents/<name>.md`.

## Process

### 1. Ensure there are proposals to segment (Phase 1 — free, deterministic)

If the user just made changes and there is no open proposal yet:

```
rgit capture                 # picks for you: uncommitted work, or the last commit when the tree is clean
rgit capture main..HEAD      # a specific span of commits (any A..B range)
```

Repeated captures of the same diff dedup into the existing proposal, and repos with the post-commit hook (`rgit install-hooks`) capture each commit automatically — don't capture the same commit twice. Proposals also appear from `rgit run` and the `rgit watch` daemon.

### 2. Read the pending captures

Run `rgit pending --json` → a list of `{proposal_id, trigger, diff, candidates}`. The `diff` is the raw material; the `candidates` are crude heuristic guesses you are about to replace. If the list is empty, tell the user there is nothing to segment and stop.

### 3. Dispatch the capsule-segmenter subagent (Phase 2 — agentic, on subscription)

For each pending proposal, dispatch a subagent using the **`capsule-segmenter`** agent definition (`agents/capsule-segmenter.md`); run independent proposals concurrently. Pass in the dispatch prompt: `proposal_id`, `repo_root` (absolute path of the target repo), `diff` (verbatim from `rgit pending`), and `symbols` if available. The subagent returns `{"capsules": [...], "dropped": [...]}` — high-quality capsules with real `intent` / `knobs` / `data_assumptions` / `resurrection_guide`, infrastructure noise dropped.

### 4. Write the capsules back

For each proposal, pipe the subagent's `capsules` array back through the CLI:

```
echo '<capsules-json-array>' | rgit resegment <proposal_id> --from-json -
```

This replaces the crude heuristic candidates with the agent-quality ones. Do NOT approve anything yet.

### 5. Review with the user (you run the commands; the user decides)

Approval is human-gated, but the human only decides — never make them type `rgit` commands or copy ids.

1. Show each proposal's capsules: name + one-line intent (+ key knobs if they matter).
2. Ask which capsules to keep — use the client's structured multi-select question UI if it has one, otherwise ask in plain conversation. **Always ask, even when there is a single capsule. Never auto-approve.**
3. Execute the decision yourself, one command per proposal:

```
rgit review --decide <proposal_id> --keep <name>[,<name>...]   # approves these, drops the rest
rgit review --dismiss <proposal_id>                            # the user kept nothing
```

Lost the ids from step 2? Bare `rgit review` re-lists every open proposal with its candidate names.

4. Echo the `approved -> <feature_id>` lines back to the user, then continue to step 6.

### 6. Infer graph edges (deterministic baseline + agent-judged relationships)

After approval, wire the new capsules into the graph:

```
rgit edges --apply
```

This writes a neutral `overlaps` baseline edge between capsules touching the same file+symbol and prints `overlap_pairs` (just connected) plus `depends_candidates` (over-produced `{src, dst, evidence}` hypotheses from name overlap). `overlaps` only says "same region" — it does not mean conflict.

Dispatch the **`edge-judge`** subagent (`agents/edge-judge.md`) once, passing both lists plus the referenced capsules' names/intents/slices. It returns confirmed `depends_on` edges and, per overlap pair, a precise relationship: `alternative_to`, `composable_with`, `supersedes` (directed), `conflicts_with`, or "leave as overlaps". Write each result:

```
rgit edges --add depends_on <src> <dst>          # confirmed dependency
rgit edges --add alternative_to <a> <b>          # symmetric: write BOTH directions
rgit edges --add alternative_to <b> <a>
rgit edges --add supersedes <newer> <older>      # directed: one line
```

Pairs the judge leaves unclassified keep their neutral `overlaps` baseline — the graph renderer hides it once a richer edge exists, so delete nothing. Reject coincidental overlaps: a missing edge is cheaper than a wrong one.

## Notes

- **Sibling flow:** recalling a capsule and regenerating it onto today's code is the `rgit-recall` skill, driven by the `capsule-regenerator` agent.
