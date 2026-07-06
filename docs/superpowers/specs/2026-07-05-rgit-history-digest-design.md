# History digestion: `rgit digest` + init-time backfill

**Date:** 2026-07-05
**Status:** approved

## Problem

rgit's memory compounds commit by commit, so a repo that adopts it from day one accumulates a rich capsule graph for free. A mature codebase gets nothing: its years of features, abandoned experiments, and reverted ideas are locked in `git log`, and `rgit init` starts an empty graph next to them. Recall has nothing to recall. The knowledge that most needs a memory system — "did we try this before? how did we build that? why was this reverted?" — is exactly the knowledge init throws away.

## Decisions (brainstormed 2026-07-05)

- **What to digest is the user's choice at init**: four modes — `layered` (default: everything ranked, dead experiments boosted), `trunk` (only features alive in today's code), `dead` (only reverted/deleted work), `archaeology` (layered + evolution-chain edge candidates) — plus explicit `A..B` ranges.
- **Backfilled capsules are auto-approved**, marked `origin=backfill`. No interactive review for history; recall can filter them; one command removes them all. The "never auto-approve" gate stays for live capture only.
- **Old history is ignored by default.** Scan the most recent window of mainline commits (default 400); if the whole history fits in the window, digest everything. Users extend explicitly with a range or `--all`.
- **Mainline only.** v1 scans the first-parent chain reachable from HEAD. No branch archaeology (abandoned branch tips are a later extension).

## Principle

**The engine plans for free; the agent digests on subscription; git facts stay engine-written.** Scanning, clustering, ranking, queueing, and outcome facts ("reverted by `<sha>` on `<date>`") are deterministic Python — no LLM anywhere near them. The intelligence plane only does what it alone can: cluster a diff into features, name intent, write resurrection guides. Digestion is a resumable queue, never a blocking ceremony: init stages the plan, any host-agent session drains it batch by batch.

## Design

### 1. Scan: units, dead detection, ranking — `rgit digest scan [A..B] [--mode M] [--all]`

Free and deterministic. Walks `git rev-list --first-parent` over the selected range (default: last 400 mainline commits; the whole history when it fits; an explicit `A..B` or `--all` overrides). A shallow clone gets a stderr notice that only the visible range is digestible.

Clustering, oldest → newest, each unit = exactly one future segmenter dispatch:

