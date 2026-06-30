# research-git v2 — Graph Intelligence + Ambient Capture

**Parent spec:** `2026-06-16-research-git-design.md` (§8 roadmap, §12 known limitations).
**Status:** Implemented (A–F). G deferred to v3.
**Scope:** v2 milestone, features A–F. Feature G (embedding recall) is explicitly deferred to v3 — see Non-goals.

---

## 1. Why v2

v1 shipped the Memory Loop end-to-end and the plugin-packaging *plane* (agents + skills + marketplace + installer). What it left thin is the part that makes a graph memory actually *feel* like memory: the graph has no real edges, recall does not rank, and capture only fires on an explicit `rgit run`/commit. v2 closes that gap.

The through-line: **the engine stays deterministic and free.** Every new piece has a pure-function core plus a thin CLI or loop wrapper, so it is unit-testable in isolation. The agent appears in exactly one new place — judging `depends_on` candidates — and it lives in the plugin plane on the user's subscription, never in the store path and never as a paid API. MCP narrows to a query-only shared-memory plane (finishing principle 7).

v2 delivers six features:

- **A — Edge-aware recall ranking.** Replace the bare `LIKE` with a scored, wildcard-safe, edge-aware ranker.
- **B — Edge inference.** Deterministic `conflicts_with`; agent-judged `depends_on`.
- **C — Plane split.** Move the intelligence-adjacent write tools out of MCP into the CLI; MCP becomes query-only.
- **D — `returncode` on runs.** A failed run is no longer stored identically to a successful one.
- **E — Comment-in/out toggles as activation/deactivation events.** Read a feature being commented out as a deactivation, commented back in as an activation.
- **F — Ambient capture daemon.** A background watcher that keeps the Phase-1 proposal backlog warm so nothing is lost in a long messy session.

These directly close every open item folded into v2 by the parent spec §12 (no auto edge inference, weak/literal-wildcard recall, no `returncode`).

---

## 2. Architecture delta

```
ENGINE (deterministic, free, no agent)
  store/        +events table, runs += returncode
  ranking.py    NEW  pure score(capsule, query_tokens) -> float
  recall.py     rewritten to load + score in Python (no SQL LIKE)
  edges.py      NEW  conflict_pairs() [writes], depends_candidates() [emits, no write]
  toggles.py    NEW  detect_toggles(diff) -> activation/deactivation events
  watch.py      NEW  pure tick(store, snapshot) -> (snapshot, staged_proposal_id?)
  runner.py     threads proc.returncode; segment_diff records toggle events
  cli.py        +edges, +pending, +resegment, +watch

MCP (query-only shared memory)
  mcp_server.py recall / compose / get_feature / get_run / list_features
                REMOVED: pending_captures, resegment

PLUGIN PLANE (local intelligence, subscription, no paid API)
  agents/edge-judge.md       NEW  judges depends_on candidates
  skills/rgit-capture        rewritten: CLI write path + post-approve edge step
```

Nothing in the engine calls an LLM. The only agent dispatch v2 adds is `edge-judge`, orchestrated by the `rgit-capture` skill.

---

## 3. Feature A — Edge-aware recall ranking

### Problem
`recall.py` today is `store.find_features(query)`, a single SQL `LIKE '%query%'` over a couple of columns: no scoring (results are in insertion order), and `%`/`_` in a query are treated as SQL wildcards (the §12 bug).

### Design
Matching moves out of SQL into Python. A v2 graph is on the order of hundreds of capsules, so loading all features and scoring them in memory is both correct and fast — and it removes the wildcard bug by construction (no `LIKE`).

New module `src/rgit/ranking.py`, a pure function with no I/O:

```python
def tokenize(text: str) -> list[str]:
    """Lowercase, split on non-alphanumeric, drop empties. Deterministic."""

def lexical_score(capsule: Capsule, query_tokens: list[str]) -> float:
    """Weighted field-hit score for one capsule against the query tokens.

    Field weights:
      intent                       x3
      knobs, result_summary        x2
      code_slices (code/symbol/file stems), resurrection_guide   x1
    Structural boost: +2 per query token that exactly equals a slice symbol
    or a slice file stem (e.g. token 'loss' == symbol 'loss').
    A token contributes a field's weight once per field it appears in (set
    membership), so longer text does not inflate the score.
    """

def score(capsule, query_tokens, neighbor_lexical: list[float], alpha: float = 0.5) -> float:
    """Final edge-aware score.

    return lexical_score(capsule, query_tokens) + alpha * max(neighbor_lexical, default=0.0)
    """
```

`recall.recall(store, query)` becomes:

