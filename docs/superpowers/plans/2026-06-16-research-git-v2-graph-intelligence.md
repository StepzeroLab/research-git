# research-git v2 — Graph Intelligence + Ambient Capture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the research-git graph rank its recall, carry real `depends_on`/`conflicts_with` edges, record run outcomes and feature on/off state, and capture ambiently — all on a deterministic, zero-paid-API engine plus one new `edge-judge` subagent.

**Architecture:** Each feature is a small pure-function module (`ranking`, `edges`, `toggles`, `watch`) with a thin CLI/loop wrapper, so every unit is testable in isolation. The store gains an `events` table and a `returncode` column. MCP narrows to query-only (the write tools move to the CLI). The only new agent dispatch is `edge-judge`, orchestrated by the `rgit-capture` skill in the plugin plane.

**Tech Stack:** Python 3.11+, SQLite (stdlib), libcst, FastMCP, pytest. Env via `uv` (`.venv/bin/python`, `.venv/bin/pytest`). Git via subprocess.

**Source spec:** `docs/superpowers/specs/2026-06-16-research-git-v2-graph-intelligence-design.md`. Feature G (embeddings) is out of scope (v3).

---

## Conventions for every task

- Run tests with the project venv: `.venv/bin/pytest` (not bare `pytest`/`python`). The repo is already `uv`-provisioned.
- Tests use the `git_repo` fixture in `tests/conftest.py` (an initialized repo with one commit and a `model.py` containing `def forward(x): return x`).
- The MCP/store tools resolve the store from cwd; tests `monkeypatch.chdir(git_repo)` then `Store.init(git_repo)`.
- Commit after each task with the message shown in its final step.

---

## File Structure

**New engine modules**
- `src/rgit/ranking.py` — pure scoring: `tokenize`, `lexical_score`, `score`. (Feature A)
- `src/rgit/edges.py` — `conflict_pairs`, `apply_conflicts`, `depends_candidates`. (Feature B)
- `src/rgit/toggles.py` — `detect_toggles`, `map_to_capsules`. (Feature E)
- `src/rgit/watch.py` — `snapshot`, `tick`, `loop`. (Feature F)

**New plugin asset**
- `src/rgit/_plugin/agents/edge-judge.md` — subagent that judges `depends_on` candidates. (Feature B)

**Modified**
- `src/rgit/store/db.py` — `events` table; `returncode` column + migration. (D, E)
- `src/rgit/store/models.py` — `Run.returncode`; new `Event` dataclass. (D, E)
- `src/rgit/store/store.py` — `returncode` in `add_run`/`get_run`; `add_event`/`latest_event`. (D, E)
- `src/rgit/runner.py` — thread `proc.returncode`; pass `now` to `segment_diff`. (D)
- `src/rgit/recall.py` — rewrite to use `ranking`, return `conflicts_with` + `score`, approved-only. (A)
- `src/rgit/astmap.py` — add `symbol_at_line`. (E)
- `src/rgit/segmenter.py` — `segment_diff` records toggle events, gains `now` param. (E)
- `src/rgit/mcp_server.py` — drop `pending_captures_tool`/`resegment_tool`; widen `recall_tool` shape. (A, C)
- `src/rgit/cli.py` — add `edges`, `pending`, `resegment`, `watch` subcommands. (B, C, F)
- `src/rgit/_plugin/skills/rgit-capture/SKILL.md` — CLI write path + post-approve edge step. (B, C)

**New tests**
- `tests/test_ranking.py`, `tests/test_edges.py`, `tests/test_toggles.py`, `tests/test_watch.py`.
- Modified: `tests/test_recall.py`, `tests/test_mcp_server.py`, `tests/test_runner.py`, `tests/test_store.py`, `tests/test_cli.py`.

---

## Task 1: Store schema — `events` table + run `returncode`

**Files:**
- Modify: `src/rgit/store/db.py`
- Test: `tests/test_db.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_db.py`:

```python
def test_events_table_and_run_returncode_exist(tmp_path):
    from rgit.store.db import connect, init_schema
    conn = connect(tmp_path / "g.db")
    init_schema(conn)
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "events" in tables
    run_cols = {r[1] for r in conn.execute("PRAGMA table_info(runs)")}
    assert "returncode" in run_cols


def test_returncode_migration_adds_column_to_old_runs(tmp_path):
    import sqlite3
    from rgit.store.db import init_schema
    # simulate a pre-v2 db: runs without returncode
    conn = sqlite3.connect(tmp_path / "old.db")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE runs (id TEXT PRIMARY KEY, cmd TEXT NOT NULL, "
                 "artifact_hash TEXT NOT NULL, metrics TEXT, base_commit TEXT NOT NULL, "
                 "env TEXT, created_at TEXT NOT NULL)")
    conn.commit()
    init_schema(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(runs)")}
    assert "returncode" in cols
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_db.py -q`
Expected: FAIL — `events` not in tables / `returncode` missing.

- [ ] **Step 3: Implement**

In `src/rgit/store/db.py`, add the `returncode` column to the `runs` CREATE and add the `events` table inside the `SCHEMA` string. The `runs` block becomes:

```python
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    cmd TEXT NOT NULL,
    artifact_hash TEXT NOT NULL,
    metrics TEXT,
    base_commit TEXT NOT NULL,
    env TEXT,
    created_at TEXT NOT NULL,
    returncode INTEGER
);
```

And add, after the `proposals` table in `SCHEMA`:

```python
CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    capsule_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    run_id TEXT,
    created_at TEXT NOT NULL
);
```

Extend `init_schema` to migrate older `runs` tables (mirror the existing `from_features` migration):

```python
def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    # migrate older graphs that predate columns
    pcols = {r[1] for r in conn.execute("PRAGMA table_info(proposals)")}
    if "from_features" not in pcols:
        conn.execute("ALTER TABLE proposals ADD COLUMN from_features TEXT")
    rcols = {r[1] for r in conn.execute("PRAGMA table_info(runs)")}
    if "returncode" not in rcols:
        conn.execute("ALTER TABLE runs ADD COLUMN returncode INTEGER")
    conn.commit()
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_db.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/rgit/store/db.py tests/test_db.py
git commit -m "feat(store): events table + run returncode column (schema + migration)"
```

---

## Task 2: Models + store — `Run.returncode`, `Event`, `add_event`/`latest_event`

**Files:**
- Modify: `src/rgit/store/models.py`, `src/rgit/store/store.py`
- Test: `tests/test_store.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_store.py`:

```python
def test_run_roundtrip_carries_returncode(git_repo):
    from rgit.store.store import Store
    from rgit.store.models import Run
    store = Store.init(git_repo)
    rid = store.add_run(Run(id="", cmd="x", artifact_hash="h", metrics=None,
                            base_commit="abc", env=None, created_at="t",
                            returncode=1))
    assert store.get_run(rid).returncode == 1


def test_add_and_latest_event(git_repo):
    from rgit.store.store import Store
    store = Store.init(git_repo)
    store.add_event("feat_1", "deactivate", "run_1", "t1")
    store.add_event("feat_1", "activate", "run_2", "t2")
    latest = store.latest_event("feat_1")
    assert latest.kind == "activate"
    assert store.latest_event("feat_unknown") is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_store.py -q`
Expected: FAIL — `Run.__init__` rejects `returncode` / `add_event` missing.

