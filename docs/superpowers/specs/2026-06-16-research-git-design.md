# research-git — Overall Design

**Status:** Draft (approved in brainstorming, pending spec review)
**Date:** 2026-06-16
**Scope:** Master/vision spec. Drills down into milestone specs v1 → v2 → v3.

---

> **Thesis.** Git can recover *history*. It cannot recover an **entangled research idea as a
> clean semantic unit on top of today's codebase**. That gap is the entire reason research-git
> exists.

## 1. Problem

Git versions the **whole working tree as a timeline of snapshots**. But research changes
are really **orthogonal experiments** — add a loss term here, swap an attention block there,
toggle a data augmentation. Git forces these into a linear/branching history, so:

- "Go back to the version with feature X" drags along all the unrelated state from that
  moment.
- Infrastructure improvements made since get tangled with the experimental deltas you
  actually care about.
- A feature, once committed, is interleaved with infra and other tweaks — you can't cleanly
  isolate "just X" again.

The PhD reality: you comment code in/out, several half-features coexist in the tree, you run
an experiment, you move on. Weeks later you want *one* idea back — onto your *current* infra,
not the stale snapshot it was born in. Git cannot do this, because its unit is a tree
snapshot, not a semantic feature.

## 2. What research-git is

**Not really a version-control system — a memory system with an agent as its read/write head.**

- A **knowledge graph** is the durable, language-agnostic **memory**. Each experimental
  *feature* is a first-class node: its code snippet(s), its *intent*, its knobs, its
  relationships, and the experiment results it produced.
- An **agent** (your existing coding agent — Claude Code / Cursor) is the **read/write head**:
  it does *agentic retrieval* over the graph and **regenerates** a stored feature to fit the
  *current* codebase, rather than applying a stale patch literally.

The stored snippet is a **reference spec, not a patch**. The current code is always
regenerated fresh against current reality.

## 3. Core principles (the decisions that shape everything)

1. **Features are orthogonal units, not points on a timeline.** The system composes features
   onto current infra (mix-and-match), independent of when each was written.

2. **Lean agentic re-application.** When infra has moved under a feature, an agent
   understands the feature's *intent* and re-implements it into current code — it does not
   replay a brittle line-diff.

3. **Reproducibility is protected by frozen artifacts, never by the agent.** The agent is an
   *authoring-time* assistant, under human review — exactly like writing code by hand. The
   moment an experiment runs, the fully-materialized code is **frozen, content-addressed, and
   stored immutably**, linked to the run + metrics. "The code behind run X" is always a
   byte-exact replay of a stored blob — the agent is **never** in the reproduction path.

4. **Build the memory, borrow the agent.** The novel, ownable IP is the *memory engine*
   (capture, graph, freeze). The agentic retrieval/regeneration **and segmentation** are
   delegated to the host agent via **natively-dispatched subagents** (research-git ships as a
   plugin — see principle 7), not reimplemented as an agent loop. **No pay-per-use Claude API
   anywhere** — heavy LLM work runs on the host agent's subscription; the engine's only
   autonomous fallback is a *free, no-LLM heuristic*.

5. **Capture is ambient, anchored to the research heartbeat.** A feature enters the graph by
   the system *watching* your work and proposing candidate nodes you approve — not by manual
   discipline. The **experiment run is the natural punctuation**: the diff between run N and
   run N+1 is a candidate feature *and* auto-links that feature to its results.

6. **The graph is the source of truth for memory; git is the substrate.** research-git rides
   on top of a normal git repo, using it as the diff base and the frozen-artifact store.

7. **Two planes: a shared *dumb* memory, smart *local* clients** (like git's remote vs.
   working client). The graph and the intelligence that acts on it are separated:
   - **Shared memory plane — MCP (query-only).** MCP exposes the graph **read-only**:
     `recall` / `get_capsule` / `get_run` return **graph snippets** (capsule code + intent +
     `resurrection_guide` + edges + run links). It can be exposed to **external collaborators**,
     so a lab/team shares *one* memory. It carries **no LLM intelligence** — it just serves the
     graph.
   - **Local intelligence plane — the plugin (subagents + skills).** Every session installs the
     research-git plugin, which *defines the protocol*: how to segment a diff into capsules, how
     to regenerate a capsule onto current code, how to summarize. The session pulls snippets
     (from its local store *or* a shared MCP) and does the downstream work via
     **natively-dispatched subagents on its own subscription**.
   - **Why:** the shared graph is cheap to serve and freely shareable; every collaborator's
     heavy LLM work runs on *their own* subscription, locally, per the shared protocol — no
     central API billing, no central intelligence bottleneck.

