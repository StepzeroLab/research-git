# research-git v3 — The Research Layer

**Status:** Approved in brainstorming (pending spec review)
**Date:** 2026-06-27
**Scope:** Milestone spec v3. Builds on the shipped v1 (Memory Loop) and v2 (Graph Intelligence + Ambient Capture). Parent: `2026-06-16-research-git-design.md` §8.

---

> **Thesis (v3).** The graph stops just *storing* ideas and starts *answering research questions*: which variant won, what does the ablation say, is this regeneration faithful to the original, and how do colliding features merge. Every surface is terminal-only — formatted `rgit` subcommands that print tables — with **no web dashboard and no editor UI**.

## 1. Where v3 sits

v1 built the memory loop (capture → curate → recall → regenerate). v2 added graph intelligence (edge inference, ranked recall, ambient capture) and split the planes (deterministic CLI engine + query-only MCP + plugin agents). v3 is the **research-query layer**: it turns the lineage edges (`variant_of`, `produced`), run metrics (now parseable), and frozen artifacts that v1/v2 already record into answers a researcher actually asks.

**Design constraints carried forward (unchanged):**

1. The **deterministic engine** does all the work. Three of the four v3 features (`compare`, `ablation`, `provenance`) are *pure graph/object reads* — zero agent, zero network.
2. The **agent appears in exactly one place**: regeneration. v3 adds **no new agent**; it teaches the existing `capsule-regenerator` to merge.
3. **MCP stays query-only** (principle 7). v3's read features are safe to expose to a shared lab memory, so they are added as read-only MCP tools.
4. **No paid API.** Nothing in v3 calls a paid model. (Embedding semantic recall, "G", needs a model and is therefore deferred to v4.)

## 2. Features

### 2.1 `rgit compare <symbol|capsule>` — "which variant won"

*Pure graph read.*

Resolve the target, gather the variant cluster, attach each variant's run metrics, rank, print.

- **Target resolution.** Argument is either a capsule id/name or a `file:symbol` (or bare symbol). A capsule target uses its `variant_of` lineage cluster (transitive closure over `variant_of` in both directions). A symbol target gathers every capsule whose `code_slices` touch that symbol.
- **Metrics.** For each capsule in the cluster, follow `produced` edges (capsule → run) to its runs and read `run.metrics`. A capsule may have several runs; the table lists each run as a row.
- **Ranking / verdict.** Pick the metric: explicit `--metric <name>` wins; otherwise the metric present across the most runs. Direction comes from the metric-direction config (§2.5); `--higher` / `--lower` override. The best run for that metric is marked `★`. If the chosen metric's direction is unknown, the table still prints (values shown) but **no `★` verdict** is rendered and a one-line note explains how to set the direction.
- **Output.** A table `feature → run → <metrics…> → Δ(vs cluster baseline) → verdict`. The baseline for Δ is the cluster's lowest-`created_at` run.

### 2.2 `rgit ablation A B [...]` — base vs +A vs +A+B grid

*Pure graph read.*

- **Input.** One or more capsule ids/names. The engine forms the **powerset** of that set (`{}`, `{A}`, `{B}`, `{A,B}`, …).
- **Active-feature set of a run.** The set of capsules connected to the run by an **`active` edge** (§2.4). For runs with no `active` edges (e.g. pre-v3 runs), the engine falls back to the run's `produced` capsules as its active set.
- **Cell selection.** For each subset in the powerset, find run(s) whose active set equals that subset exactly. When several match, use the latest by `created_at`. Empty cell renders as `—` (explicitly "no run"), never an interpolated guess.
- **Output.** A grid: rows = subsets (ordered by size, then name), columns = the union of metric names across selected runs. Per column, the best cell is marked `★` using the metric-direction config (unknown direction → no `★`).

### 2.3 `rgit provenance <run>` — clean vs agent-adapted audit

*Pure read. No new storage.*

A regeneration adapts a capsule's stored ("clean") code onto today's tree; provenance proves whether that adaptation stayed faithful.