1. **Merge units.** Merge commit `M` → unit diff `M^1..M` (the whole merged branch as one diff — the PR content). Squash-merge repos naturally yield single-commit units.
2. **Revert pairs → dead units.** Detect `This reverts commit <sha>` trailers and `Revert "..."` subjects. The original commit X becomes a `kind=dead` unit (diff of X itself) carrying `reverted_by`, revert date, and revert message; both X and the revert R leave the normal stream. A revert whose original lies outside the scanned range is treated as a normal unit — its diff (the removal) is still real history.
3. **Streak clustering.** Consecutive non-merge commits by the same author, gaps ≤ 48h, with overlapping touched files → one unit (diff `first^..last`; a streak containing the root commit diffs against git's empty tree). Caps: ≤ 10 commits and ≤ 300 KB of diff per unit, split when exceeded; a single oversized commit becomes its own unit flagged `oversized` (the flag travels to the segmenter).
4. **Infra pre-drop.** A unit whose changes all match noise patterns (lockfiles, docs-only, pure formatting, dependency bumps, CI config) is recorded `skipped=infra` and never queued. Patterns are conservative — ambiguity goes to the segmenter, which already drops noise.

**Deleted-later detection (file-level, v1):** a unit whose touched files all no longer exist in today's HEAD → `kind=dead` (deleted). Symbol-level detection (`git log -S`) is out of scope for v1.

**Ranking** (deterministic score, sets digestion order): log-scaled churn + code density (test/doc-heavy units rank down) + a significant dead-experiment bonus (the only knowledge that is actually lost otherwise) + recency + a merge-structure bonus. Modes act as filters on the queue: `trunk` keeps `landed`, `dead` keeps `dead`, `layered` keeps both with dead boosted, `archaeology` = layered + chronology edge candidates (§4).

Unit identity = hash of the unit's commit-sha set, so re-running scan is idempotent and incremental: new commits create new units, existing rows are untouched. Re-running with a different `--mode` or range updates the settings row and re-filters the queue; unit rows persist.

### 2. Storage

**New table `digest_units`** (idempotent migration in `db.py`): `id` (sha-set hash prefix), `kind` (`landed` | `dead`), `shas` (JSON), `score`, `status` (`pending` | `staged` | `done` | `skipped`), `skip_reason` (`infra` | `empty` | `user` | `error`), `proposal_id`, `capsule_ids` (JSON), `meta` (JSON: subjects, dates, author, `reverted_by`, `oversized`), `created_at`. Plus a single settings row: mode, range spec, thresholds, and `head_at_scan` — the boundary between digested history and the live-capture world (post-commit hooks own everything after it).

**`Capsule.origin`**: `"live"` (default) | `"backfill"`. Features-table migration adds the column with default `'live'`. Exposed everywhere capsules render: `rgit features`, recall results, graph, MCP payloads. `rgit recall --exclude-backfill` filters (the MCP recall tool gains the same optional filter); ranking does not discount backfill (quality is agent-grade either way).

**`Proposal.trigger`** gains the value `"backfill"`.

### 3. Staging and accept — `rgit digest next` / `accept` / `skip` / `clear` / `status`

- `rgit digest next [--batch N] --json` (default batch 10): first recycles `staged` units whose proposal is still open (crash recovery — no duplicate staging; content-addressed `diff_ref` dedup is the backstop), then stages the next highest-ranked `pending` units: each gets a proposal via `CommitDiffSource`/`RangeDiffSource` pinned at the historical shas, `trigger="backfill"`, `HeuristicSegmenter` placeholder candidates. Returns `[{unit, proposal_id, diff, symbols, meta}]` — same shape idea as `rgit pending --json`, plus commit metadata for the segmenter.
- `rgit digest accept <proposal_id>`: non-interactive. Every candidate on the proposal becomes an approved capsule with `origin=backfill` and `base_commit = proposal.source_commit` (the unit's end commit — capsules pin to where the feature landed, existing behavior); the proposal is marked `resolved`. **Dead units get an engine-written outcome**: `result_summary.failure_reason` = first line of the revert message (when present), `notes` = `"reverted by <sha> on <date>"` or `"files deleted from HEAD"`. Outcome facts come from git, never from the agent. Zero candidates is a legal result: the unit resolves `skipped=infra`. Unit → `done` with `capsule_ids` recorded.
- **Backfill proposals stay off the live-capture surface**: `rgit pending` excludes `trigger="backfill"` proposals, so the `rgit-capture` skill never grabs an in-flight digest batch. If a staged unit's proposal was resolved through another path anyway, `digest next` reconciles instead of re-staging: proposal `resolved` → unit `done`, proposal `dismissed` → unit `skipped=user`.
- `rgit digest skip <unit_id>`: mark `skipped=user`.
- `rgit digest clear`: delete every `origin=backfill` capsule plus its edges (new `Store.delete_feature` with cascading edge delete), reset `done` units to `pending`. The regret channel — `origin` exists precisely so cleanup can't touch hand-made capsules.
- `rgit digest status [--json]`: totals by status, digested/remaining counts, estimated remaining batches.

CLI conventions unchanged: stdout is clean JSON, prompts and notices on stderr.

### 4. Edges at scale

Backfill makes `depends_candidates` O(n²)-explosive. Rules: after each accepted batch, generate candidates only for **new-batch capsules × the graph** (never re-scan old pairs); cap what goes to the edge-judge at **30 pairs per batch**, ranked by evidence strength (the number of shared identifiers in `depends_candidates` evidence); everything past the quota keeps the free `overlaps` baseline unjudged. A missing edge stays cheaper than a wrong one. `archaeology` mode adds chronology candidates — units touching the same top-level symbol across time → `supersedes` hypotheses — into the same per-batch quota.

### 5. `rgit init` changes

After `Store.init()`, init inspects history. On a TTY with ≥ 2 mainline commits: prompt (stderr, numbered pickers like the guidance-mode precedent) — digest history? → mode (4 options) → range (default window / all / custom `A..B`) — then run the scan inline (free) and print the plan: `staged N units (~K batches); ask your agent to run the rgit-digest skill to continue`. Non-TTY: never block; print a one-line hint (`note: N commits of history detected; run \`rgit digest scan\` + the rgit-digest skill to backfill`). Automation flags: `rgit init --digest[=MODE] [--range A..B]` and `rgit init --no-digest`.

Init only ever plans. Actual digestion happens in a host-agent session — the engine cannot and must not dispatch subagents.

### 6. Plugin: the `rgit-digest` skill

Third skill in `src/rgit/_plugin/skills/` (keep `pyproject.toml` package-data globs in sync). Flow:

1. `rgit digest status --json` → report totals, agree with the user how many batches this session runs.
2. Loop until the session budget is spent or the queue is empty: `rgit digest next --batch 10 --json` → dispatch **the existing `capsule-segmenter`** concurrently per proposal, passing a `history_context` block (commit subjects, dates, author, `reverted_by`, oversized flag, and the caveat "this is a historical diff; today's code may have moved") → `rgit resegment <pid> --from-json -` → `rgit digest accept <pid>`.
3. After each batch: `rgit edges --apply` (incremental per §4) → one `edge-judge` dispatch with the quota'd candidates → write results back.
4. Report: digested N units → M capsules (X dead); K units remain; run the skill again to continue.

`capsule-segmenter.md` gets a compatible extension only: an optional `history_context` input and one rule for historical mode (write the resurrection guide against today's code, which may have refactored past the diff). No new segmenter agent. The installer's managed guidance block gains a pointer to `rgit-digest` so host agents discover the skill.

### 7. Errors and recovery

- Empty unit diff (e.g. everything excluded) → `skipped=empty`, no proposal, batch continues.
- Garbage segmenter output → existing `validate_candidates` rejects at `resegment`; the skill marks the unit `skipped=error` with a note and moves on. One bad unit never blocks a batch.
- Interrupted session → the queue is the progress; `digest next` recycles staged-open work (see §3).
- New commits after scan → re-run `scan`; unit-id hashing makes it purely additive. Everything after `head_at_scan` belongs to live capture (hooks/watch), so the two never overlap.
- Non-Python files → `symbols` comes back empty and the segmenter works from the raw diff, exactly as live capture does today.
- Windows + Python 3.11 stay supported: all git access goes through the existing `_git` wrappers; new test fixtures pin `core.autocrlf false` like `git_repo`.

### 8. Testing

Mirror layout, `MockSegmenter`/injected candidates only — tests never dispatch agents.

- `tests/test_digestscan.py`: a scripted-history fixture factory in `conftest.py` (merges, a revert pair, an author streak, an infra-only commit, a file deleted later) → assert unit boundaries, kinds, infra pre-drop, ranking order, window defaults, rescan idempotence.
- `tests/test_digestqueue.py`: scan → next → accept/skip → status lifecycle; staged-open recovery after a simulated crash; `clear` cascades (capsules + edges gone, units reset).
- `tests/test_cli.py` additions: digest subcommand JSON contracts; init TTY prompt (mocked stdin), non-TTY hint, `--digest/--no-digest/--range` flags.
- `tests/test_e2e.py` addition: scripted history → scan → simulated skill loop (inject candidates via `resegment`, then `accept`) → recall returns backfill capsules, `--exclude-backfill` filters them, dead capsules carry the revert facts.
- Edge quota: over-quota candidate sets judge only the top 30; the rest keep `overlaps`.

## Defaults (all configurable)

| knob | default |
|---|---|
| scan window (first-parent commits) | 400 |
| streak cap | 10 commits / 48h / 300 KB |
| batch size (`digest next`) | 10 units |
| edge-judge quota | 30 pairs per batch |

## Out of scope (v1)

Branch scanning (abandoned branch tips), symbol-level deletion detection (`git log -S`), refining already-approved capsules, embeddings, and any mutating MCP tools (the MCP plane stays read-only by design).