## 4. The four core operations

| Operation | What it does | How |
|-----------|--------------|-----|
| **Capture** | A messy working tree → clean feature node(s) | Observer fires on a trigger → segmentation agent clusters the diff into candidate features → you approve/name in a queue |
| **Recall** | "Which variant did the loss-reweighting thing?" | Agentic retrieval: semantic search + graph traversal (pull the feature *and* its `depends_on` subgraph, avoid its `conflicts_with` set) |
| **Compose** | Apply feature(s) onto current infra | Agent reads retrieved snippets + intent + current code → regenerates + merges → you review → freeze artifact → run |
| **Link-to-results** | "Which feature won?" | Every run hangs off the feature(s) that produced it — a graph query over metrics |

## 5. Architecture — Hybrid (engine + MCP facade + CLI/hooks)

```
                 ┌──────────────────────────────────────────────┐
   triggers      │              research-git engine             │
 (run/commit/    │                                              │
  idle/manual)   │   observer ─► segmenter ─► graph store ◄─────┼─ freeze/run tracker
        │        │      │           (LLM)        (SQLite +       │        ▲
        ▼        │      │                         objects/)     │        │ rgit run
  CLI / git hooks├──────┘                            ▲          │        │
                 │                                    │          │
                 │                MCP facade (recall / compose / get)     │
                 └──────────────────────┬───────────────────────┘        │
                                        │ MCP                            │
                                        ▼                                │
                          Host agent (Claude Code / Cursor)  ────────────┘
                          = read/write head: retrieval + regeneration
```

- A small **local engine** owns the graph, the observer, segmentation, and freeze. It runs in
  the background for ambient capture (git hooks, file watcher, the `rgit run` wrapper).
- An **MCP facade** exposes `recall / compose / get` so the host agent is the read/write head
  for retrieval + regeneration.
- A thin **CLI + git hooks** drive ambient triggers and wrap experiment launches.

### Subsystems

| # | Subsystem | Job | LLM? |
|---|-----------|-----|------|
| 1 | **Observer** | Watch the tree, fire on triggers, diff vs a base | No |
| 2 | **Segmenter** | Cluster edits into coherent candidate feature nodes | Free heuristic (default) → host agent re-segments (subscription) |
| 3 | **Graph store** | The memory: feature/run nodes, edges, embeddings, payloads | No |
| 4 | **Curation** | Approval queue — name / merge / split / edit-intent | No |
| 5 | **Retrieval** | Agentic search + graph traversal | Yes (host agent) |
| 6 | **Regeneration** | Regenerate + merge features into current code | Yes (host agent) |
| 7 | **Freeze + run tracker** | Content-address artifact, record run + metrics, link back | No |

## 6. Data model (graph schema)

### Feature Capsule (the feature node)

A feature node is a **self-contained capsule** — the regeneration agent reads one node and
has everything it needs to bring the feature back:

| Capsule field | Meaning | Stored as |
|---|---|---|
| **intent** | why this experiment existed | `feature.intent` (NL) |
| **code slices** | relevant snippets / files / symbols | payload `[{file, symbol(qualified name), anchor, code, kind(add\|wrap\|insert)}]`, content-addressed in `objects/`. Symbol-level refs let the agent relocate code when files move/rename. |
| **knobs** | hyperparams / flags / configs | `feature.knobs(json)` |
| **dependencies** | modules + data assumptions | structural deps as **edges** (`depends_on`, `touches`); **data assumptions** (dataset, preprocessing, shape/dtype expectations) as `feature.data_assumptions` |
| **result** | metrics / notes / failure reason | denormalized `feature.result_summary {verdict(improved\|neutral\|regressed), key_delta, failure_reason, notes}`; authoritative per-run metrics live on `run` nodes via `produced` edges (one feature → many runs) |
| **resurrection guide** | how to regenerate on current code | `feature.resurrection_guide` (NL recipe) — advisory hint that seeds the `compose` brief; written against the feature's own intent/structure, not a specific infra snapshot; refreshed each time a regeneration succeeds |

