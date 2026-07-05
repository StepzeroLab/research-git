---
name: rgit-recall
description: Use when the user wants to recall, resurrect, bring back, or re-apply a previously captured feature/idea onto today's codebase (e.g. "bring back the re-ranking retrieval step").
---

# rgit-recall

Drives the **recall → compose → regenerate** half of the research-git loop. The stored capsule is a spec; the regenerator rebuilds it onto *today's* code.

**Prerequisites:** the repo is `rgit init`-ed and the `research-git` MCP server is connected (it exposes `recall` and `compose`).

**Locating the agent definitions.** On Claude Code the plugin runtime resolves agent paths for you. On other CLIs (Codex, Gemini, opencode) this skill is symlinked into `~/.agents/skills/rgit-recall`, so resolve the plugin root once and reference the agent from there:

```bash
SKILL_REAL=$(realpath ~/.agents/skills/rgit-recall 2>/dev/null || readlink -f ~/.agents/skills/rgit-recall)
PLUGIN_ROOT=$(dirname "$(dirname "$SKILL_REAL")")    # the bundled _plugin/ directory
```

The `agents/capsule-regenerator.md` reference below lives at `$PLUGIN_ROOT/agents/capsule-regenerator.md`.

## Process

### 1. Recall the capsule(s)

Take the user's natural-language ask and call the MCP tool **`recall(query)`**. It returns matches, each with its `depends_on` subgraph. Show the user a short list (name + intent) and confirm which feature(s) to bring back. Default to the top match if unambiguous. If nothing matches, tell the user and stop (suggest `list_features` to browse).

### 2. Resolve the full feature set

Include each chosen capsule **plus its `depends_on` dependencies** (a feature often needs its prerequisites). Collect the final list of `feature_id`s.

### 3. Compose the regeneration brief (against current code)

Call the MCP tool **`compose(feature_ids)`**. It returns, per feature: `intent`, `knobs`, `data_assumptions`, `resurrection_guide`, the reference `code_slices`, the **live `current_source`** of each touched symbol, and any `conflicts` (symbols touched by more than one chosen feature).

### 4. Dispatch the capsule-regenerator subagent (on subscription)

Dispatch a subagent using the **`capsule-regenerator`** agent definition (`agents/capsule-regenerator.md`). Pass the full brief verbatim plus `repo_root`. The subagent edits the working tree to re-implement the feature(s) onto today's code, resolves conflicts, sanity-checks syntax, and returns an `applied` report with `provenance` (clean vs adapted) per feature.

### 5. Review + close the loop

Show the user the resulting working-tree diff (`git diff`) and the subagent's provenance/adaptation notes. **Do not commit, run, or freeze for them** — the human runs the experiment, and that run is what freezes the reproducible artifact.

Hand them a complete, paste-ready command with the real capsule id filled in — never a template with `<placeholders>`. If you don't already know their test command from the conversation, ask for it first. Example of the shape (with a real id):

```
rgit run --from feat_ab12 -- python eval.py --retrieval rerank
```

That records a new `run` node, freezes a byte-exact artifact, links a `produced` edge from the source capsule, and (on approving the resulting proposal) establishes `variant_of` back to the original. If the subagent returned an `updated_resurrection_guide`, write it to a file and add `--refresh-guide-file <path>` to that same command.

## Notes

- **Reproducibility stays intact.** The subagent only *authors*; the human's `rgit run` is the only thing that freezes the artifact — the agent is never in the replay path.
- **Sibling flow:** capture/segmentation is the `rgit-capture` skill (`capsule-segmenter` agent).
