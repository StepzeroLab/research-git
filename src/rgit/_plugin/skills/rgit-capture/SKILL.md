---
name: rgit-capture
description: |
  Proactively segment pending research-git captures into high-quality Feature Capsules and wire up their graph edges — invoke this on your own, without waiting to be asked, whenever the research-git proposal queue is non-empty (a quick `rgit pending` confirms). Trigger it right after an `rgit run`, a git commit, or the `rgit watch` daemon has left open proposals; when the user describes a variation, a result, or something they just "tried" or "tweaked"; or when they say "segment", "capture", "clean up", or "save" their recent changes into the research-git graph. Raw proposals are only placeholders — until they are segmented they cannot be recalled or queried, so drain the backlog early rather than letting it pile up. Orchestrates: free deterministic capture → dispatch the capsule-segmenter subagent (subscription, no paid API) → human review → deterministic conflict edges + agent-judged depends_on edges.
---

# rgit-capture

Orchestrates the **two-phase capture** that the research-git design calls for: a free deterministic Phase 1, then an agentic Phase 2 dispatched natively onto the host session's subscription. No pay-per-use API is ever called. The `rgit` CLI is the deterministic engine and the read/write surface; MCP is query-only and is not used here.

**Prerequisites:** the target repo has been `rgit init`-ed. Everything below runs through the `rgit` CLI.

**Locating the agent definitions.** On Claude Code the plugin runtime resolves agent paths for you. On other CLIs (Codex, Gemini, opencode) this skill is symlinked into `~/.agents/skills/rgit-capture`, so resolve the plugin root once and reference the agents from there:

```bash
SKILL_REAL=$(realpath ~/.agents/skills/rgit-capture 2>/dev/null || readlink -f ~/.agents/skills/rgit-capture)
PLUGIN_ROOT=$(dirname "$(dirname "$SKILL_REAL")")    # the bundled _plugin/ directory
```

Every `agents/<name>.md` reference below (`agents/capsule-segmenter.md`, `agents/edge-judge.md`) lives at `$PLUGIN_ROOT/agents/<name>.md`.

## Process

### 1. Ensure there are proposals to segment (Phase 1 — free, deterministic)

If the user just made changes and there is no open proposal yet, create one:

```
rgit capture --trigger manual        # uncommitted work (worktree vs HEAD)
rgit capture --commit HEAD           # work that was already committed
rgit capture --range A..B            # several commits at once (e.g. main..HEAD)
```

Pick the source that matches where the work lives: after a `git commit` the working tree is clean, so the plain form finds nothing — use `--commit HEAD` (or `--range`) to capture from history. If the repo has the post-commit hook installed (`rgit install-hooks`), each commit is captured automatically; don't capture the same commit twice.

This runs the libcst symbol mapping + the free heuristic, producing one or more open proposals with a raw diff and a crude candidate. Proposals also appear automatically from `rgit run`, the post-commit hook, and the `rgit watch` daemon.

### 2. Read the pending captures

Run `rgit pending --json`. You get a list of `{proposal_id, trigger, diff, candidates}`. The `diff` is the raw material; the `candidates` are the crude heuristic guesses you are about to replace. If the list is empty, tell the user there is nothing to segment and stop.

### 3. Dispatch the capsule-segmenter subagent (Phase 2 — agentic, on subscription)

For each pending proposal, dispatch a subagent using the **`capsule-segmenter`** agent definition (`agents/capsule-segmenter.md`). Run independent proposals concurrently. Pass in the dispatch prompt: `proposal_id`, `repo_root` (absolute path of the target repo), `diff` (verbatim from `rgit pending`), and `symbols` if available (otherwise the subagent infers from the diff). The subagent returns `{"capsules": [...], "dropped": [...]}` — high-quality capsules with real `intent` / `knobs` / `data_assumptions` / `resurrection_guide`, infrastructure noise dropped.

### 4. Write the capsules back

For each proposal, write the subagent's `capsules` array back through the CLI. Pipe the JSON to stdin:

```
echo '<capsules-json-array>' | rgit resegment <proposal_id> --from-json -
```

This replaces the crude heuristic candidates with the agent-quality ones. Do NOT auto-approve — capture stays human-gated.

### 5. Hand back for review

Show the user a short summary (one line per capsule: name + intent), then tell them to approve the ones they want:

```
rgit review                          # list open proposals
rgit review --approve <proposal_id> --name <name>
```

On approval the capsule lands in the graph with its `produced` edge to the run.

### 6. Infer graph edges (deterministic baseline + agent-judged relationships)

After approval, wire the new capsule into the graph:

```
rgit edges --apply
```

This deterministically writes a neutral **`overlaps`** baseline edge between capsules touching the same file+symbol, and prints JSON with `overlap_pairs` (the `{a, b}` pairs it just connected) and `depends_candidates` (over-produced `{src, dst, evidence}` hypotheses from name overlap).

`overlaps` only says "same region" — it does **not** mean conflict. Dispatch the **`edge-judge`** subagent (`agents/edge-judge.md`) once, passing both `depends_candidates` and `overlap_pairs` plus the referenced capsules' names/intents/slices. The judge returns two things:

- confirmed `depends_on` edges, and
- a precise relationship for each overlap pair: `alternative_to`, `composable_with`, `supersedes` (directed), `conflicts_with`, or "leave as overlaps".

Write each result with `rgit edges --add`:

```
rgit edges --add depends_on <src> <dst>          # confirmed dependency
rgit edges --add alternative_to <a> <b>          # symmetric: write BOTH directions
rgit edges --add alternative_to <b> <a>
rgit edges --add supersedes <newer> <older>      # directed: one line
```

For a symmetric type write both directions; for `supersedes` write the single directed edge. Pairs the judge leaves unclassified keep their neutral `overlaps` baseline — the graph renderer hides that baseline automatically once a richer edge exists for the pair, so don't delete anything.

Reject coincidental overlaps — a missing edge is cheaper than a wrong one. The confirmed `depends_on` plus the agent-classified same-region relationships are what make `recall` and `rgit graph` show real structure instead of an undifferentiated conflict mesh.

## Notes

- **No paid API.** All LLM work here is the dispatched `capsule-segmenter` and `edge-judge` subagents, which run on this session's subscription.
- **Phase 1 vs Phase 2.** `rgit` (diff + libcst + heuristic) is the deterministic substrate; the subagents are the semantic layer. Same split as the Understand-Anything plugin (deterministic extraction → dispatched analyzer).
- **Regeneration** (recalling a capsule and re-applying it onto today's code) is the sibling flow — see the `capsule-regenerator` agent driven off `recall` + `compose` (the `rgit-recall` skill).