- **Capsules in scope.** Capsules connected to the run by `produced` or `active` edges.
- **Clean code.** The capsule's stored `code_slice.code`.
- **Adapted code.** The same `file:symbol` extracted from the run's **frozen artifact**: `objects.get(run.artifact_hash)` → untar in memory → read `slice.file` → `read_symbol_source(text, symbol)`. The freeze already content-addresses the whole working tree, so this is a pure lookup.
- **Output.** Per slice: a unified diff (clean → adapted) and a one-word flag — `clean` (byte-identical after normalization) or `adapted` (differs). A run-level summary line counts each. A slice whose file/symbol is absent from the artifact renders as `missing` (the feature was not present in that run's tree).

### 2.4 Conflict-merge — enrich `compose()`, teach the regenerator

*Engine + the existing agent. Wired through the recall → regenerate path, NOT a standalone `rgit merge` command.*

`compose()` already detects when several recalled capsules touch the same `(file, symbol)` and emits a `conflicts` list. v3 upgrades that flag into a structured **MergeContext** and teaches the regenerator to act on it.

- **MergeContext (per colliding region).** `{file, symbol, current_source, contributors: [{capsule, clean_slice, knobs, intent}]}` — the live current source plus each contributing capsule's clean slice, intent, and knobs. Built deterministically inside `compose()`; no agent involved in *building* it.
- **Regenerator behavior.** `capsule-regenerator.md` gains a section: when the brief contains MergeContext entries, produce a single coherent merged implementation of that region that honors every contributor's intent/knobs (e.g. the entropy + temperature + label-smoothing triangle becomes one combined loss), rather than emitting conflicting edits or only reporting the `conflicts_with` edge.
- **Boundary.** The engine computes *where* and *what collides* (deterministic); the agent decides *how* to merge (judgment). Same split as everywhere else in the system.

### 2.5 Metric-direction config (supports verdicts)

Verdicts in `compare`/`ablation` need a per-metric direction. Direction is **data, not a silent guess**.

- **Storage.** `metric_directions` table: `metric TEXT PRIMARY KEY, direction TEXT` where direction ∈ {`higher`, `lower`}.
- **CLI.**
  - `rgit metric-dir set <metric> <higher|lower>` — write/update.
  - `rgit metric-dir list` — show the table.
  - `rgit metric-dir suggest` — deterministic name heuristic: a metric whose name matches `loss|err|nll|ppl|perplexity` → `lower`; `acc|accuracy|f1|reward|score|bleu|rouge` → `higher`. It **prints proposals only** (`metric → suggested direction`); it never writes. The user applies them with `set`.
- **Agent-propose path.** No new agent. The host agent, when it has context, populates directions by calling `rgit metric-dir set` itself — the same CLI the user uses.
- **Consumption.** `compare`/`ablation` read this table to mark `★`. Unknown direction → value shown, no verdict, one-line hint.

## 3. Data model changes

Minimal and additive.

- **`active` edge** (`run → capsule`, type string `"active"`). Written by `rgit run --with <names/ids>`. The edges table is generic (`src, dst, type`), so this needs **no schema migration** — only a new type string and a thin `Store.active_features(run_id)` reader (and `runs_with_active(capsule_id)` for ablation). Ablation falls back to `produced` edges when a run has no `active` edges.
- **`metric_directions` table.** The only real migration: created idempotently in `init_schema`, following the exact pattern of the v2 `returncode` migration (so `Store.open` on a legacy DB self-heals).

No other tables change. `runs`, `features`, `edges`, `proposals`, `events` are untouched.

## 4. CLI surface (additions)

```
rgit compare <symbol|capsule> [--metric NAME] [--higher|--lower]
rgit ablation <capsule> [<capsule> ...] [--metric NAME]
rgit provenance <run>
rgit metric-dir set <metric> <higher|lower>
rgit metric-dir list
rgit metric-dir suggest
rgit run --with <name-or-id>[,<name-or-id>...] -- <cmd>     # writes `active` edges
```

All output is formatted tables/diffs printed to stdout. No flags spawn a server or open a browser.

## 5. MCP surface (additions, query-only)

Per principle 7, the read features are safe for a shared lab memory and are exposed as **read-only** tools returning structured snippets (the CLI renders them as tables; MCP returns the underlying data):

- `compare(target)` → ranked variant cluster + metrics.
- `ablation(capsules)` → the grid as structured cells.
- `provenance(run)` → per-slice clean/adapted/missing + diffs.

These tools **read only**; they never write edges, capsules, or directions. The write path (`metric-dir set`, `run --with`) stays in the CLI/engine plane.

## 6. Component & file map

Five new pure modules + targeted edits. Each new module has one responsibility and is independently unit-testable.

| File | New/Edit | Responsibility |
|---|---|---|
| `src/rgit/compare.py` | new | resolve target → variant cluster → ranked metric table (pure data) |
| `src/rgit/ablation.py` | new | powerset → active-set grouping → grid (pure data) |
| `src/rgit/provenance.py` | new | run artifact untar → clean vs adapted per slice (pure data) |
| `src/rgit/metricdir.py` | new | direction config CRUD + heuristic `suggest` (pure) |
| `src/rgit/tables.py` | new | shared terminal table + unified-diff renderer (pure formatting) |
| `src/rgit/compose.py` | edit | `conflicts` flag → structured MergeContext |
| `src/rgit/_plugin/agents/capsule-regenerator.md` | edit | consume MergeContext, perform real merge |
| `src/rgit/store/db.py` | edit | `metric_directions` table + idempotent migration |
| `src/rgit/store/store.py` | edit | metric-dir CRUD; `active_features` / `runs_with_active` readers |
| `src/rgit/runner.py` | edit | `--with` capsules → write `active` edges |
| `src/rgit/cli.py` | edit | `compare` / `ablation` / `provenance` / `metric-dir` subcommands + `run --with` |
| `src/rgit/mcp_server.py` | edit | read-only `compare` / `ablation` / `provenance` tools |

The compute modules (`compare`, `ablation`, `provenance`, `metricdir`) return plain data structures; `tables.py` and the CLI/MCP layers handle presentation. This keeps the research logic free of formatting and fully testable.

## 7. Data flow

```
rgit compare X
  cli → compare.compare(store, X)
        ├─ resolve target (capsule lineage cluster | symbol touchers)
        ├─ per capsule: neighbors(cap, "produced") → get_run → metrics
        ├─ metricdir.direction(metric) → rank, mark ★
        └─ return rows                     → tables.render → stdout

rgit ablation A B
  cli → ablation.ablation(store, [A,B])
        ├─ powerset({A,B})
        ├─ per run: active_features(run) (fallback produced) → bucket by subset
        ├─ per column metricdir.direction → mark ★
        └─ return grid                     → tables.render → stdout

rgit provenance R
  cli → provenance.provenance(store, R)
        ├─ capsules via produced|active edges
        ├─ untar objects.get(run.artifact_hash) in memory
        ├─ per slice: clean=slice.code, adapted=read_symbol_source(artifact_text, symbol)
        └─ return per-slice {flag, diff}    → tables.render → stdout

recall → compose (regenerate path)
  compose(store, ids)
        ├─ existing per-capsule current_source + conflicts
        └─ NEW: build MergeContext for colliding regions
                                           → capsule-regenerator (agent merges)
```

## 8. Error handling

- **Unknown target / run id** → a clear `KeyError`-derived CLI message (`no capsule/run matching '<x>'`), exit non-zero. Never a stack trace to the user.
- **No runs / no metrics in a cluster** → the table prints with empty metric cells and a note ("no runs recorded for this variant"); not an error.
- **Unknown metric direction** → values shown, no `★`, one-line hint to run `metric-dir set`. Not an error.
- **Slice missing from a frozen artifact** (provenance) → that slice renders `missing`; other slices still render.
- **Corrupt/absent artifact object** → provenance reports `artifact unavailable` for the run and exits non-zero (the freeze contract was violated upstream).
- **Tolerance principle (carried from v1/v2):** read commands never mutate state and never abort the whole report because one cell is unresolvable — a bad cell degrades to `—` / `missing`, the rest prints.

## 9. Testing

TDD, `.venv/bin/pytest`, no paid API.

- `tests/test_compare.py` — target resolution (capsule vs symbol), variant-cluster gathering, ranking with/without known direction, Δ baseline, empty-metrics degradation.
- `tests/test_ablation.py` — powerset bucketing, exact-subset match, latest-run tiebreak, `produced`-edge fallback, empty cell `—`, per-column `★`.
- `tests/test_provenance.py` — clean==adapted → `clean`; modified symbol → `adapted` with diff; absent slice → `missing`; missing artifact → error.
- `tests/test_metricdir.py` — set/list/get round-trip; idempotent migration on a legacy DB (mirror v2's `test_open_migrates_legacy_v1_db`); `suggest` heuristic mapping; unknown metric → no direction.
- `tests/test_compose.py` (extend) — MergeContext built for a colliding region with both contributors' clean slices + current source.
- `tests/test_runner.py` (extend) — `--with` writes `active` edges; no `--with` writes none.
- `tests/test_cli.py` (extend) — `compare` / `ablation` / `provenance` / `metric-dir` happy paths + unknown-id error exit codes.
- `tests/test_mcp_server.py` (extend) — read-only `compare`/`ablation`/`provenance` tools return structured data and write nothing.

## 10. Non-goals (v3)

- **No web dashboard / served HTML graph viewer.** Every surface is a terminal `rgit` subcommand. A read-only graph visualization, if ever wanted, stays a separate optional add-on, never the primary surface.
- **No editor-projection extension.**
- **No `rgit merge` standalone command.** Conflict-merge lives inside the recall → regenerate path.
- **No embedding semantic recall (G).** It needs a local embedding model or a paid API; deferred to v4. The `ranking.score` slot stays lexical+structural in v3.
- **No auto-detection of active features from the tree.** Active features are declared explicitly via `--with` (deterministic, no false positives); tree-scanning detection is out of scope.

## 11. Roadmap after v3 (v4 seed)

- **G — embedding semantic recall.** Second signal in `ranking.score`, environment-gated on a local embedding model.
- Optional read-only graph visualization as a separate add-on, if demand appears.