- [ ] **Step 3: Implement**

In `src/rgit/store/models.py`, add `returncode` to `Run` (default keeps back-compat) and a new `Event` dataclass. The `Run` dataclass becomes:

```python
@dataclass
class Run:
    id: str
    cmd: str
    artifact_hash: str
    metrics: Optional[dict]
    base_commit: str
    env: Optional[dict]
    created_at: str
    returncode: Optional[int] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Run":
        return cls(**d)
```

Add after the `Run` dataclass:

```python
@dataclass
class Event:
    id: str
    capsule_id: str
    kind: str               # "activate" | "deactivate"
    run_id: Optional[str]
    created_at: str
```

In `src/rgit/store/store.py`, update the import line to include `Event`:

```python
from .models import Capsule, Run, Proposal, Event
```

Replace `add_run` and `get_run` so they persist/read `returncode` (the table now has 8 columns):

```python
    def add_run(self, run: Run) -> str:
        rid = run.id or new_id("run_")
        self.conn.execute(
            "INSERT INTO runs VALUES (?,?,?,?,?,?,?,?)",
            (rid, run.cmd, run.artifact_hash,
             json.dumps(run.metrics) if run.metrics is not None else None,
             run.base_commit, json.dumps(run.env) if run.env else None,
             run.created_at, run.returncode))
        self.conn.commit()
        return rid

    def get_run(self, rid: str) -> Run:
        row = self.conn.execute("SELECT * FROM runs WHERE id=?", (rid,)).fetchone()
        if row is None:
            raise KeyError(rid)
        return Run(id=row["id"], cmd=row["cmd"], artifact_hash=row["artifact_hash"],
                   metrics=json.loads(row["metrics"]) if row["metrics"] else None,
                   base_commit=row["base_commit"],
                   env=json.loads(row["env"]) if row["env"] else None,
                   created_at=row["created_at"], returncode=row["returncode"])
```

Add event methods at the end of the `Store` class:

```python
    # ---- events -------------------------------------------------------
    def add_event(self, capsule_id: str, kind: str, run_id: Optional[str],
                  created_at: str) -> str:
        eid = new_id("evt_")
        self.conn.execute("INSERT INTO events VALUES (?,?,?,?,?)",
                          (eid, capsule_id, kind, run_id, created_at))
        self.conn.commit()
        return eid

    def latest_event(self, capsule_id: str) -> Optional[Event]:
        row = self.conn.execute(
            "SELECT * FROM events WHERE capsule_id=? ORDER BY created_at DESC, id DESC "
            "LIMIT 1", (capsule_id,)).fetchone()
        if row is None:
            return None
        return Event(id=row["id"], capsule_id=row["capsule_id"], kind=row["kind"],
                     run_id=row["run_id"], created_at=row["created_at"])
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_store.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/rgit/store/models.py src/rgit/store/store.py tests/test_store.py
git commit -m "feat(store): Run.returncode + Event model with add_event/latest_event"
```

---

## Task 3: Feature D — runner records `returncode`

**Files:**
- Modify: `src/rgit/runner.py`
- Test: `tests/test_runner.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_runner.py`:

```python
def test_failed_run_records_nonzero_returncode(git_repo):
    from rgit.runner import run_experiment
    from rgit.segmenter import HeuristicSegmenter
    from rgit.store.store import Store
    store = Store.init(git_repo)
    run_id, _ = run_experiment(store, ["python3", "-c", "import sys; sys.exit(3)"],
                               HeuristicSegmenter(), now="t")
    assert store.get_run(run_id).returncode == 3
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_runner.py -q`
Expected: FAIL — `returncode` is `None`.

- [ ] **Step 3: Implement**

In `src/rgit/runner.py`, record `proc.returncode` on the `Run` and pass `now` through to `segment_diff` (so toggle events get a timestamp — wired in Task 8). The body of `run_experiment` becomes:

```python
    base = current_commit(store.root)
    proc = subprocess.run(cmd, cwd=store.root, capture_output=True, text=True)
    # Freeze BEFORE parsing metrics: the compute is already spent, so nothing
    # downstream (a bad metric line, etc.) may cost us the reproducible artifact.
    artifact = freeze_worktree(store.root, store.objects)
    metrics = parse_metrics(proc.stdout, store.root)
    run_id = store.add_run(Run(
        id="", cmd=" ".join(cmd), artifact_hash=artifact, metrics=metrics,
        base_commit=base, env=env, created_at=now, returncode=proc.returncode))
    for src in (from_features or []):
        store.add_edge(src, run_id, "produced")     # source capsule's lineage -> this run
    prop_id = segment_diff(store, trigger="run", segmenter=segmenter, run_id=run_id,
                           from_features=from_features, now=now)
    return run_id, prop_id
```

> Note: `segment_diff` does not yet accept `now`. Task 8 adds that parameter. If you implement strictly in order, temporarily call `segment_diff(..., from_features=from_features)` here and add `now=now` in Task 8. Either way the final state must pass `now=now`.

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_runner.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/rgit/runner.py tests/test_runner.py
git commit -m "feat(runner): record subprocess returncode on the run node (feature D)"
```

---

## Task 4: Feature A (core) — `ranking.py`

**Files:**
- Create: `src/rgit/ranking.py`
- Test: `tests/test_ranking.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_ranking.py`:

```python
from rgit.ranking import tokenize, lexical_score, score
from rgit.store.models import Capsule, CodeSlice


def _cap(name="f", intent="", guide="", knobs=None, slices=None):
    return Capsule(id="", name=name, intent=intent, status="approved",
                   base_commit="abc", knobs=knobs or {}, data_assumptions=None,
                   resurrection_guide=guide, result_summary=None, payload_hash=None,
                   code_slices=slices or [])


def test_tokenize_lowercases_and_splits():
    assert tokenize("Entropy-Reg Loss!") == ["entropy", "reg", "loss"]


def test_wildcard_query_is_safe():
    cap = _cap(intent="add entropy loss")
    # %/_ must not blow up or act as wildcards — they are just non-matching tokens
    assert lexical_score(cap, tokenize("%_%")) == 0.0


def test_intent_hit_outranks_guide_hit():
    in_intent = _cap(name="a", intent="entropy regularizer")
    in_guide = _cap(name="b", guide="entropy regularizer")
    toks = tokenize("entropy")
    assert lexical_score(in_intent, toks) > lexical_score(in_guide, toks)


def test_structural_boost_on_symbol_match():
    plain = _cap(intent="loss tweak")
    structural = _cap(intent="loss tweak",
                      slices=[CodeSlice("train.py", "loss", None, "code", "wrap")])
    toks = tokenize("loss")
    assert lexical_score(structural, toks) > lexical_score(plain, toks)


def test_score_adds_edge_boost():
    cap = _cap(intent="entropy")
    toks = tokenize("entropy")
    base = score(cap, toks, [])
    boosted = score(cap, toks, [10.0])
    assert boosted == base + 0.5 * 10.0
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_ranking.py -q`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement**

Create `src/rgit/ranking.py`:

```python
from __future__ import annotations
import json
import re

from .store.models import Capsule

ALPHA = 0.5  # weight of the best matching one-hop neighbor
_WORD = re.compile(r"[A-Za-z0-9]+")


