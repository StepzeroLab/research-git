---
name: rgit-digest
description: Use when a research-git digest queue has pending units (`rgit digest status`) — after `rgit init` staged a history-digestion plan, or when the user asks to backfill, digest, or import git history into capsules. Batches run on the host session's subscription and progress is resumable, so partial sessions are fine.
---

# rgit-digest

Drains the history-digestion queue: deterministic staging through the `rgit` CLI, semantic segmentation via the `capsule-segmenter` subagent, non-interactive ingestion as `origin=backfill` capsules. Unlike live capture there is NO human approval gate — backfilled capsules are marked, filterable in recall, and bulk-removable (`rgit digest clear`).

**Prerequisites:** the repo is `rgit init`-ed and `rgit digest scan` has run (init usually does this; run it yourself if `rgit digest status` shows no units).

**Locating the agent definitions.** Same as rgit-capture: on Claude Code the plugin runtime resolves agent paths; on other CLIs resolve the plugin root from the skill symlink:

```bash
SKILL_REAL=$(realpath ~/.agents/skills/rgit-digest 2>/dev/null || readlink -f ~/.agents/skills/rgit-digest)
PLUGIN_ROOT=$(dirname "$(dirname "$SKILL_REAL")")
```

## Process

### 1. Report the plan and agree on scope

Run `rgit digest status --json`. Tell the user: pending units, how many are dead experiments, estimated batches. Agree how many batches to run this session (default: keep going until the queue is empty or the user stops you).

### 2. Stage a batch

```
rgit digest next --batch 10 --json
```

Each item is `{unit_id, kind, score, proposal_id, meta, diff, candidates, oversized}`. `meta` carries commit subjects, dates, author — and for dead units `reverted_by` / `revert_subject` / `revert_date`. Empty-diff units never appear (the engine skips them itself).

### 3. Dispatch capsule-segmenter per item (concurrently)

For each item, dispatch a subagent from `agents/capsule-segmenter.md`, passing `proposal_id`, `repo_root`, `diff`, and a `history_context` block built from `meta`: the commit subjects + dates + author, the revert info when present, `oversized` when true, and the note "historical diff — today's code may have refactored past it; write the resurrection guide against intent and structure". The subagent returns `{"capsules": [...], "dropped": [...]}`.

### 4. Write back and ingest — no approval question

```
echo '<capsules-json-array>' | rgit resegment <proposal_id> --from-json -
rgit digest accept <proposal_id>
```

`accept` ingests every capsule as `origin=backfill` (dead units get their revert facts recorded by the engine) and marks the unit done. Zero capsules is fine — the unit resolves as infra. Do NOT use `rgit review` here and do NOT ask the user per capsule: the backfill trust model is mark-and-filter, not gate.

### 5. Wire edges after each batch

Collect the feature ids `accept` printed, then:

```
rgit edges --apply --scope <fid1,fid2,...> --limit 30
```

`edges --apply` emits JSON to stdout. Dispatch `agents/edge-judge.md` once with the `overlap_pairs` and `depends_candidates` from that output plus the referenced capsules' names/intents/slices. Include each backfill capsule's base-commit date; in archaeology mode explicitly ask the judge to consider `supersedes`/`variant_of` for same-region pairs across time. Write confirmed edges with `rgit edges --add ...` exactly as rgit-capture does (symmetric types in BOTH directions).

### 6. Loop and report

Repeat 2–5 until the agreed batches are done or `rgit digest status` shows nothing pending. Then report: units digested → capsules created (dead count), units remaining, and that running this skill again resumes where it left off.

## Notes

- **Sibling flows:** live capture (human-gated) is `rgit-capture`; recall/regeneration is `rgit-recall`.
- Interrupted sessions are safe: `rgit digest next` recycles anything staged but not yet accepted.