1. `tokens = tokenize(query)`; if empty, return `[]`.
2. Compute `lexical_score` for every capsule once; cache by id.
3. For each capsule, gather one-hop neighbors over `depends_on` + `conflicts_with`, look up their cached lexical scores, compute the final `score`.
4. Drop capsules whose **own** `lexical_score` is 0 (a capsule surfaces on its own merits, not purely because a neighbor matched — the neighbor only boosts ranking).
5. Sort by final score descending; ties broken by capsule name (stable, deterministic).
6. Return `[{"capsule", "score", "depends_on": [...], "conflicts_with": [...]}]` — each hit now carries both subgraphs.

`alpha = 0.5` is a module constant, not yet user-configurable (YAGNI).

### Interface
- In: `Store`, query string.
- Out: ranked list of dicts (capsule + score + two subgraphs).
- Depends on: `store.list_features`, `store.neighbors`, `ranking.py`.

---

## 4. Feature B — Edge inference

### Problem
The `edges` table exists and `recall` already *reads* `depends_on` neighbors, but nothing *writes* `depends_on` or `conflicts_with`. Only the manual `touches`/`produced`/`variant_of` edges from curation exist.

### Design
Two halves: a deterministic engine module and an agent-judged step.

New module `src/rgit/edges.py`:

```python
def conflict_pairs(store) -> list[tuple[str, str]]:
    """Every unordered pair of capsules that share a (file, symbol) in their
    code_slices. Deterministic, confidence 1.0."""

def apply_conflicts(store) -> int:
    """Write conflicts_with for each conflict pair, symmetric (two directed rows
    a->b and b->a so neighbors() works in either direction). Idempotent via the
    edges UNIQUE(src,dst,type) constraint. Returns the number of pairs written."""

def depends_candidates(store) -> list[dict]:
    """Emit depends_on CANDIDATES — writes nothing.

    For each ordered pair (X, Y), X is a candidate to depend_on Y when a name
    USED in X's slice code intersects the set of symbol names DEFINED by Y's
    slices. 'Names used' come from a libcst Name-collection visitor over each
    slice's code; 'names defined' are the slice symbols of Y.
    Returns [{src, dst, evidence: [shared names]}] for the judge to rule on.
    Skips pairs that already have a depends_on edge."""
```