def tokenize(text: str) -> list[str]:
    """Lowercase, split on non-alphanumerics, drop empties. Deterministic."""
    if not text:
        return []
    return [w for w in _WORD.findall(text.lower())]


def _hits(text: str, tokens: set[str], weight: float) -> float:
    """weight x (number of distinct query tokens present in `text`)."""
    if not text:
        return 0.0
    words = set(tokenize(text))
    return weight * len(tokens & words)


def lexical_score(capsule: Capsule, query_tokens: list[str]) -> float:
    """Weighted field-hit score for one capsule against the query tokens.

    Weights: intent/name x3 ; knobs/result_summary x2 ; code/guide x1.
    Structural boost: +2 per query token that exactly equals a slice symbol or
    a slice file stem. A token counts once per field (set membership), so longer
    text does not inflate the score. Wildcard-safe: no SQL, pure Python.
    """
    toks = set(query_tokens)
    s = 0.0
    s += _hits(capsule.intent, toks, 3.0)
    s += _hits(capsule.name, toks, 3.0)
    s += _hits(json.dumps(capsule.knobs), toks, 2.0)
    if capsule.result_summary is not None:
        s += _hits(json.dumps(capsule.result_summary.__dict__), toks, 2.0)
    s += _hits(capsule.resurrection_guide or "", toks, 1.0)
    for sl in capsule.code_slices:
        s += _hits(sl.code or "", toks, 1.0)
        if sl.symbol and sl.symbol.lower() in toks:
            s += 2.0
        stem = sl.file.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()
        if stem in toks:
            s += 2.0
    return s


def score(capsule: Capsule, query_tokens: list[str],
          neighbor_lexical: list[float], alpha: float = ALPHA) -> float:
    """Edge-aware final score: own lexical + alpha * best matching neighbor."""
    return lexical_score(capsule, query_tokens) + alpha * max(neighbor_lexical, default=0.0)
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_ranking.py -q`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/rgit/ranking.py tests/test_ranking.py
git commit -m "feat(ranking): wildcard-safe, edge-aware capsule scorer (feature A core)"
```

---

## Task 5: Feature A (integration) — rewrite `recall.py` + widen `recall_tool`

**Files:**
- Modify: `src/rgit/recall.py`, `src/rgit/mcp_server.py`
- Test: `tests/test_recall.py`

- [ ] **Step 1: Write the failing test**

Replace the body of `tests/test_recall.py` with (keeps the original two assertions, adds ranking + conflicts):

```python
from rgit.recall import recall
from rgit.store.store import Store
from rgit.store.models import Capsule, CodeSlice


def _cap(name, intent):
    return Capsule(id="", name=name, intent=intent, status="approved",
                   base_commit="abc", knobs={}, data_assumptions=None,
                   resurrection_guide="...", result_summary=None, payload_hash=None,
                   code_slices=[CodeSlice("model.py", "forward", "L1", "x", "wrap")])


def test_recall_returns_match_with_depends_on_subgraph(git_repo):
    store = Store.init(git_repo)
    base = store.add_feature(_cap("projection-head", "add projection head"))
    loss = store.add_feature(_cap("contrastive-loss", "add aux contrastive loss"))
    store.add_edge(loss, base, "depends_on")
    results = recall(store, "contrastive")
    assert len(results) == 1
    assert results[0]["capsule"].name == "contrastive-loss"
    assert results[0]["depends_on"][0].name == "projection-head"
    assert "score" in results[0]
    assert "conflicts_with" in results[0]


def test_recall_no_match_returns_empty(git_repo):
    store = Store.init(git_repo)
    store.add_feature(_cap("dropout", "raise dropout"))
    assert recall(store, "transformer") == []


def test_recall_ranks_by_score(git_repo):
    store = Store.init(git_repo)
    store.add_feature(_cap("weak", "mentions entropy once"))
    store.add_feature(_cap("entropy-strong", "entropy entropy regularizer entropy"))
    results = recall(store, "entropy")
    assert [r["capsule"].name for r in results][0] == "entropy-strong"


def test_recall_includes_conflicts_subgraph(git_repo):
    store = Store.init(git_repo)
    a = store.add_feature(_cap("alpha", "entropy a"))
    b = store.add_feature(_cap("beta", "unrelated b"))
    store.add_edge(a, b, "conflicts_with")
    results = recall(store, "entropy")
    assert results[0]["conflicts_with"][0].name == "beta"


def test_recall_skips_non_approved(git_repo):
    store = Store.init(git_repo)
    cap = _cap("proposed-one", "entropy proposed")
    cap.status = "proposed"
    store.add_feature(cap)
    assert recall(store, "entropy") == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_recall.py -q`
Expected: FAIL — no `score`/`conflicts_with` keys, ranking absent.

- [ ] **Step 3: Implement**

Replace `src/rgit/recall.py` entirely:

```python
from __future__ import annotations
from .ranking import tokenize, lexical_score, score
from .store.store import Store


def recall(store: Store, query: str) -> list[dict]:
    """Edge-aware ranked recall over approved capsules.

    Each hit carries its score and both one-hop subgraphs (depends_on,
    conflicts_with). A capsule surfaces on its own lexical merit; a matching
    neighbor only boosts its rank.
    """
    tokens = tokenize(query)
    if not tokens:
        return []
    caps = [c for c in store.list_features() if c.status == "approved"]
    by_id = {c.id: c for c in caps}
    lex = {c.id: lexical_score(c, tokens) for c in caps}

    results = []
    for c in caps:
        if lex[c.id] <= 0:
            continue
        dep_ids = store.neighbors(c.id, "depends_on")
        conf_ids = store.neighbors(c.id, "conflicts_with")
        neigh_lex = [lex[i] for i in (dep_ids + conf_ids) if i in lex]
        results.append({
            "capsule": c,
            "score": score(c, tokens, neigh_lex),
            "depends_on": [by_id[i] for i in dep_ids if i in by_id],
            "conflicts_with": [by_id[i] for i in conf_ids if i in by_id],
        })
    results.sort(key=lambda r: (-r["score"], r["capsule"].name))
    return results
```

Then widen `recall_tool` in `src/rgit/mcp_server.py` to expose the new fields:

```python
def recall_tool(query: str) -> list[dict]:
    """Find feature capsules by keyword/structure; ranked, with subgraphs."""
    store = Store.open()
    return [{"capsule": _capsule_dict(r["capsule"]),
             "score": r["score"],
             "depends_on": [_capsule_dict(d) for d in r["depends_on"]],
             "conflicts_with": [_capsule_dict(d) for d in r["conflicts_with"]]}
            for r in recall(store, query)]
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_recall.py tests/test_mcp_server.py -q`
Expected: PASS for `test_recall.py`. `test_mcp_server.py`'s recall test still passes (it only asserts `name` + `depends_on` present). The `pending_captures`/`resegment` MCP test still passes here — it is removed in Task 11.

- [ ] **Step 5: Commit**

```bash
git add src/rgit/recall.py src/rgit/mcp_server.py tests/test_recall.py
git commit -m "feat(recall): edge-aware ranked recall + conflicts subgraph (feature A)"
```

---

## Task 6: Feature B (engine) — `edges.py`

**Files:**
- Create: `src/rgit/edges.py`
- Test: `tests/test_edges.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_edges.py`:

```python
from rgit.edges import conflict_pairs, apply_conflicts, depends_candidates
from rgit.store.store import Store
from rgit.store.models import Capsule, CodeSlice


def _cap(name, slices):
    return Capsule(id="", name=name, intent=f"{name} intent", status="approved",
                   base_commit="abc", knobs={}, data_assumptions=None,
                   resurrection_guide="...", result_summary=None, payload_hash=None,
                   code_slices=slices)


def test_conflict_pairs_share_file_and_symbol(git_repo):
    store = Store.init(git_repo)
    a = store.add_feature(_cap("a", [CodeSlice("m.py", "loss", None, "x", "wrap")]))
    b = store.add_feature(_cap("b", [CodeSlice("m.py", "loss", None, "y", "wrap")]))
    store.add_feature(_cap("c", [CodeSlice("m.py", "other", None, "z", "wrap")]))
    pairs = conflict_pairs(store)
    assert {a, b} in [set(p) for p in pairs]
    assert len(pairs) == 1


def test_apply_conflicts_is_symmetric_and_idempotent(git_repo):
    store = Store.init(git_repo)
    a = store.add_feature(_cap("a", [CodeSlice("m.py", "loss", None, "x", "wrap")]))
    b = store.add_feature(_cap("b", [CodeSlice("m.py", "loss", None, "y", "wrap")]))
    assert apply_conflicts(store) == 1
    assert apply_conflicts(store) == 1  # idempotent: pair count unchanged
    assert b in store.neighbors(a, "conflicts_with")
    assert a in store.neighbors(b, "conflicts_with")


def test_depends_candidates_emits_without_writing(git_repo):
    store = Store.init(git_repo)
    # y DEFINES symbol `Encoder`; x USES the name `Encoder` in its slice code
    y = store.add_feature(_cap("enc", [CodeSlice("e.py", "Encoder", None,
                                                 "class Encoder: pass", "add")]))
    x = store.add_feature(_cap("head", [CodeSlice("h.py", "Head", None,
                                                  "h = Encoder()", "add")]))
    cands = depends_candidates(store)
    assert {"src": x, "dst": y, "evidence": ["Encoder"]} in cands
    # nothing was written
    assert store.neighbors(x, "depends_on") == []


def test_depends_candidates_skips_existing_edges(git_repo):
    store = Store.init(git_repo)
    y = store.add_feature(_cap("enc", [CodeSlice("e.py", "Encoder", None,
                                                 "class Encoder: pass", "add")]))
    x = store.add_feature(_cap("head", [CodeSlice("h.py", "Head", None,
                                                  "h = Encoder()", "add")]))
    store.add_edge(x, y, "depends_on")
    assert depends_candidates(store) == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_edges.py -q`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement**

Create `src/rgit/edges.py`:

```python
from __future__ import annotations
import re

from .store.store import Store

_IDENT = re.compile(r"[A-Za-z_]\w*")


def _approved(store: Store):
    return [c for c in store.list_features() if c.status == "approved"]


def _used_names(code: str) -> set[str]:
    """Identifiers referenced in slice code. Tolerant of diff markers: a leading
    +/- (but not the +++/--- file headers) is stripped before scanning."""
    names: set[str] = set()
    for line in (code or "").splitlines():
        if line[:1] in "+-" and not line.startswith(("+++", "---")):
            line = line[1:]
        names.update(_IDENT.findall(line))
    return names


def conflict_pairs(store: Store) -> list[tuple[str, str]]:
    """Unordered capsule pairs sharing a (file, symbol). Deterministic."""
    caps = _approved(store)
    keys = {c.id: {(s.file, s.symbol) for s in c.code_slices if s.symbol} for c in caps}
    pairs = []
    for i in range(len(caps)):
        for j in range(i + 1, len(caps)):
            if keys[caps[i].id] & keys[caps[j].id]:
                pairs.append((caps[i].id, caps[j].id))
    return pairs


def apply_conflicts(store: Store) -> int:
    """Write conflicts_with for each pair, symmetric. Idempotent (UNIQUE edge).
    Returns the number of conflicting pairs."""
    pairs = conflict_pairs(store)
    for a, b in pairs:
        store.add_edge(a, b, "conflicts_with")
        store.add_edge(b, a, "conflicts_with")
    return len(pairs)


def depends_candidates(store: Store) -> list[dict]:
    """Emit depends_on CANDIDATES (writes nothing). X is a candidate to depend_on
    Y when a name used in X's slice code intersects the symbols Y defines. Skips
    pairs that already carry a depends_on edge."""
    caps = _approved(store)
    defines = {c.id: {s.symbol for s in c.code_slices if s.symbol} for c in caps}
    uses = {c.id: set().union(*[_used_names(s.code) for s in c.code_slices])
            if c.code_slices else set() for c in caps}
    out = []
    for x in caps:
        existing = set(store.neighbors(x.id, "depends_on"))
        for y in caps:
            if x.id == y.id or y.id in existing:
                continue
            shared = uses[x.id] & defines[y.id]
            if shared:
                out.append({"src": x.id, "dst": y.id, "evidence": sorted(shared)})
    return out
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_edges.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/rgit/edges.py tests/test_edges.py
git commit -m "feat(edges): deterministic conflicts + depends_on candidate generation (feature B)"
```

---

## Task 7: Feature E (core) — `astmap.symbol_at_line` + `toggles.py`

**Files:**
- Modify: `src/rgit/astmap.py`
- Create: `src/rgit/toggles.py`
- Test: `tests/test_toggles.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_toggles.py`:

```python
from rgit.toggles import detect_toggles, map_to_capsules
from rgit.store.store import Store
from rgit.store.models import Capsule, CodeSlice

DEACTIVATE_DIFF = """diff --git a/train.py b/train.py
--- a/train.py
+++ b/train.py
@@ -1,3 +1,3 @@
 def loss(x):
-    return entropy(x)
+    # return entropy(x)
     return 0
"""

ACTIVATE_DIFF = """diff --git a/train.py b/train.py
--- a/train.py
+++ b/train.py
@@ -1,3 +1,3 @@
 def loss(x):
-    # return entropy(x)
+    return entropy(x)
     return 0
"""

NON_TOGGLE_DIFF = """diff --git a/train.py b/train.py
--- a/train.py
+++ b/train.py
@@ -1,2 +1,2 @@
 def loss(x):
-    return a
+    return b
"""


def test_detect_deactivate():
    toggles = detect_toggles(DEACTIVATE_DIFF)
    assert len(toggles) == 1
    assert toggles[0]["kind"] == "deactivate"
    assert toggles[0]["file"] == "train.py"


def test_detect_activate():
    toggles = detect_toggles(ACTIVATE_DIFF)
    assert [t["kind"] for t in toggles] == ["activate"]


def test_non_toggle_edit_is_ignored():
    assert detect_toggles(NON_TOGGLE_DIFF) == []


def test_map_to_capsules_matches_by_file_and_symbol(git_repo):
    store = Store.init(git_repo)
    (git_repo / "train.py").write_text(
        "def loss(x):\n    # return entropy(x)\n    return 0\n")
    fid = store.add_feature(Capsule(
        id="", name="entropy", intent="entropy loss", status="approved",
        base_commit="abc", knobs={}, data_assumptions=None, resurrection_guide="...",
        result_summary=None, payload_hash=None,
        code_slices=[CodeSlice("train.py", "loss", None, "code", "wrap")]))
    mapped = map_to_capsules(store, detect_toggles(DEACTIVATE_DIFF))
    assert mapped == [{"capsule_id": fid, "kind": "deactivate"}]
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_toggles.py -q`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement `symbol_at_line` in `src/rgit/astmap.py`**