Plus housekeeping: `{ id, name, status(proposed|approved), base_commit, payload_hash,
embedding }`.

**Two reconciliations:**
- *result is one-to-many.* A feature produces many runs; `result_summary` is a rollup, the
  `produced` edges hold the authoritative detail. The summary must never contradict the runs.
- *resurrection guide is a hint, not a contract.* Reproducibility rests on frozen artifacts,
  never on the guide. The guide may drift as infra evolves, so it is refreshed when a
  regeneration succeeds (the capsule learns).

### Other nodes / edges

- **run**: `{ id, cmd, artifact_hash, metrics(json), base_commit, env, created_at }` — the
  immutable reproducibility anchor.
- **edge**: `{ src, dst, type }`, type ∈ `{ depends_on, variant_of, derived_from, supersedes,
  produced, touches, conflicts_with }`.
- **proposal**: `{ id, trigger, diff_ref, candidates(json), status }`.

Storage: SQLite `.rgit/graph.db` for nodes/edges/proposals; content-addressed `.rgit/objects/`
for feature payloads and frozen run artifacts. The whole `.rgit/` sits beside `.git/`.

## 7. Reproducibility contract

The agent helps you *author* a composition; it is **never** a runtime/replay dependency.
`rgit run` freezes a byte-exact, content-addressed snapshot of what actually ran. "The code
behind run X" = re-materialize `artifact_hash`, guaranteed identical. Whether a feature was
applied cleanly or agent-adapted is recorded as provenance, so reproducibility is auditable.

## 8. Roadmap

### v1 — The Memory Loop (walking skeleton)
Thinnest end-to-end vertical, both hard unknowns (capture quality, regeneration quality)
touched. Detailed in §9 and its own child spec.

- Triggers: `rgit run` (experiment) + git commit + on-demand `rgit capture`.
- Segmentation: a **free, no-LLM `HeuristicSegmenter`** (one rough candidate per changed
  file, `libcst`-assisted hunk→symbol mapping) stages candidates on every trigger; the host
  agent **re-segments** them into high-quality capsules on demand via the MCP `pending_captures`
  + `resegment` tools. **No paid API.**
- Recall: keyword + structural match (no embeddings yet).
- Regeneration: host agent via MCP.
- Freeze + run node + metrics by convention.
- Curation: `rgit review` CLI.

### v2 — Plugin packaging + ambient intelligence
**Headline: ship research-git as a Claude Code plugin and split the two planes (principle 7).**