The `conflicts_with` half is fully deterministic and runs without an agent. The `depends_on` half is a name-reference *heuristic that over-produces on purpose* — it generates candidates and hands them to an agent, because true dependency (does X actually rely on Y's symbol, or do both merely mention a common name like `forward`?) needs judgment.

The agent is a new dedicated subagent, `agents/edge-judge.md` (a graph-reasoning persona, not the segmenter). It receives the candidate list plus the two capsules' slices and returns, for each candidate, a keep/drop decision with a one-line reason.

### CLI surface (`rgit edges`)
- `rgit edges --apply` — write `conflicts_with` deterministically; print the `depends_on` candidates as JSON to stdout (for a skill or a human to act on).
- `rgit edges --candidates` — print candidates JSON only; write nothing.
- `rgit edges --add depends_on SRC DST` — write one judged `depends_on` edge (directed SRC→DST).

### Orchestration
The `rgit-capture` skill, after a proposal is approved into a capsule, runs the incremental edge step:

1. `rgit edges --apply` → conflicts written, candidates emitted as JSON.
2. If candidates exist, dispatch the `edge-judge` subagent over them.
3. For each candidate the judge confirms, `rgit edges --add depends_on SRC DST`.

This keeps the engine/agent split clean: deterministic conflicts and candidate generation in the CLI, judgment in the plugin plane, write-back through a deterministic CLI verb.

### Interface
- `edges.py` in: `Store`. Out: pairs / candidate dicts / edge writes.
- Depends on: `store.list_features`, `store.add_edge`, `store.neighbors`, a libcst `Name` collector (new small helper, may live in `astmap.py` or `edges.py`).

---

## 5. Feature C — Plane split (query-only MCP)

### Problem
`mcp_server.py` still exposes `pending_captures_tool` and `resegment_tool` — the intelligence-adjacent write surface that principle 7 says belongs in the local plane, not the shared-memory plane. The `rgit-capture` skill currently drives its write path through these MCP tools.

### Design
Move the write path from MCP to the CLI; MCP keeps only deterministic reads.

- Remove `pending_captures_tool` and `resegment_tool` from `mcp_server.py` (and their `mcp.tool()` registrations). Remaining tools: `recall`, `compose`, `get_feature`, `get_run`, `list_features` — all query-only.
- Add CLI verbs backed by the existing store methods:
  - `rgit pending [--json]` — list open proposals; `--json` emits `[{proposal_id, trigger, diff, candidates}]` (the shape the skill consumed from `pending_captures`). The diff is re-materialized from the proposal's `diff_ref`.
  - `rgit resegment <proposal_id> --from-json -` — read a capsules JSON array from a file or stdin (`-`) and replace the proposal's candidates via `store.set_proposal_candidates`. Does not auto-approve; capture stays human-gated through `rgit review`.
- Rewrite `skills/rgit-capture/SKILL.md` to call `rgit pending --json` and `rgit resegment` instead of the MCP tools. `skills/rgit-recall/SKILL.md` is untouched (it only uses `recall`/`compose`, which remain).

`compose` stays in MCP: it is a deterministic read (it returns reference slices, the live `current_source`, and conflicts) with no agent and no write, so it is query-plane.

### Interface
- CLI in: proposal id, capsules JSON. Out: proposal listing / candidate replacement.
- Depends on: `store.list_proposals`, `store.get_proposal`, `store.set_proposal_candidates`, `gitutil` to re-materialize the diff from `diff_ref`.

---

## 6. Feature D — `returncode` on runs

### Problem
`run_experiment` discards `proc.returncode`. A crashed experiment is stored byte-for-byte like a successful one (§12).

### Design
- Add `returncode INTEGER` to the `runs` table, with an ALTER migration in `init_schema` mirroring the existing `from_features` migration (detect missing column via `PRAGMA table_info`, `ALTER TABLE runs ADD COLUMN returncode INTEGER`).
- Add `returncode: Optional[int]` to `store.models.Run`; persist and read it in `store.add_run` / `store.get_run`.
- `runner.run_experiment` records `proc.returncode`. Freeze-before-parse is unchanged — a nonzero return still freezes the artifact and records the run.
- Surface it: a run is `ok` when `returncode == 0`. `get_run` (CLI + MCP) exposes the field so a failed run is visibly distinct. No behavior change to segmentation — a failed run still stages a proposal (the messy code is still worth capturing).

### Interface
- In: subprocess return code. Out: persisted integer on the run node.
- Depends on: schema migration, `models.Run`, `store.add_run`/`get_run`, `runner`.

---

## 7. Feature E — Comment-in/out toggles as events

### Problem
Researchers toggle a feature by commenting its block in or out. v1 sees this as an ordinary diff; the semantic "this feature was turned off here" is lost.

### Design
New module `src/rgit/toggles.py`:

```python
def detect_toggles(diff: str) -> list[dict]:
    """Find comment-in/out toggles in a unified diff.

    A toggle is a hunk region where a removed line and an added line differ ONLY
    by a leading comment marker. Python-only: marker is '#' plus optional single
    space, matching the libcst scope of the rest of the engine.
      code -> comment   = deactivate   (a '#'-prefixed added line equals a removed code line)
      comment -> code   = activate     (a removed '#'-prefixed line equals an added code line)
    Returns [{file, line, kind: 'activate'|'deactivate', text}]."""

def map_to_capsules(store, toggles: list[dict]) -> list[dict]:
    """Map each toggle's (file, line) to a capsule whose code_slices overlap that
    line range (reuse astmap line ranges). Returns [{capsule_id, kind}] for
    toggles that land inside a known capsule; unmapped toggles are dropped."""
```

New `events` table:

```sql
CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    capsule_id TEXT NOT NULL,
    kind TEXT NOT NULL,          -- 'activate' | 'deactivate'
    run_id TEXT,                 -- the run/proposal trigger this was observed in, if any
    created_at TEXT NOT NULL
);
```

Wiring: `segment_diff` (the deterministic phase-1, run by `rgit run`, `rgit capture`, and the daemon) calls `detect_toggles` on the same diff it already has, maps to capsules, and records an event per mapped toggle via a new `store.add_event`. This is free and adds no agent.

Surfacing: a capsule's **current activation state** is the `kind` of its latest event (none = unknown/active by default). `recall` and `compose` can include `active: bool|None` so a session can see "this feature is currently commented out."

### Interface
- `toggles.py` in: diff string (+ store for mapping). Out: event dicts.
- Depends on: `astmap` line ranges, `store.add_event`, `store.latest_event`.

---

## 8. Feature F — Ambient capture daemon

### Problem
Capture only fires on an explicit `rgit run` or commit. In a long, messy editing session, intermediate states — often where an idea is clearest before it gets entangled — are never staged.

### Design
New module `src/rgit/watch.py`, built around a pure, testable core:

```python
def snapshot(store) -> dict:
    """A cheap fingerprint of the worktree: {relpath: mtime_ns} over git-tracked
    and untracked-not-ignored files (via `git status`/walk). No file contents."""

def tick(store, last_snapshot: dict, now: str) -> tuple[dict, Optional[str]]:
    """One watch step. Returns (new_snapshot, staged_proposal_id_or_None).

    If new_snapshot != last_snapshot AND the tree has been idle (unchanged since
    the previous tick — caller enforces the idle delay between ticks), run the
    free deterministic phase-1: segment_diff with the HeuristicSegmenter, which
    also records toggle events (E). Otherwise stage nothing and just return the
    new snapshot. Deterministic given (snapshot, now); no agent, no network."""
```

CLI `rgit watch [--interval N] [--idle N] [--once]`:
- A foreground loop that calls `tick` every `--interval` seconds, treating the tree as idle when it was unchanged across the idle window, and prints each staged proposal id.
- `--once` runs exactly one `tick` and exits — the test seam and a cron-friendly mode.
- Backgrounding is documented (`nohup rgit watch &`, or a launchd plist snippet), not implemented as OS daemonization — that keeps the loop trivial and testable. (True service packaging is a v2.x polish item, not core.)

**Honest boundary (agreed in design):** the daemon does ambient **Phase-1** capture only — it keeps the proposal backlog warm. **Phase-2 agent segmentation stays session-driven**: when the user opens a Claude Code session, `/rgit-capture` drains the backlog through `edge-judge`/`capsule-segmenter`. The daemon never dispatches an agent, because the agent needs a session and we use no headless or paid API. There is no plan to add a headless-agent path in v2.

### Interface
- `watch.py` in: `Store`, prior snapshot, timestamp. Out: new snapshot + optional proposal id.
- Depends on: `gitutil` (diff/status), `segmenter.segment_diff`, `toggles`. Stdlib `os.stat` polling — no new dependency.

---

## 9. Data model summary

- `runs` += `returncode INTEGER` (migrated).
- New `events` table (capsule activation/deactivation timeline).
- `edges` now actually populated: `conflicts_with` (deterministic), `depends_on` (judged), alongside the existing `touches`/`produced`/`variant_of`.
- `features`, `proposals`, `objects/` unchanged.

---

## 10. Testing

Pure-function unit tests per module, plus the integration seams:

- **ranking:** score ordering (intent hit outranks guide hit; structural boost applies; edge boost lifts a capsule whose neighbor matches); a query containing `%`/`_` returns sane results (wildcard-safety regression).
- **edges:** `conflict_pairs` finds shared (file,symbol) and ignores disjoint ones; `apply_conflicts` is idempotent and symmetric; `depends_candidates` emits the right candidate with evidence and writes nothing; `--add` roundtrips into `neighbors`.
- **plane split:** the MCP server no longer registers `pending_captures`/`resegment`; `rgit pending --json` then `rgit resegment <id> --from-json -` roundtrips candidate replacement.
- **returncode:** a command exiting nonzero records that code and still freezes + stages.
- **toggles:** `detect_toggles` classifies activate vs deactivate correctly and ignores non-toggle edits; `map_to_capsules` maps inside a slice range and drops outside it; an event is recorded through `segment_diff`.
- **watch:** `tick` stages a proposal on an idle change, stages nothing on no change, is deterministic given `(snapshot, now)`; `--once` CLI seam.

No test, skill, or engine path calls a paid API.

---

## 11. Non-goals (v2)

- **G — embedding-based semantic recall.** Deferred to v3. It needs either a local embedding model (download is network-blocked in this sandbox) or a paid API (disallowed). It slots into the A scorer as a second signal when a local model is available — the ranking interface is designed to accept it without a rewrite.
- **True OS daemonization / launchd packaging.** v2 ships a testable foreground loop and documents backgrounding; service packaging is v2.x polish.
- **Non-Python toggle detection.** Toggle detection is Python/`#`-only, matching the libcst scope of the rest of the engine.
- **User-configurable ranking weights** (`alpha`, field weights). Constants in v2; expose later if needed.

---

## 12. Roadmap context

- **v2 (this spec):** A–F. Graph ranks, has real edges, captures ambiently, records run outcomes and feature on/off state.
- **v2.x polish:** OS service packaging for the daemon; configurable ranking.
- **v3:** G (embedding recall, drops into the A scorer) + the research layer in the parent spec — variant comparison / ablation tables, conflict-merge sophistication, provenance/audit views. **All terminal-only (`rgit` subcommands printing tables); no web dashboard and no editor UI** (explicit v3 non-goals).