Add at the end of `src/rgit/astmap.py`:

```python
def symbol_at_line(repo: Path, file: str, line: int) -> Optional[str]:
    """Name of the top-level def/class enclosing `line` (1-based), or None."""
    path = repo / file
    if path.suffix != ".py" or not path.exists():
        return None
    wrapper = MetadataWrapper(cst.parse_module(path.read_text()))
    finder = _SymbolFinder([(line, line)])
    wrapper.visit(finder)
    found = sorted(finder.found)
    return found[0] if found else None
```

- [ ] **Step 4: Implement `src/rgit/toggles.py`**

Create `src/rgit/toggles.py`:

```python
from __future__ import annotations
import re

from .astmap import symbol_at_line
from .store.store import Store

_FILE = re.compile(r"^\+\+\+ b/(.+)$")
_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
_COMMENT = re.compile(r"^(\s*)#\s?(.*)$")


def _decomment(s: str) -> tuple[bool, str]:
    """(is_comment, code-body-stripped). Python '#' only, one optional space."""
    m = _COMMENT.match(s)
    if m:
        return True, (m.group(1) + m.group(2)).strip()
    return False, s.strip()


def detect_toggles(diff: str) -> list[dict]:
    """Find comment-in/out toggles in a unified diff (Python '#' only).

    code -> comment = deactivate ; comment -> code = activate. The reported
    `line` is the new-side line number of the added line.
    Returns [{file, line, kind, text}].
    """
    out: list[dict] = []
    file = None
    new_ln = 0
    removed_code: set[str] = set()       # stripped code bodies removed in this hunk
    removed_comment_bodies: set[str] = set()
    for raw in diff.splitlines():
        mf = _FILE.match(raw)
        if mf:
            file = mf.group(1)
            removed_code, removed_comment_bodies = set(), set()
            continue
        mh = _HUNK.match(raw)
        if mh:
            new_ln = int(mh.group(1))
            removed_code, removed_comment_bodies = set(), set()
            continue
        if file is None or raw.startswith(("diff --git", "--- ", "index ")):
            continue
        if raw.startswith("-") and not raw.startswith("---"):
            is_c, body = _decomment(raw[1:])
            (removed_comment_bodies if is_c else removed_code).add(body)
            continue
        if raw.startswith("+") and not raw.startswith("+++"):
            is_c, body = _decomment(raw[1:])
            if is_c and body in removed_code:
                out.append({"file": file, "line": new_ln, "kind": "deactivate",
                            "text": raw[1:]})
            elif (not is_c) and body in removed_comment_bodies:
                out.append({"file": file, "line": new_ln, "kind": "activate",
                            "text": raw[1:]})
            new_ln += 1
            continue
        # context line advances the new-side counter
        new_ln += 1
    return out


def map_to_capsules(store: Store, toggles: list[dict]) -> list[dict]:
    """Map each toggle to an approved capsule whose code_slices cover it.

    Prefer an exact (file, symbol) match; fall back to file-only when the
    enclosing symbol can't be resolved. Returns [{capsule_id, kind}].
    """
    caps = [c for c in store.list_features() if c.status == "approved"]
    out = []
    for t in toggles:
        sym = symbol_at_line(store.root, t["file"], t["line"])
        for c in caps:
            hit = any(sl.file == t["file"] and (sym is None or sl.symbol == sym)
                      for sl in c.code_slices)
            if hit:
                out.append({"capsule_id": c.id, "kind": t["kind"]})
                break
    return out
```

- [ ] **Step 5: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_toggles.py tests/test_astmap.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/rgit/astmap.py src/rgit/toggles.py tests/test_toggles.py
git commit -m "feat(toggles): comment-in/out toggle detection + capsule mapping (feature E core)"
```

---

## Task 8: Feature E (integration) — `segment_diff` records events

**Files:**
- Modify: `src/rgit/segmenter.py`, `src/rgit/cli.py`
- Test: `tests/test_segmenter.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_segmenter.py`:

```python
def test_segment_diff_records_toggle_event(git_repo):
    import subprocess
    from rgit.segmenter import segment_diff, HeuristicSegmenter
    from rgit.store.store import Store
    from rgit.store.models import Capsule, CodeSlice
    store = Store.init(git_repo)
    # an approved capsule covering loss() in train.py
    (git_repo / "train.py").write_text("def loss(x):\n    return entropy(x)\n")
    subprocess.run(["git", "add", "."], cwd=git_repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-q", "-m", "add train"], cwd=git_repo,
                   check=True, capture_output=True)
    fid = store.add_feature(Capsule(
        id="", name="entropy", intent="entropy loss", status="approved",
        base_commit="abc", knobs={}, data_assumptions=None, resurrection_guide="...",
        result_summary=None, payload_hash=None,
        code_slices=[CodeSlice("train.py", "loss", None, "code", "wrap")]))
    # now comment the feature out in the working tree
    (git_repo / "train.py").write_text("def loss(x):\n    # return entropy(x)\n")
    segment_diff(store, "manual", HeuristicSegmenter(), run_id=None, now="t9")
    latest = store.latest_event(fid)
    assert latest is not None and latest.kind == "deactivate"
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_segmenter.py -q`
Expected: FAIL — `segment_diff` has no `now` param / records no event.

- [ ] **Step 3: Implement**

In `src/rgit/segmenter.py`, change the `segment_diff` signature and body to record toggle events:

```python
def segment_diff(store: Store, trigger: str, segmenter: Segmenter,
                 run_id: Optional[str], from_features: Optional[list[str]] = None,
                 now: str = "") -> str:
    """Diff the working tree vs HEAD, segment it, store an open Proposal, and
    record comment-in/out toggle events against the capsules they touch.

    `from_features` records the capsule(s) this work regenerated, so approving the
    resulting proposal links the new capsule `variant_of` those sources.
    """
    from .toggles import detect_toggles, map_to_capsules
    diff = diff_since(store.root, "HEAD")
    symbols = changed_symbols(diff, store.root)
    candidates = segmenter.segment(diff, symbols)
    diff_ref = store.objects.put(diff.encode())
    pid = store.add_proposal(Proposal(
        id="", trigger=trigger, diff_ref=diff_ref,
        candidates=candidates, status="open", run_id=run_id,
        from_features=from_features))
    for ev in map_to_capsules(store, detect_toggles(diff)):
        store.add_event(ev["capsule_id"], ev["kind"], run_id, now)
    return pid
```

In `src/rgit/cli.py`, update the `capture` branch to pass `now` (so manual captures timestamp their events):

```python
    if args.cmd == "capture":
        pid = segment_diff(store, args.trigger, _segmenter(), run_id=None, now=_now())
        print(f"proposal {pid} created")
        return 0
```

Also confirm the Task 3 `runner.py` call passes `now=now` (it does in the final Task 3 code).

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_segmenter.py tests/test_runner.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/rgit/segmenter.py src/rgit/cli.py tests/test_segmenter.py
git commit -m "feat(segmenter): record toggle activation/deactivation events on capture (feature E)"
```

---