- **Plugin form (local intelligence plane).** Package as a plugin (`.claude-plugin/plugin.json`
  + `agents/*.md` + `skills/*/SKILL.md`), the way Understand-Anything injects subagents natively
  into a session. Concretely:
  - `agents/capsule-segmenter.md` — subagent that reads a diff + symbol map and produces
    high-quality capsules (intent, `data_assumptions`, `resurrection_guide` — i.e. the
    *summarization*). Replaces the v1 heuristic as the primary path; the heuristic stays as the
    no-agent fallback.
  - `agents/capsule-regenerator.md` — subagent that regenerates a recalled capsule onto current
    code (the compose/regeneration step).
  - `skills/rgit-capture/SKILL.md` — orchestrator: run the *free deterministic* part (`rgit`
    diff + libcst symbol map = "phase 1"), then **dispatch `capsule-segmenter` natively** (on
    the session's subscription, fan-out for big diffs), then write capsules back via `rgit`.
  - `skills/rgit-recall/SKILL.md` — recall → compose → dispatch `capsule-regenerator`.
  - The `rgit` CLI/store stays the deterministic substrate the skills/agents call.
- **MCP narrows to a query-only shared memory plane (principle 7).** Keep `recall` / `get_capsule`
  / `get_run` / browse — they return **graph snippets** and are safe to expose to external
  collaborators (a shared lab memory). The v1 intelligence-adjacent tools (`pending_captures`,
  `resegment`, and agent-side `compose`) **move into the local plugin plane** — MCP no longer
  carries intelligence, only serves the graph. A local session consumes snippets from its own
  store *or* a shared MCP and does the downstream work per the plugin's protocol.
- Always-on background daemon; periodic/idle segmentation so long messy sessions get
  segmented incrementally.
- Embedding-based semantic recall; the full edge taxonomy populated automatically
  (`depends_on`, `conflicts_with` inference).
- Comment-in/out toggles read as explicit feature activation/deactivation events.
- Better merge/split UX for proposals.

### v3 — The research layer
**The graph stops just storing ideas and starts answering research questions. All surfaces are terminal-only — formatted CLI output, no web dashboard, no editor UI** (a heavy frontend is out of character for a CLI + agent tool; the views are `rgit` subcommands that print tables).

- **Variant comparison / "which feature won"** — `rgit compare <symbol|capsule>` walks `variant_of` + `produced` + run metrics and prints a ranked table (feature → run → metric deltas → verdict). The deterministic payoff of the lineage edges and the (now parseable) run metrics.
- **Ablation tables** — `rgit ablation` prints base vs +featureA vs +featureA+featureB metric comparisons, generated from the graph.
- **Conflict-merge sophistication** — when several capsules touch the same region (e.g. the entropy/temperature/label-smoothing conflict triangle), the compose/regenerate step guides a real merge instead of only flagging the `conflicts_with` edge.
- **Provenance / audit views** — `rgit provenance <run>` shows clean (original) vs agent-adapted code per feature, so a regeneration is auditable. Terminal diff/table output.
- **G — embedding semantic recall** — a second signal in the `ranking.score` slot (query "regularization" matches "regularizer"). Environment-gated: needs a local embedding model or a paid API, so it lands when a local model is available.

**Explicit non-goals (v3):** no web dashboard / served HTML graph viewer, and no editor-projection extension. If a *read-only* graph visualization is ever wanted it stays a separate optional add-on, never the primary surface.

## 9. v1 detailed design — The Memory Loop

### 9.1 Components & boundaries (each independently testable)

- **`store`** (`.rgit/`) — pure data layer, *no LLM*. SQLite (`features`, `runs`, `edges`,
  `proposals`) + content-addressed `objects/`.
- **`observer`** (CLI + git hooks) — *no LLM*. `rgit init / run / capture / review /
  features`. Computes diffs vs a base via git.
- **`segmenter`** — pluggable behind a `Segmenter` protocol. The **default `HeuristicSegmenter`
  is free and LLM-free**: `libcst` maps hunks → symbols, grouped into one rough candidate
  **Feature Capsule** per changed file (low confidence). The host agent re-segments these into
  high-quality capsules via MCP (`pending_captures` → `resegment`). `MockSegmenter` is used in
  tests. There is no pay-per-use API segmenter.
- **`mcp`** (FastMCP server `research-git`) — read/compose over the store. Tools: `recall`,
  `compose`, `get_feature/run`, `list_features`.
- **`runner/freezer`** — *no LLM*. Content-addresses the working tree, records `run` node +
  metrics, links run→feature.

The two LLM-touching parts (`segmenter`; regeneration in the host agent) are isolated behind
clean interfaces so they can be mocked in tests.

### 9.2 CLI surface

- `rgit init` — create `.rgit/`, install git hooks.
- `rgit run -- <cmd>` — diff vs base → enqueue segmentation → execute experiment → on
  completion freeze artifact, create `run` node, parse metrics, link run→feature(s).
- `rgit capture [hint]` — on-demand segmentation pass.
- `rgit review` — list proposals; approve / name / merge / split → write feature nodes + edges.
- `rgit features` / `rgit log` — browse the graph.

### 9.3 MCP tools

- `recall(query)` → matching features + their `depends_on` subgraph + payload snippets.
- `compose(feature_ids)` → regeneration brief assembled from each Capsule: intent + code
  slices + knobs + `data_assumptions` + `resurrection_guide`, plus the *current* code of
  touched regions (located by symbol when files moved), plus conflict notes. The host agent
  edits the working tree from this; on success it refreshes the `resurrection_guide`.
- `pending_captures()` → open proposals awaiting segmentation, each with its raw diff, so the
  host agent can re-segment them on its subscription (no paid API).
- `resegment(proposal_id, candidates)` → replace a proposal's rough heuristic candidates with
  the agent's high-quality Feature Capsules.
- `get_feature(id)`, `get_run(id)`, `list_features()`.

### 9.4 Data flow (the demo narrative)

1. You hack on `model.py` — add a contrastive loss, messy, mixed with infra edits.
2. `rgit run -- python train.py` → diff vs base, segmenter proposes **`contrastive-loss-aux`**;
   training runs; artifact frozen; `run` node with metrics created; run→proposal linked
   tentatively.
3. `rgit review` → approve & name. Feature node lands in the graph with a `produced` edge to
   that run.
4. Weeks later, infra refactored. In Claude Code: *"bring back the contrastive loss."* Agent
   `recall("contrastive loss")` → node + snippet + intent → `compose([id])` → brief + current
   code → re-implements into refactored `model.py` → you review the diff → `rgit run` → new
   frozen artifact + `run` node + `variant_of` edge to the original.

### 9.5 Reproducibility

Metrics captured by convention: the experiment writes `rgit_metrics.json` or prints
`RGIT_METRIC key=value` on stdout; the engine parses. Freeze = git tree hash or `tar`+sha256
into `objects/`. Re-materialize must be byte-identical.

### 9.6 Error handling

- Low-confidence segmentation → proposal flagged "needs review," never auto-approved.
- No prior run → diff base falls back to `HEAD`.
- Regeneration → agent always shows a diff; nothing auto-committed; artifact frozen only on
  explicit `rgit run`.
- Two features touch the same region → `compose` returns it as a conflict note; agent resolves
  under review.
- Missing metrics → `run` stored with `metrics=null`, not an error.

### 9.7 Testing

- **Unit**: store CRUD, diff computation, hunk→AST mapping, content-addressing, metrics parsing.
- **Integration**: fixture repo with a known messy diff → segmenter (mocked LLM for
  determinism + opt-in live test) → assert candidate boundaries.
- **End-to-end**: the §9.4 narrative automated on a toy PyTorch-ish repo.
- **Reproducibility**: freeze → re-materialize → assert byte-identical, and same metric under
  fixed seed.

## 10. Non-goals (v1)

- Not a general-purpose VCS; git remains the substrate.
- No multi-user / collaboration / remote sync in v1.
- No conflict-merge automation beyond surfacing conflicts for the agent.
- No embeddings / dashboards / always-on daemon in v1 (v2/v3).

## 11. Open questions / risks

- **Segmentation quality** is the central research risk: can the agent reliably carve a clean
  feature from a messy, multi-feature diff? Mitigation: human approval queue; start with
  run-anchored diffs (smaller, more coherent boundaries).
- **Regeneration fidelity**: agent re-implementation may subtly differ from intent. Mitigation:
  human review + frozen artifacts + provenance (clean vs adapted).
- **Diff-base selection**: choosing the right base to diff against when runs/commits interleave.
- **Cost/latency** of autonomous segmentation on every trigger — may need batching/debounce.

## 12. Known v1 limitations (from final review — deferred to v2)

The v1 final code review confirmed the architecture, the reproducibility contract, and the
no-paid-API guarantee, and the fixed items (robust metrics, untracked-file capture,
freeze-before-parse) shipped. These remain open and are folded into v2:

- **Edges are written but not yet inferred or read.** `curation` records `touches` edges, but
  nothing creates `depends_on` / `conflicts_with`, so `recall`'s subgraph is empty in practice
  until v2's automatic edge inference lands. (v2 roadmap item.)
- **Conflict granularity mismatch.** `compose` detects conflicts at `(file, symbol)` while
  `touches` edges are file-level — unify on symbol-level when edge inference is added.
- **`find_features` breadth.** Keyword search covers name/intent/data_assumptions only (not
  `resurrection_guide`/`knobs`), and treats SQL `LIKE` wildcards literally — superseded by v2
  embedding-based recall.
- **Diff-format breadth.** `astmap` handles standard `+++ b/<path>` / `@@` diffs (tracked +
  `--no-index` untracked); exotic formats (`--no-prefix`, renames) are out of v1 scope.
- **Run provenance.** The runner does not record the experiment's exit code; a crashed run is
  stored like a successful one. Add a `returncode` field in v2.