## Task 9: Feature F (core) — `watch.py`

**Files:**
- Create: `src/rgit/watch.py`
- Test: `tests/test_watch.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_watch.py`:

```python
from rgit.watch import snapshot, tick
from rgit.store.store import Store


def _dirty(repo):
    (repo / "model.py").write_text("def forward(x):\n    return x + 1\n")


def test_tick_waits_for_idle_then_stages(git_repo):
    store = Store.init(git_repo)
    _dirty(git_repo)
    snap = snapshot(store)
    # first tick: tree moved relative to an empty prior snapshot -> no staging
    snap2, pid = tick(store, {}, now="t1")
    assert pid is None
    # second tick: snapshot unchanged since `snap2` -> idle -> stage
    snap3, pid2 = tick(store, snap2, now="t2")
    assert pid2 is not None
    assert len(store.list_proposals("open")) == 1


def test_tick_dedupes_already_staged_state(git_repo):
    store = Store.init(git_repo)
    _dirty(git_repo)
    snap = snapshot(store)
    _, pid = tick(store, snap, now="t1")     # idle immediately (same snapshot) -> stage
    assert pid is not None
    _, pid2 = tick(store, snap, now="t2")    # same diff already staged -> skip
    assert pid2 is None
    assert len(store.list_proposals("open")) == 1


def test_tick_idle_clean_tree_stages_nothing(git_repo):
    store = Store.init(git_repo)
    snap = snapshot(store)
    _, pid = tick(store, snap, now="t1")
    assert pid is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_watch.py -q`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement**

Create `src/rgit/watch.py`:

```python
from __future__ import annotations
from typing import Callable, Optional

from .gitutil import _snapshot_paths, diff_since
from .segmenter import HeuristicSegmenter, segment_diff
from .store.store import Store


def snapshot(store: Store) -> dict:
    """Cheap worktree fingerprint: {relpath: mtime_ns} over tracked/untracked-not-
    ignored files, excluding the .rgit/ store itself. No file contents."""
    snap: dict[str, int] = {}
    for rel in _snapshot_paths(store.root, exclude_root=store.dir):
        p = store.root / rel
        try:
            snap[rel] = p.stat().st_mtime_ns
        except FileNotFoundError:
            continue
    return snap


def tick(store: Store, last_snapshot: dict, now: str) -> tuple[dict, Optional[str]]:
    """One watch step. Returns (new_snapshot, staged_proposal_id_or_None).

    Stages a free Phase-1 proposal only when the tree is IDLE (unchanged since the
    prior snapshot) AND has an uncommitted diff that is not already staged in an
    open proposal. Deterministic given (snapshot, tree, now). Never calls an agent.
    """
    cur = snapshot(store)
    if cur != last_snapshot:
        return cur, None                                  # still moving — debounce
    diff = diff_since(store.root, "HEAD")
    if not diff.strip():
        return cur, None                                  # idle but nothing to capture
    for p in store.list_proposals("open"):
        if p.diff_ref and store.objects.get(p.diff_ref).decode() == diff:
            return cur, None                              # this exact state already staged
    pid = segment_diff(store, "watch", HeuristicSegmenter(), run_id=None, now=now)
    return cur, pid


def loop(store: Store, interval: float, idle: float,
         now_fn: Callable[[], str]) -> None:  # pragma: no cover - timing loop
    """Foreground watch loop. Background it with `nohup rgit watch &` or launchd."""
    import time
    last = snapshot(store)
    while True:
        time.sleep(max(interval, idle))
        last, pid = tick(store, last, now_fn())
        if pid:
            print(f"staged proposal {pid}")
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_watch.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/rgit/watch.py tests/test_watch.py
git commit -m "feat(watch): ambient Phase-1 capture daemon core (feature F)"
```

---

## Task 10: CLI — `edges`, `pending`, `resegment`, `watch` subcommands

**Files:**
- Modify: `src/rgit/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cli.py` (match the file's existing import/invocation style — `from rgit import cli`, `cli.main([...])`, `monkeypatch.chdir`, `capsys`):

```python
def test_pending_and_resegment_roundtrip(git_repo, monkeypatch, capsys, tmp_path):
    import json
    from rgit import cli
    from rgit.store.store import Store
    from rgit.store.models import Proposal
    monkeypatch.chdir(git_repo)
    store = Store.init(git_repo)
    diff_ref = store.objects.put(b"some diff text")
    pid = store.add_proposal(Proposal(id="", trigger="run", diff_ref=diff_ref,
                                      candidates=[{"name": "rough"}]))
    cli.main(["pending", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert out[0]["proposal_id"] == pid
    assert out[0]["diff"] == "some diff text"

    payload = tmp_path / "caps.json"
    payload.write_text(json.dumps([{"name": "refined", "intent": "better"}]))
    cli.main(["resegment", pid, "--from-json", str(payload)])
    assert store.get_proposal(pid).candidates == [{"name": "refined", "intent": "better"}]


def test_edges_apply_writes_conflicts_and_emits_candidates(git_repo, monkeypatch, capsys):
    import json
    from rgit import cli
    from rgit.store.store import Store
    from rgit.store.models import Capsule, CodeSlice
    monkeypatch.chdir(git_repo)
    store = Store.init(git_repo)

    def cap(name, slices):
        return Capsule(id="", name=name, intent=f"{name}", status="approved",
                       base_commit="abc", knobs={}, data_assumptions=None,
                       resurrection_guide="...", result_summary=None, payload_hash=None,
                       code_slices=slices)
    a = store.add_feature(cap("a", [CodeSlice("m.py", "loss", None, "x", "wrap")]))
    b = store.add_feature(cap("b", [CodeSlice("m.py", "loss", None, "y", "wrap")]))
    cli.main(["edges", "--apply"])
    res = json.loads(capsys.readouterr().out)
    assert res["conflicts_written"] == 1
    assert b in store.neighbors(a, "conflicts_with")


def test_edges_add_writes_depends_on(git_repo, monkeypatch, capsys):
    from rgit import cli
    from rgit.store.store import Store
    monkeypatch.chdir(git_repo)
    store = Store.init(git_repo)
    cli.main(["edges", "--add", "depends_on", "feat_x", "feat_y"])
    assert "feat_y" in store.neighbors("feat_x", "depends_on")


def test_watch_once_stages_proposal(git_repo, monkeypatch, capsys):
    from rgit import cli
    from rgit.store.store import Store
    monkeypatch.chdir(git_repo)
    Store.init(git_repo)
    (git_repo / "model.py").write_text("def forward(x):\n    return x + 1\n")
    cli.main(["watch", "--once"])
    assert "staged proposal" in capsys.readouterr().out
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_cli.py -q`
Expected: FAIL — unknown subcommands `pending`/`resegment`/`edges`/`watch`.

- [ ] **Step 3: Implement**

In `src/rgit/cli.py`, register the new subparsers (add after the `features`/`mcp` parsers, before `install`):

```python
    p_edges = sub.add_parser("edges")
    p_edges.add_argument("--apply", action="store_true")
    p_edges.add_argument("--candidates", action="store_true")
    p_edges.add_argument("--add", nargs=3, metavar=("TYPE", "SRC", "DST"))

    p_pend = sub.add_parser("pending")
    p_pend.add_argument("--json", action="store_true")

    p_reseg = sub.add_parser("resegment")
    p_reseg.add_argument("proposal_id")
    p_reseg.add_argument("--from-json", dest="from_json", required=True,
                         metavar="PATH", help="file path, or - for stdin")

    p_watch = sub.add_parser("watch")
    p_watch.add_argument("--interval", type=float, default=5.0)
    p_watch.add_argument("--idle", type=float, default=5.0)
    p_watch.add_argument("--once", action="store_true")
```

Add the handler branches after the `features` branch (and before `return 1`):

```python
    if args.cmd == "edges":
        from . import edges as edgesmod
        if args.add:
            etype, src, dst = args.add
            store.add_edge(src, dst, etype)
            print(f"edge {src} -{etype}-> {dst}")
            return 0
        if args.apply:
            n = edgesmod.apply_conflicts(store)
            cands = edgesmod.depends_candidates(store)
            print(json.dumps({"conflicts_written": n, "depends_candidates": cands},
                             indent=2, ensure_ascii=False))
            return 0
        if args.candidates:
            print(json.dumps(edgesmod.depends_candidates(store), indent=2,
                             ensure_ascii=False))
            return 0
        print("nothing to do (use --apply, --candidates, or --add)")
        return 1

    if args.cmd == "pending":
        items = []
        for p in store.list_proposals("open"):
            diff = store.objects.get(p.diff_ref).decode() if p.diff_ref else ""
            items.append({"proposal_id": p.id, "trigger": p.trigger,
                          "diff": diff, "candidates": p.candidates})
        if args.json:
            print(json.dumps(items, indent=2, ensure_ascii=False))
        else:
            for it in items:
                print(f"{it['proposal_id']}  [{it['trigger']}]  "
                      f"{len(it['candidates'])} candidate(s)")
        return 0

    if args.cmd == "resegment":
        import sys
        from pathlib import Path
        raw = sys.stdin.read() if args.from_json == "-" else Path(args.from_json).read_text()
        candidates = json.loads(raw)
        store.set_proposal_candidates(args.proposal_id, candidates)
        print(f"resegmented {args.proposal_id}: {len(candidates)} candidate(s)")
        return 0

    if args.cmd == "watch":
        from . import watch as watchmod
        if args.once:
            snap = watchmod.snapshot(store)
            _, pid = watchmod.tick(store, snap, _now())
            print(f"staged proposal {pid}" if pid else "nothing to capture")
            return 0
        watchmod.loop(store, interval=args.interval, idle=args.idle, now_fn=_now)
        return 0
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_cli.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/rgit/cli.py tests/test_cli.py
git commit -m "feat(cli): edges, pending, resegment, watch subcommands (B/C/F surface)"
```

---

## Task 11: Feature C — narrow MCP to query-only

**Files:**
- Modify: `src/rgit/mcp_server.py`
- Test: `tests/test_mcp_server.py`

- [ ] **Step 1: Write the failing test**

In `tests/test_mcp_server.py`, **delete** `test_pending_captures_and_resegment_round_trip` (its capability now lives in `tests/test_cli.py::test_pending_and_resegment_roundtrip`) and the now-unused `Proposal` import. **Add**:

```python
def test_intelligence_tools_are_not_registered():
    # the write/intelligence-adjacent tools moved to the CLI (plane split)
    assert not hasattr(srv, "pending_captures_tool")
    assert not hasattr(srv, "resegment_tool")


def test_recall_tool_exposes_score_and_conflicts(git_repo, monkeypatch):
    monkeypatch.chdir(git_repo)
    store = Store.init(git_repo)
    a = store.add_feature(_cap("alpha"))
    b = store.add_feature(_cap("beta"))
    store.add_edge(a, b, "conflicts_with")
    out = srv.recall_tool("alpha")
    assert "score" in out[0]
    assert "conflicts_with" in out[0]
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_mcp_server.py -q`
Expected: FAIL — `pending_captures_tool`/`resegment_tool` still exist.

- [ ] **Step 3: Implement**

In `src/rgit/mcp_server.py`, delete the `pending_captures_tool` and `resegment_tool` function definitions and their two `mcp.tool()(...)` registration lines. The registration block becomes:

```python
# Register as MCP tools (functions remain directly unit-testable).
mcp.tool()(recall_tool)
mcp.tool()(compose_tool)
mcp.tool()(get_feature_tool)
mcp.tool()(list_features_tool)
```

`recall_tool` already has the widened shape from Task 5. `compose_tool` stays (it is a deterministic read = query plane).

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_mcp_server.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/rgit/mcp_server.py tests/test_mcp_server.py
git commit -m "feat(mcp): drop pending_captures/resegment — MCP is query-only (feature C)"
```

---

## Task 12: Plugin plane — `edge-judge` agent + rewrite `rgit-capture` skill

**Files:**
- Create: `src/rgit/_plugin/agents/edge-judge.md`
- Modify: `src/rgit/_plugin/skills/rgit-capture/SKILL.md`
- Test: `tests/test_installer.py` (asset-presence assertion)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_installer.py`:

```python
def test_edge_judge_agent_is_packaged():
    from rgit import installer
    agents = installer.plugin_dir() / "agents"
    assert (agents / "edge-judge.md").exists()


def test_capture_skill_uses_cli_not_mcp_write_tools():
    from rgit import installer
    skill = (installer.plugin_dir() / "skills" / "rgit-capture" / "SKILL.md").read_text()
    assert "rgit pending" in skill
    assert "rgit resegment" in skill
    assert "pending_captures" not in skill     # MCP write tools are gone
    assert "resegment(" not in skill
```

> Check `installer.plugin_dir()` resolves to `src/rgit/_plugin` (it does — it's the packaged plugin root used by the installer). If the helper name differs, use the existing accessor from `tests/test_installer.py`.

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_installer.py -q`
Expected: FAIL — `edge-judge.md` missing; skill still references MCP write tools.

- [ ] **Step 3: Create the `edge-judge` agent**

Create `src/rgit/_plugin/agents/edge-judge.md`:

```markdown
---
name: edge-judge
description: |
  Judges candidate depends_on edges between research Feature Capsules. The deterministic engine over-produces candidates from name overlap; this agent decides which are real dependencies versus coincidental shared names. Runs on the host session's subscription, never a paid API.
---

# Edge Judge

You are a senior ML research engineer with deep experience reasoning about how experimental features depend on one another in a codebase. You are skeptical of coincidence: two capsules sharing a common name like `forward`, `loss`, or `config` do not necessarily form a dependency. A real `depends_on` means capsule X genuinely relies on a symbol that capsule Y introduces — remove Y and X no longer works as intended.

## Your input (provided in the dispatch prompt)

- `candidates` — a list of `{src, dst, evidence}` objects. `src` depends_on `dst` is the hypothesis; `evidence` is the set of shared names that triggered the candidate.
- `capsules` — for each capsule id referenced, its `name`, `intent`, and `code_slices` (so you can see what each defines and uses).

## Your job

For each candidate, decide: does `src` actually depend on `dst`?

- **Confirm** when the shared name is a meaningful symbol that `dst` introduces and `src` consumes (a class, function, or config key that is the point of `dst`).
- **Reject** when the overlap is coincidental: a common builtin, a generic method name, a parameter that both happen to use, or a name that neither capsule actually owns.

When unsure, **reject** — a missing edge is cheaper than a wrong one, and the deterministic `conflicts_with` edges already capture same-region overlap.

## Output

Return JSON only:

```json
{"confirmed": [{"src": "feat_x", "dst": "feat_y", "reason": "x instantiates Encoder, defined by y"}],
 "rejected":  [{"src": "feat_a", "dst": "feat_b", "reason": "shared name 'forward' is coincidental"}]}
```
```

- [ ] **Step 4: Rewrite the `rgit-capture` skill**

Replace `src/rgit/_plugin/skills/rgit-capture/SKILL.md` with (CLI write path + post-approve edge step; natural long lines, no hard wrapping):

```markdown
---
name: rgit-capture
description: |
  Turn pending research-git captures into high-quality Feature Capsules and wire up their graph edges. Use when the user wants to "segment", "capture", or "clean up" their recent experimental changes into the research-git graph, or after an `rgit run` / commit / `rgit watch` has left open proposals. Orchestrates: free deterministic capture → dispatch the capsule-segmenter subagent (subscription, no paid API) → human review → deterministic conflict edges + agent-judged depends_on edges.
---

# rgit-capture

Orchestrates the **two-phase capture** that the research-git design calls for: a free deterministic Phase 1, then an agentic Phase 2 dispatched natively onto the host session's subscription. No pay-per-use API is ever called. The `rgit` CLI is the deterministic engine and the read/write surface; MCP is query-only and is not used here.

**Prerequisites:** the target repo has been `rgit init`-ed. Everything below runs through the `rgit` CLI.

## Process

### 1. Ensure there are proposals to segment (Phase 1 — free, deterministic)

If the user just made changes and there is no open proposal yet, create one:

```
rgit capture --trigger manual
```

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

### 6. Infer graph edges (deterministic conflicts + agent-judged depends_on)

After approval, wire the new capsule into the graph:

```
rgit edges --apply
```

This deterministically writes `conflicts_with` edges (capsules touching the same file+symbol) and prints `depends_candidates` as JSON — over-produced `{src, dst, evidence}` hypotheses from name overlap.

If there are candidates, dispatch the **`edge-judge`** subagent (`agents/edge-judge.md`) with the candidate list and the referenced capsules' names/intents/slices. For each pair the judge confirms, write the edge:

```
rgit edges --add depends_on <src> <dst>
```

Reject coincidental overlaps — a missing edge is cheaper than a wrong one. The confirmed `depends_on` and the deterministic `conflicts_with` edges are what make `recall` rank related work together.

## Notes

- **No paid API.** All LLM work here is the dispatched `capsule-segmenter` and `edge-judge` subagents, which run on this session's subscription.
- **Phase 1 vs Phase 2.** `rgit` (diff + libcst + heuristic) is the deterministic substrate; the subagents are the semantic layer. Same split as the Understand-Anything plugin (deterministic extraction → dispatched analyzer).
- **Regeneration** (recalling a capsule and re-applying it onto today's code) is the sibling flow — see the `capsule-regenerator` agent driven off `recall` + `compose` (the `rgit-recall` skill).
```

- [ ] **Step 5: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_installer.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/rgit/_plugin/agents/edge-judge.md src/rgit/_plugin/skills/rgit-capture/SKILL.md tests/test_installer.py
git commit -m "feat(plugin): edge-judge agent + CLI-driven rgit-capture with edge inference (B/C)"
```

---

## Task 13: Full-suite regression + spec status sync

**Files:**
- Modify: `docs/superpowers/specs/2026-06-16-research-git-v2-graph-intelligence-design.md` (status line only)

- [ ] **Step 1: Run the entire test suite**

Run: `.venv/bin/pytest -q`
Expected: PASS — all prior tests plus the v2 additions. If `tests/test_e2e.py` exercises recall ordering or run shape, confirm it still holds; the recall return now includes `score`/`conflicts_with` keys (additive) and `Run` gained `returncode` (defaulted), so existing assertions remain valid.

- [ ] **Step 2: Confirm no paid-API references crept in**

Run: `grep -rn "anthropic\|claude-api\|api_key\|API key" src/rgit || echo "clean"`
Expected: `clean` (or only doc-string mentions of "no paid API").

- [ ] **Step 3: Mark the spec implemented**

In the spec, change the `**Status:**` line to:

```markdown
**Status:** Implemented (A–F). G deferred to v3.
```

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/specs/2026-06-16-research-git-v2-graph-intelligence-design.md
git commit -m "docs: mark v2 (A–F) implemented"
```

---

## Self-Review

**Spec coverage:**
- A (edge-aware ranking) → Tasks 4, 5. ✓
- B (conflicts deterministic + depends_on agent-judged, `rgit edges`, edge-judge, capture orchestration) → Tasks 6, 10, 12. ✓
- C (drop pending_captures/resegment from MCP, add CLI verbs, rewrite skill) → Tasks 10, 11, 12. ✓
- D (returncode) → Tasks 1, 2, 3. ✓
- E (toggles → events table, segment_diff wiring) → Tasks 1, 2, 7, 8. ✓
- F (watch daemon) → Tasks 9, 10. ✓
- Data model (events, returncode, edges populated) → Tasks 1, 2, 6. ✓
- Non-goal G (embeddings) → not implemented, ranking interface accepts a future neighbor/extra signal. ✓

**Type/signature consistency:**
- `segment_diff(store, trigger, segmenter, run_id, from_features=None, now="")` — defined Task 8, called with `now=now` in `runner` (Task 3) and `now=_now()` in CLI capture (Task 8) and `now=now` in `watch.tick` (Task 9). ✓
- `Run(..., returncode=None)` — model Task 2, persisted Task 2, set in runner Task 3. ✓
- `store.add_event(capsule_id, kind, run_id, created_at)` / `latest_event(capsule_id)` — Task 2; called in Task 8. ✓
- `recall()` returns `{capsule, score, depends_on, conflicts_with}` — Task 5; consumed by `recall_tool` Task 5 and tested Task 11. ✓
- `edges.apply_conflicts(store)->int`, `depends_candidates(store)->list[dict]`, `conflict_pairs(store)->list[tuple]` — Task 6; used in CLI Task 10 and skill Task 12. ✓
- `toggles.detect_toggles(diff)->list[dict]`, `map_to_capsules(store, toggles)->list[dict]` — Task 7; used in `segment_diff` Task 8. ✓
- `astmap.symbol_at_line(repo, file, line)` — Task 7; used in `toggles.map_to_capsules` Task 7. ✓
- `watch.snapshot(store)`, `watch.tick(store, last, now)` — Task 9; used in CLI Task 10. ✓
- `ranking.tokenize/lexical_score/score` — Task 4; used in `recall` Task 5. ✓

**Placeholder scan:** none — every code step shows complete code, every test step shows real assertions.

**Note for the implementer on Task 3 ordering:** `runner.run_experiment` is updated to call `segment_diff(..., now=now)` before `segment_diff` formally grows the `now` parameter in Task 8. If executing strictly in task order, the Task 3 call to `segment_diff` will fail at runtime until Task 8 lands. To keep each task green in isolation, in Task 3 call `segment_diff(store, trigger="run", segmenter=segmenter, run_id=run_id, from_features=from_features)` (no `now`), and add `now=now` as part of Task 8. The final committed state has `now=now`. Both the Task 3 and Task 8 test suites pass under that approach.
```