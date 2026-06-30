# research-git v3 — The Research Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a terminal-only research-query layer over the v2 graph — `rgit compare`, `rgit ablation`, `rgit provenance`, conflict-aware regeneration, and a metric-direction config — all pure deterministic reads except the merge step, which reuses the existing `capsule-regenerator` agent.

**Architecture:** Five new pure modules (`compare`, `ablation`, `provenance`, `metricdir`, `tables`) return plain data; `tables.py` and the CLI/MCP layers render. Conflict-merge is an *enrichment* of the existing `compose()` brief plus a regenerator-prompt edit — no new agent. One additive `active` edge type (no migration) and one new `metric_directions` table (idempotent migration mirroring v2's `returncode` migration).

**Tech Stack:** Python 3.11+, SQLite (stdlib), libcst, FastMCP, pytest. Env via `uv`; tests run with `.venv/bin/pytest`. **No paid API anywhere.**

**Conventions for every task:**
- Run tests with `.venv/bin/pytest`, not bare `pytest`.
- Source lives under `src/rgit/`, tests under `tests/`.
- The `git_repo` fixture (in `tests/conftest.py`) gives an initialized git repo with one commit and a `model.py`; `Store.init(git_repo)` opens a store there.
- Capsules/runs are built with the real dataclasses from `rgit.store.models`.

---

## File Structure

| File | New/Edit | Responsibility |
|---|---|---|
| `src/rgit/tables.py` | new | shared terminal table + unified-diff rendering (pure formatting) |
| `src/rgit/metricdir.py` | new | metric-direction config CRUD + heuristic `suggest` (pure) |
| `src/rgit/compare.py` | new | resolve target → variant cluster → ranked metric rows (pure) |
| `src/rgit/ablation.py` | new | powerset → active-set grouping → grid (pure) |
| `src/rgit/provenance.py` | new | run artifact untar → clean-vs-adapted per slice (pure) |
| `src/rgit/store/db.py` | edit | `metric_directions` table + idempotent migration |
| `src/rgit/store/store.py` | edit | metric-dir CRUD; `active_features` / `runs_with_active` readers |
| `src/rgit/runner.py` | edit | `active` capsules → write `active` edges |
| `src/rgit/compose.py` | edit | `conflicts` flag → structured `merge_context` |
| `src/rgit/_plugin/agents/capsule-regenerator.md` | edit | consume `merge_context`, perform real merge |
| `src/rgit/cli.py` | edit | `compare` / `ablation` / `provenance` / `metric-dir` subcommands + `run --with` |
| `src/rgit/mcp_server.py` | edit | read-only `compare` / `ablation` / `provenance` tools |

**Task order (dependency-respecting):**
1. `tables.py` (no deps) → 2. `metricdir` storage (db + store) → 3. `metricdir.py` module → 4. `compare.py` → 5. `active` edges (store + runner) → 6. `ablation.py` → 7. `provenance.py` → 8. `compose` merge_context + regenerator prompt → 9. CLI wiring → 10. MCP wiring.

---

### Task 1: `tables.py` — shared terminal renderer

**Files:**
- Create: `src/rgit/tables.py`
- Test: `tests/test_tables.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tables.py
from rgit.tables import render_table, render_diff


def test_render_table_aligns_columns_and_marks_winner():
    out = render_table(
        headers=["feature", "eval_loss"],
        rows=[["temperature", "1.18"], ["entropy", "1.10"]],
        mark={(1, 1): True},   # row index 1, col index 1 gets the ★
    )
    lines = out.splitlines()
    assert lines[0].split() == ["feature", "eval_loss"]
    assert "★" in lines[-1]                 # winner row carries the marker
    # every data row is padded to the same visual width
    assert len({len(l) for l in lines}) == 1


def test_render_table_no_marks():
    out = render_table(headers=["a", "b"], rows=[["1", "2"]], mark={})
    assert "★" not in out
    assert "a" in out and "b" in out


def test_render_diff_shows_added_and_removed_lines():
    out = render_diff("def f():\n    return 1\n", "def f():\n    return 2\n",
                      label="model.py:f")
    assert "model.py:f" in out
    assert "-    return 1" in out
    assert "+    return 2" in out


def test_render_diff_identical_is_empty_body():
    out = render_diff("x\n", "x\n", label="same")
    assert out.strip() == "same: (identical)"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_tables.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'rgit.tables'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/rgit/tables.py
from __future__ import annotations
import difflib


def render_table(headers: list[str], rows: list[list[str]],
                 mark: dict[tuple[int, int], bool]) -> str:
    """Render a fixed-width column table. `mark[(r, c)]` appends a ★ to that cell.

    All lines (header + rows) are padded to one common width so the output is a
    clean rectangular block in the terminal.
    """
    cells = [list(headers)] + [list(r) for r in rows]
    marked = [[f"{v} ★" if mark.get((ri - 1, ci)) else v
               for ci, v in enumerate(row)]
              for ri, row in enumerate(cells)]
    widths = [max(len(row[c]) for row in marked) for c in range(len(headers))]
    lines = ["  ".join(v.ljust(widths[c]) for c, v in enumerate(row))
             for row in marked]
    full = max(len(l) for l in lines)
    return "\n".join(l.ljust(full) for l in lines)


def render_diff(clean: str, adapted: str, label: str) -> str:
    """Unified diff clean->adapted under a label; '(identical)' when equal."""
    if clean == adapted:
        return f"{label}: (identical)"
    diff = difflib.unified_diff(
        clean.splitlines(), adapted.splitlines(),
        fromfile="clean", tofile="adapted", lineterm="")
    return label + "\n" + "\n".join(diff)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_tables.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/rgit/tables.py tests/test_tables.py
git commit -m "feat(tables): shared terminal table + unified-diff renderer"
```

---

### Task 2: `metric_directions` storage — db migration + Store CRUD

**Files:**
- Modify: `src/rgit/store/db.py:60-69` (the `init_schema` function and `SCHEMA` string)
- Modify: `src/rgit/store/store.py` (add metric-direction methods to the `Store` class, after the events section ending at line 173)
- Test: `tests/test_metricdir_store.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_metricdir_store.py
import sqlite3
from rgit.store.store import Store
from rgit.store.db import connect, init_schema


def test_set_and_get_direction_roundtrip(git_repo):
    store = Store.init(git_repo)
    store.set_metric_direction("eval_loss", "lower")
    store.set_metric_direction("accuracy", "higher")
    assert store.get_metric_direction("eval_loss") == "lower"
    assert store.get_metric_direction("accuracy") == "higher"
    assert store.get_metric_direction("unknown_metric") is None


def test_set_direction_upserts(git_repo):
    store = Store.init(git_repo)
    store.set_metric_direction("m", "lower")
    store.set_metric_direction("m", "higher")    # overwrite, not duplicate
    assert store.get_metric_direction("m") == "higher"
    assert store.list_metric_directions() == {"m": "higher"}


def test_open_migrates_db_without_metric_directions(git_repo, tmp_path):
    """A graph.db created before the metric_directions table still opens."""
    # Build a legacy DB that has every table EXCEPT metric_directions.
    legacy = git_repo / ".rgit" / "graph.db"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(legacy)
    conn.execute("CREATE TABLE features (id TEXT PRIMARY KEY)")  # minimal stand-in
    conn.commit()
    conn.close()
    # Opening through Store must add the table via the idempotent migration.
    store = Store.init(git_repo)
    store.set_metric_direction("eval_loss", "lower")
    assert store.get_metric_direction("eval_loss") == "lower"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_metricdir_store.py -v`
Expected: FAIL with `AttributeError: 'Store' object has no attribute 'set_metric_direction'`

- [ ] **Step 3a: Add the table to the schema**

In `src/rgit/store/db.py`, append this table to the `SCHEMA` string (after the `events` table, before the closing `"""`):

```python
CREATE TABLE IF NOT EXISTS metric_directions (
    metric TEXT PRIMARY KEY,
    direction TEXT NOT NULL
);
```

The `CREATE TABLE IF NOT EXISTS` in `executescript` already adds the table to legacy DBs on every open (the same self-healing path the v2 `returncode` migration relies on), so no extra `ALTER` is needed for this table. Leave the existing `init_schema` migration body unchanged.

- [ ] **Step 3b: Add CRUD methods to Store**

In `src/rgit/store/store.py`, add these methods to the `Store` class (after `latest_event`, at the end of the class, around line 173):

```python
    # ---- metric directions -------------------------------------------
    def set_metric_direction(self, metric: str, direction: str) -> None:
        """Record whether a metric is better when 'higher' or 'lower' (upsert)."""
        self.conn.execute(
            "INSERT INTO metric_directions VALUES (?,?) "
            "ON CONFLICT(metric) DO UPDATE SET direction=excluded.direction",
            (metric, direction))
        self.conn.commit()

    def get_metric_direction(self, metric: str) -> Optional[str]:
        row = self.conn.execute(
            "SELECT direction FROM metric_directions WHERE metric=?",
            (metric,)).fetchone()
        return row["direction"] if row else None

    def list_metric_directions(self) -> dict[str, str]:
        return {r["metric"]: r["direction"] for r in
                self.conn.execute("SELECT metric, direction FROM metric_directions")}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_metricdir_store.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/rgit/store/db.py src/rgit/store/store.py tests/test_metricdir_store.py
git commit -m "feat(store): metric_directions table + upsert CRUD"
```

---

### Task 3: `metricdir.py` — direction resolution + heuristic suggest

**Files:**
- Create: `src/rgit/metricdir.py`
- Test: `tests/test_metricdir.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_metricdir.py
from rgit.metricdir import suggest, best_index
from rgit.store.store import Store


def test_suggest_maps_names_by_heuristic():
    s = suggest(["eval_loss", "val_accuracy", "ppl", "f1", "reward", "mystery"])
    assert s["eval_loss"] == "lower"
    assert s["ppl"] == "lower"
    assert s["val_accuracy"] == "higher"
    assert s["f1"] == "higher"
    assert s["reward"] == "higher"
    assert "mystery" not in s            # no confident guess -> omitted


def test_best_index_lower_picks_minimum(git_repo):
    store = Store.init(git_repo)
    store.set_metric_direction("loss", "lower")
    # values aligned to rows; None means the row has no value for this metric
    assert best_index(store, "loss", [1.2, 0.9, 1.0]) == 1


def test_best_index_higher_picks_maximum(git_repo):
    store = Store.init(git_repo)
    store.set_metric_direction("acc", "higher")
    assert best_index(store, "acc", [0.7, 0.9, None]) == 1


def test_best_index_unknown_direction_returns_none(git_repo):
    store = Store.init(git_repo)
    assert best_index(store, "loss", [1.2, 0.9]) is None   # direction unset


def test_best_index_all_none_returns_none(git_repo):
    store = Store.init(git_repo)
    store.set_metric_direction("loss", "lower")
    assert best_index(store, "loss", [None, None]) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_metricdir.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'rgit.metricdir'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/rgit/metricdir.py
from __future__ import annotations
import re
from typing import Optional

from .store.store import Store

_LOWER = re.compile(r"loss|err|nll|ppl|perplex", re.I)
_HIGHER = re.compile(r"acc|f1|reward|score|bleu|rouge", re.I)


def suggest(metric_names: list[str]) -> dict[str, str]:
    """Heuristic direction guess by metric name. Confident matches only.

    A name matching a 'lower-is-better' token (loss/err/nll/ppl/perplex) maps to
    'lower'; a 'higher-is-better' token (acc/f1/reward/score/bleu/rouge) maps to
    'higher'. Anything unrecognized is omitted so the caller never writes a guess
    it isn't sure about.
    """
    out: dict[str, str] = {}
    for name in metric_names:
        if _LOWER.search(name):
            out[name] = "lower"
        elif _HIGHER.search(name):
            out[name] = "higher"
    return out


def best_index(store: Store, metric: str, values: list[Optional[float]]) -> Optional[int]:
    """Index of the best value per the stored direction, or None.

    Returns None when the metric has no configured direction (never guess) or
    when every value is None. `values` is positional (aligned to the caller's
    rows); None entries are skipped.
    """
    direction = store.get_metric_direction(metric)
    if direction is None:
        return None
    present = [(i, v) for i, v in enumerate(values) if v is not None]
    if not present:
        return None
    pick = min if direction == "lower" else max
    return pick(present, key=lambda iv: iv[1])[0]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_metricdir.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/rgit/metricdir.py tests/test_metricdir.py
git commit -m "feat(metricdir): direction resolution + name-heuristic suggest"
```

---

### Task 4: `compare.py` — variant cluster → ranked metric rows

**Files:**
- Create: `src/rgit/compare.py`
- Test: `tests/test_compare.py`

**Context:** A capsule produces runs via `produced` edges (`capsule_id -> run_id`), written by `runner.run_experiment` for `from_features`. Variants are linked by `variant_of` edges (`new_capsule -> source_capsule`), written by `curation.approve`. `store.neighbors(src, type)` returns outgoing `dst` ids only — to walk `variant_of` in both directions you must also scan incoming edges (use the raw query shown below).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_compare.py
from rgit.compare import compare
from rgit.store.store import Store
from rgit.store.models import Capsule, CodeSlice, Run


def _cap(name, intent="x"):
    return Capsule(id="", name=name, intent=intent, status="approved",
                   base_commit="abc", knobs={}, data_assumptions=None,
                   resurrection_guide=None, result_summary=None, payload_hash=None,
                   code_slices=[CodeSlice("loss.py", "Loss", None, "code", "wrap")])


def _run_with(store, metric_val, at):
    rid = store.add_run(Run(id="", cmd="train", artifact_hash="h", metrics=metric_val,
                            base_commit="abc", env=None, created_at=at))
    return rid


def test_compare_ranks_variant_cluster_by_direction(git_repo):
    store = Store.init(git_repo)
    store.set_metric_direction("eval_loss", "lower")
    a = store.add_feature(_cap("temperature"))
    b = store.add_feature(_cap("label-smoothing"))
    store.add_edge(b, a, "variant_of")              # b is a variant of a
    ra = _run_with(store, {"eval_loss": 1.18}, "2026-01-01T00:00:00")
    rb = _run_with(store, {"eval_loss": 1.10}, "2026-01-02T00:00:00")
    store.add_edge(a, ra, "produced")
    store.add_edge(b, rb, "produced")

    result = compare(store, "temperature")          # target is the cluster anchor
    assert {r["feature"] for r in result["rows"]} == {"temperature", "label-smoothing"}
    assert result["metric"] == "eval_loss"
    winner = [r for r in result["rows"] if r["winner"]]
    assert len(winner) == 1 and winner[0]["feature"] == "label-smoothing"
    # Δ is vs the cluster's earliest run (temperature @ 1.18): label-smoothing = 1.10
    by_name = {r["feature"]: r for r in result["rows"]}
    assert by_name["temperature"]["delta"] == 0.0
    assert by_name["label-smoothing"]["delta"] == -0.08


def test_compare_by_symbol_gathers_touchers(git_repo):
    store = Store.init(git_repo)
    store.add_feature(_cap("a"))
    store.add_feature(_cap("b"))
    result = compare(store, "loss.py:Loss")         # both capsules touch Loss
    assert {r["feature"] for r in result["rows"]} == {"a", "b"}


def test_compare_unknown_direction_no_winner(git_repo):
    store = Store.init(git_repo)
    a = store.add_feature(_cap("temperature"))
    ra = _run_with(store, {"eval_loss": 1.18}, "2026-01-01T00:00:00")
    store.add_edge(a, ra, "produced")
    result = compare(store, "temperature")          # no direction set
    assert all(not r["winner"] for r in result["rows"])
    assert result["metric"] == "eval_loss"


def test_compare_unknown_target_raises(git_repo):
    store = Store.init(git_repo)
    import pytest
    with pytest.raises(KeyError):
        compare(store, "nonexistent")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_compare.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'rgit.compare'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/rgit/compare.py
from __future__ import annotations
from collections import Counter
from typing import Optional

from .metricdir import best_index
from .store.store import Store


def _variant_cluster(store: Store, fid: str) -> list[str]:
    """Transitive closure over variant_of edges in BOTH directions."""
    seen, frontier = {fid}, [fid]
    while frontier:
        cur = frontier.pop()
        out = store.neighbors(cur, "variant_of")
        inc = [r["src"] for r in store.conn.execute(
            "SELECT src FROM edges WHERE dst=? AND type=?", (cur, "variant_of"))]
        for nxt in out + inc:
            if nxt not in seen:
                seen.add(nxt)
                frontier.append(nxt)
    return list(seen)


def _resolve_targets(store: Store, target: str) -> list[str]:
    """A target is `file:symbol`, a bare symbol, or a capsule name/id."""
    caps = store.list_features()
    by_id = {c.id: c for c in caps}
    by_name = {c.name: c for c in caps}
    if target in by_id:
        return _variant_cluster(store, target)
    if target in by_name:
        return _variant_cluster(store, by_name[target].id)
    # symbol target: "file:symbol" or bare "symbol"
    file, _, symbol = target.partition(":")
    want_file, want_sym = (file, symbol) if symbol else (None, target)
    hits = [c.id for c in caps for s in c.code_slices
            if s.symbol == want_sym and (want_file is None or s.file == want_file)]
    if not hits:
        raise KeyError(f"no capsule/symbol matching '{target}'")
    return hits


def _runs_of(store: Store, fid: str) -> list:
    return [store.get_run(rid) for rid in store.neighbors(fid, "produced")]


def _dominant_metric(runs: list) -> Optional[str]:
    names: Counter = Counter()
    for r in runs:
        for k in (r.metrics or {}):
            names[k] += 1
    return names.most_common(1)[0][0] if names else None


def compare(store: Store, target: str, metric: Optional[str] = None,
            direction: Optional[str] = None) -> dict:
    """Rank a variant cluster (or symbol's touchers) by a single metric.

    Returns {"metric", "rows": [{feature, run, value, delta, winner}]}. One metric
    is resolved up front (explicit `metric`, else the most common across the
    cluster's runs) and used for every row, so values and the Δ column are
    comparable. Δ is value minus the cluster's earliest-run value. Direction comes
    from the stored config unless `direction` overrides; an unknown direction
    yields no winner (values still shown).
    """
    fids = _resolve_targets(store, target)
    caps = {c.id: c for c in store.list_features()}
    fids = sorted(fids, key=lambda i: caps[i].name)
    runs_by_fid = {fid: sorted(_runs_of(store, fid), key=lambda r: r.created_at)
                   for fid in fids}
    all_runs = sorted((r for fid in fids for r in runs_by_fid[fid]),
                      key=lambda r: r.created_at)
    chosen_metric = metric or _dominant_metric(all_runs)
    if direction is not None and chosen_metric is not None:
        store.set_metric_direction(chosen_metric, direction)

    baseline = next((r.metrics[chosen_metric] for r in all_runs
                     if chosen_metric and chosen_metric in (r.metrics or {})), None)
    rows, values = [], []
    for fid in fids:
        run = next((r for r in runs_by_fid[fid]
                    if chosen_metric and chosen_metric in (r.metrics or {})), None)
        val = (run.metrics or {}).get(chosen_metric) if run and chosen_metric else None
        delta = round(val - baseline, 6) if (val is not None and baseline is not None) else None
        rows.append({"feature": caps[fid].name, "run": run.id if run else None,
                     "value": val, "delta": delta, "winner": False})
        values.append(val)
    win = best_index(store, chosen_metric, values) if chosen_metric else None
    if win is not None:
        rows[win]["winner"] = True
    return {"metric": chosen_metric, "rows": rows}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_compare.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/rgit/compare.py tests/test_compare.py
git commit -m "feat(compare): variant-cluster ranking by metric direction"
```

---

### Task 5: `active` edges — Store readers + runner `--with`

**Files:**
- Modify: `src/rgit/store/store.py` (add two readers near the edges section, after `neighbors` at line 95)
- Modify: `src/rgit/runner.py:12-36` (the `run_experiment` signature and body)
- Test: `tests/test_active_edges.py`

**Context:** The `edges` table is generic `(src, dst, type)`, so the `active` type needs no migration. An `active` edge points `run_id -> capsule_id`. `active_features(run)` reads outgoing; `runs_with_active(capsule)` reads incoming.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_active_edges.py
from rgit.store.store import Store
from rgit.store.models import Capsule, CodeSlice
from rgit.runner import run_experiment
from rgit.segmenter import MockSegmenter


def _cap(store, name):
    return store.add_feature(Capsule(
        id="", name=name, intent="x", status="approved", base_commit="abc",
        knobs={}, data_assumptions=None, resurrection_guide=None,
        result_summary=None, payload_hash=None,
        code_slices=[CodeSlice("model.py", "forward", None, "x", "wrap")]))


def test_active_edges_round_trip(git_repo):
    store = Store.init(git_repo)
    a, b = _cap(store, "a"), _cap(store, "b")
    store.add_edge("run_1", a, "active")
    store.add_edge("run_1", b, "active")
    assert set(store.active_features("run_1")) == {a, b}
    assert store.runs_with_active(a) == ["run_1"]


def test_run_experiment_writes_active_edges(git_repo):
    store = Store.init(git_repo)
    a = _cap(store, "a")
    run_id, _ = run_experiment(store, ["true"], MockSegmenter(), now="2026-01-01T00:00:00",
                               active=[a])
    assert store.active_features(run_id) == [a]


def test_run_experiment_without_active_writes_none(git_repo):
    store = Store.init(git_repo)
    run_id, _ = run_experiment(store, ["true"], MockSegmenter(), now="2026-01-01T00:00:00")
    assert store.active_features(run_id) == []
```

Check `tests/test_runner.py` or `src/rgit/segmenter.py` for the exact `MockSegmenter` import path; if it differs, match the existing one used in `tests/test_runner.py`.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_active_edges.py -v`
Expected: FAIL with `AttributeError: 'Store' object has no attribute 'active_features'`

- [ ] **Step 3a: Add Store readers**

In `src/rgit/store/store.py`, add after `neighbors` (line 95):

```python
    def active_features(self, run_id: str) -> list[str]:
        """Capsules declared active in a run (run -active-> capsule edges)."""
        return self.neighbors(run_id, "active")

    def runs_with_active(self, capsule_id: str) -> list[str]:
        """Runs that declared this capsule active (incoming active edges)."""
        return [r["src"] for r in self.conn.execute(
            "SELECT src FROM edges WHERE dst=? AND type=?", (capsule_id, "active"))]
```

- [ ] **Step 3b: Thread `active` through the runner**

In `src/rgit/runner.py`, change the signature and add the edge-writing loop. The function becomes:

```python
def run_experiment(store: Store, cmd: list[str], segmenter: Segmenter,
                   now: str, env: Optional[dict] = None,
                   from_features: Optional[list[str]] = None,
                   active: Optional[list[str]] = None) -> tuple[str, str]:
    """Execute an experiment, freeze the artifact, record the run, segment the diff.

    `from_features` marks this run as a regeneration of those capsule(s).
    `active` declares which approved capsules were active in the working tree for
    this run; each gets a `run -active-> capsule` edge so `rgit ablation` can group
    runs by their active-feature set.
    """
    base = current_commit(store.root)
    proc = subprocess.run(cmd, cwd=store.root, capture_output=True, text=True)
    artifact = freeze_worktree(store.root, store.objects)
    metrics = parse_metrics(proc.stdout, store.root)
    run_id = store.add_run(Run(
        id="", cmd=" ".join(cmd), artifact_hash=artifact, metrics=metrics,
        base_commit=base, env=env, created_at=now, returncode=proc.returncode))
    for src in (from_features or []):
        store.add_edge(src, run_id, "produced")
    for cap_id in (active or []):
        store.add_edge(run_id, cap_id, "active")
    prop_id = segment_diff(store, trigger="run", segmenter=segmenter, run_id=run_id,
                           from_features=from_features, now=now)
    return run_id, prop_id
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_active_edges.py tests/test_runner.py -v`
Expected: PASS (existing runner tests still green + 3 new)

- [ ] **Step 5: Commit**

```bash
git add src/rgit/store/store.py src/rgit/runner.py tests/test_active_edges.py
git commit -m "feat(runner): active edges declare a run's active feature set"
```

---

### Task 6: `ablation.py` — powerset → active-set grid

**Files:**
- Create: `src/rgit/ablation.py`
- Test: `tests/test_ablation.py`

**Context:** A run's active set = `store.active_features(run)`, falling back to the run's `produced` capsules (capsules with a `produced` edge to the run) when it has no active edges. The grid rows are the powerset of the requested capsules; a cell holds the latest run whose active set equals that exact subset.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ablation.py
from rgit.ablation import ablation
from rgit.store.store import Store
from rgit.store.models import Capsule, CodeSlice, Run


def _cap(store, name):
    return store.add_feature(Capsule(
        id="", name=name, intent="x", status="approved", base_commit="abc",
        knobs={}, data_assumptions=None, resurrection_guide=None,
        result_summary=None, payload_hash=None,
        code_slices=[CodeSlice("loss.py", "Loss", None, "c", "wrap")]))


def _run(store, metrics, at):
    return store.add_run(Run(id="", cmd="t", artifact_hash="h", metrics=metrics,
                             base_commit="abc", env=None, created_at=at))


def test_ablation_buckets_runs_by_active_subset(git_repo):
    store = Store.init(git_repo)
    store.set_metric_direction("eval_loss", "lower")
    a, b = _cap(store, "A"), _cap(store, "B")
    r_base = _run(store, {"eval_loss": 1.30}, "2026-01-01T00:00:00")    # {}
    r_a = _run(store, {"eval_loss": 1.18}, "2026-01-02T00:00:00")
    store.add_edge(r_a, a, "active")                                    # {A}
    r_ab = _run(store, {"eval_loss": 1.05}, "2026-01-03T00:00:00")
    store.add_edge(r_ab, a, "active"); store.add_edge(r_ab, b, "active")  # {A,B}

    grid = ablation(store, [a, b])
    subsets = {tuple(sorted(row["subset"])): row for row in grid["rows"]}
    assert subsets[()]["cells"]["eval_loss"] == 1.30
    assert subsets[("A",)]["cells"]["eval_loss"] == 1.18
    assert subsets[("A", "B")]["cells"]["eval_loss"] == 1.05
    # {B} alone had no run -> empty cell
    assert subsets[("B",)]["cells"]["eval_loss"] is None
    # winner column marks the lowest eval_loss row ({A,B})
    assert grid["winners"]["eval_loss"] == ("A", "B")


def test_ablation_falls_back_to_produced_edges(git_repo):
    store = Store.init(git_repo)
    a = _cap(store, "A")
    r = _run(store, {"eval_loss": 1.0}, "2026-01-01T00:00:00")
    store.add_edge(a, r, "produced")        # no active edge; produced is the fallback
    grid = ablation(store, [a])
    subsets = {tuple(sorted(row["subset"])): row for row in grid["rows"]}
    assert subsets[("A",)]["cells"]["eval_loss"] == 1.0


def test_ablation_latest_run_wins_a_cell(git_repo):
    store = Store.init(git_repo)
    a = _cap(store, "A")
    old = _run(store, {"eval_loss": 2.0}, "2026-01-01T00:00:00")
    new = _run(store, {"eval_loss": 1.0}, "2026-01-09T00:00:00")
    for r in (old, new):
        store.add_edge(r, a, "active")
    grid = ablation(store, [a])
    subsets = {tuple(sorted(row["subset"])): row for row in grid["rows"]}
    assert subsets[("A",)]["cells"]["eval_loss"] == 1.0     # latest by created_at
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_ablation.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'rgit.ablation'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/rgit/ablation.py
from __future__ import annotations
from itertools import chain, combinations

from .metricdir import best_index
from .store.store import Store


def _powerset(items: list[str]):
    return chain.from_iterable(combinations(items, r) for r in range(len(items) + 1))


def _active_set(store: Store, run_id: str) -> frozenset[str]:
    """A run's active capsules; fall back to produced capsules when none declared."""
    active = store.active_features(run_id)
    if active:
        return frozenset(active)
    produced = [r["src"] for r in store.conn.execute(
        "SELECT src FROM edges WHERE dst=? AND type=?", (run_id, "produced"))]
    return frozenset(produced)


def ablation(store: Store, capsule_ids: list[str], metric: str | None = None) -> dict:
    """Build a base/+A/+A+B grid over the powerset of `capsule_ids`.

    Each subset cell = the latest run whose active set equals that subset exactly.
    Returns {"rows": [{subset(names), run, cells{metric: value}}], "winners": {metric: subset}}.
    """
    caps = {c.id: c for c in store.list_features()}
    name = {cid: caps[cid].name for cid in capsule_ids}
    target = frozenset(capsule_ids)

    # All runs, newest first, indexed by their (intersected-to-target) active set.
    rows_all = store.conn.execute("SELECT id FROM runs").fetchall()
    runs = sorted((store.get_run(r["id"]) for r in rows_all),
                  key=lambda r: r.created_at, reverse=True)
    latest_for: dict[frozenset, object] = {}
    for run in runs:
        key = _active_set(store, run.id) & target
        latest_for.setdefault(key, run)        # newest wins (we iterate desc)

    metric_names: list[str] = []
    rows = []
    for subset in _powerset(capsule_ids):
        run = latest_for.get(frozenset(subset))
        cells = dict(run.metrics) if (run and run.metrics) else {}
        for k in cells:
            if k not in metric_names:
                metric_names.append(k)
        rows.append({"subset": tuple(sorted(name[c] for c in subset)),
                     "run": run.id if run else None, "cells": cells})

    cols = [metric] if metric else metric_names
    for row in rows:                            # ensure every row has every column
        for m in cols:
            row["cells"].setdefault(m, None)

    winners: dict[str, tuple] = {}
    for m in cols:
        idx = best_index(store, m, [row["cells"].get(m) for row in rows])
        if idx is not None:
            winners[m] = rows[idx]["subset"]
    return {"rows": rows, "winners": winners}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_ablation.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/rgit/ablation.py tests/test_ablation.py
git commit -m "feat(ablation): powerset grid grouped by active feature set"
```

---

### Task 7: `provenance.py` — clean vs agent-adapted per slice

**Files:**
- Create: `src/rgit/provenance.py`
- Test: `tests/test_provenance.py`

**Context:** A run's frozen artifact is a deterministic tar at `objects.get(run.artifact_hash)` (see `gitutil.freeze_worktree`). To extract a symbol from the artifact, untar in memory, read the slice's file bytes, and parse with libcst the same way `astmap.read_symbol_source` does — but from in-memory text, not a path. The capsules in scope are those with a `produced` or `active` edge to the run.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_provenance.py
import io, tarfile
from rgit.provenance import provenance
from rgit.store.store import Store
from rgit.store.models import Capsule, CodeSlice, Run


def _freeze(store, files: dict[str, str]) -> str:
    """Write a tar artifact {path: text} into the object store; return its hash."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for path, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo(name=path); info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return store.objects.put(buf.getvalue())


def _cap(store, name, clean_code):
    return store.add_feature(Capsule(
        id="", name=name, intent="x", status="approved", base_commit="abc",
        knobs={}, data_assumptions=None, resurrection_guide=None,
        result_summary=None, payload_hash=None,
        code_slices=[CodeSlice("loss.py", "Loss", None, clean_code, "wrap")]))


def test_provenance_flags_adapted_when_symbol_differs(git_repo):
    store = Store.init(git_repo)
    clean = "class Loss:\n    pass\n"
    adapted = "class Loss:\n    x = 1\n"
    fid = _cap(store, "loss", clean)
    h = _freeze(store, {"loss.py": adapted})
    rid = store.add_run(Run(id="", cmd="t", artifact_hash=h, metrics=None,
                            base_commit="abc", env=None, created_at="2026-01-01T00:00:00"))
    store.add_edge(fid, rid, "produced")
    result = provenance(store, rid)
    slice0 = result["slices"][0]
    assert slice0["flag"] == "adapted"
    assert "x = 1" in slice0["diff"]


def test_provenance_flags_clean_when_identical(git_repo):
    store = Store.init(git_repo)
    code = "class Loss:\n    pass\n"
    fid = _cap(store, "loss", code)
    h = _freeze(store, {"loss.py": code})
    rid = store.add_run(Run(id="", cmd="t", artifact_hash=h, metrics=None,
                            base_commit="abc", env=None, created_at="2026-01-01T00:00:00"))
    store.add_edge(fid, rid, "produced")
    result = provenance(store, rid)
    assert result["slices"][0]["flag"] == "clean"


def test_provenance_flags_missing_when_symbol_absent(git_repo):
    store = Store.init(git_repo)
    fid = _cap(store, "loss", "class Loss:\n    pass\n")
    h = _freeze(store, {"other.py": "x = 1\n"})      # loss.py not in artifact
    rid = store.add_run(Run(id="", cmd="t", artifact_hash=h, metrics=None,
                            base_commit="abc", env=None, created_at="2026-01-01T00:00:00"))
    store.add_edge(fid, rid, "produced")
    result = provenance(store, rid)
    assert result["slices"][0]["flag"] == "missing"


def test_provenance_unknown_run_raises(git_repo):
    store = Store.init(git_repo)
    import pytest
    with pytest.raises(KeyError):
        provenance(store, "run_nope")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_provenance.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'rgit.provenance'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/rgit/provenance.py
from __future__ import annotations
import io
import tarfile
from typing import Optional

import libcst as cst

from .store.store import Store
from .tables import render_diff


def _symbol_from_text(text: str, symbol: str) -> Optional[str]:
    """Source of a top-level def/class in `text`, or None (mirrors astmap)."""
    try:
        module = cst.parse_module(text)
    except cst.ParserSyntaxError:
        return None
    for stmt in module.body:
        if isinstance(stmt, (cst.FunctionDef, cst.ClassDef)) and stmt.name.value == symbol:
            return module.code_for_node(stmt)
    return None


def _artifact_files(store: Store, artifact_hash: str) -> dict[str, str]:
    """Untar a frozen artifact in memory -> {path: text}."""
    blob = store.objects.get(artifact_hash)
    files: dict[str, str] = {}
    with tarfile.open(fileobj=io.BytesIO(blob)) as tar:
        for member in tar.getmembers():
            f = tar.extractfile(member)
            if f is not None:
                files[member.name] = f.read().decode()
    return files


def _capsules_for_run(store: Store, run_id: str) -> list[str]:
    produced = [r["src"] for r in store.conn.execute(
        "SELECT src FROM edges WHERE dst=? AND type=?", (run_id, "produced"))]
    active = store.active_features(run_id)
    seen, out = set(), []
    for fid in produced + active:
        if fid not in seen:
            seen.add(fid); out.append(fid)
    return out


def provenance(store: Store, run_id: str) -> dict:
    """Per-slice clean (capsule) vs adapted (frozen artifact) audit.

    Each slice flag is 'clean' (identical), 'adapted' (differs, with a diff), or
    'missing' (symbol/file absent from the run's artifact).
    """
    run = store.get_run(run_id)                 # raises KeyError on unknown run
    files = _artifact_files(store, run.artifact_hash)
    slices = []
    for fid in _capsules_for_run(store, run_id):
        cap = store.get_feature(fid)
        for s in cap.code_slices:
            if not s.symbol:
                continue
            adapted = _symbol_from_text(files.get(s.file, ""), s.symbol)
            label = f"{cap.name}  {s.file}:{s.symbol}"
            if adapted is None:
                slices.append({"feature": cap.name, "symbol": s.symbol,
                               "flag": "missing", "diff": ""})
            elif adapted == s.code:
                slices.append({"feature": cap.name, "symbol": s.symbol,
                               "flag": "clean", "diff": ""})
            else:
                slices.append({"feature": cap.name, "symbol": s.symbol,
                               "flag": "adapted",
                               "diff": render_diff(s.code, adapted, label)})
    counts = {"clean": 0, "adapted": 0, "missing": 0}
    for sl in slices:
        counts[sl["flag"]] += 1
    return {"run": run_id, "slices": slices, "summary": counts}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_provenance.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/rgit/provenance.py tests/test_provenance.py
git commit -m "feat(provenance): clean-vs-adapted audit from frozen artifact"
```

---

### Task 8: conflict-merge — enrich `compose()` + teach the regenerator

**Files:**
- Modify: `src/rgit/compose.py` (add `merge_context` to the returned dict)
- Modify: `src/rgit/_plugin/agents/capsule-regenerator.md` (consume `merge_context`)
- Test: `tests/test_compose.py` (extend)

**Context:** `compose()` already collects `current_source` per symbol and a `conflicts` list (a `(file, symbol)` touched by >1 capsule). `merge_context` upgrades each conflict into a structured merge brief carrying every contributor's clean slice, intent, and knobs — what the regenerator needs to merge instead of just flag.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_compose.py  (add to the existing file)
from rgit.compose import compose
from rgit.store.store import Store
from rgit.store.models import Capsule, CodeSlice


def _conflict_cap(name, code):
    return Capsule(id="", name=name, intent=f"{name} intent", status="approved",
                   base_commit="abc", knobs={name: 1}, data_assumptions=None,
                   resurrection_guide=None, result_summary=None, payload_hash=None,
                   code_slices=[CodeSlice("loss.py", "Loss", None, code, "wrap")])


def test_compose_builds_merge_context_for_colliding_region(git_repo):
    store = Store.init(git_repo)
    a = store.add_feature(_conflict_cap("entropy", "class Loss: a"))
    b = store.add_feature(_conflict_cap("temperature", "class Loss: b"))
    brief = compose(store, [a, b])
    assert len(brief["merge_context"]) == 1
    mc = brief["merge_context"][0]
    assert mc["file"] == "loss.py" and mc["symbol"] == "Loss"
    names = {c["capsule"] for c in mc["contributors"]}
    assert names == {"entropy", "temperature"}
    # each contributor carries its clean slice + intent + knobs for the merge
    contrib = next(c for c in mc["contributors"] if c["capsule"] == "entropy")
    assert contrib["clean_slice"] == "class Loss: a"
    assert contrib["intent"] == "entropy intent"
    assert contrib["knobs"] == {"entropy": 1}


def test_compose_no_merge_context_without_collision(git_repo):
    store = Store.init(git_repo)
    a = store.add_feature(Capsule(
        id="", name="solo", intent="x", status="approved", base_commit="abc",
        knobs={}, data_assumptions=None, resurrection_guide=None,
        result_summary=None, payload_hash=None,
        code_slices=[CodeSlice("a.py", "Foo", None, "code", "wrap")]))
    brief = compose(store, [a])
    assert brief["merge_context"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_compose.py -v`
Expected: FAIL with `KeyError: 'merge_context'`

- [ ] **Step 3: Add `merge_context` to compose()**

In `src/rgit/compose.py`, replace the final `conflicts`/`return` block with one that also builds `merge_context`. The function's tail becomes:

```python
    conflicts = [{"file": f, "symbol": s, "features": names}
                 for (f, s), names in touch.items() if len(names) > 1]

    by_id = {fid: store.get_feature(fid) for fid in feature_ids}
    name_to_cap = {cap.name: cap for cap in by_id.values()}
    merge_context = []
    for (f, s), names in touch.items():
        if len(names) <= 1:
            continue
        contributors = []
        for nm in names:
            cap = name_to_cap[nm]
            slice_code = next((sl.code for sl in cap.code_slices
                               if sl.file == f and sl.symbol == s), "")
            contributors.append({"capsule": nm, "clean_slice": slice_code,
                                 "intent": cap.intent, "knobs": cap.knobs})
        merge_context.append({
            "file": f, "symbol": s,
            "current_source": read_symbol_source(store.root, f, s) or "",
            "contributors": contributors})

    return {"features": features, "conflicts": conflicts,
            "merge_context": merge_context}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_compose.py -v`
Expected: PASS (existing compose tests still green + 2 new)

- [ ] **Step 5: Teach the regenerator to merge**

In `src/rgit/_plugin/agents/capsule-regenerator.md`, add a section after the existing regeneration instructions:

```markdown
## Merging colliding capsules

The brief may include a `merge_context` array. Each entry describes a single code
region (`file`, `symbol`) that more than one recalled capsule modifies, with:

- `current_source` — the region as it exists in today's tree.
- `contributors[]` — each colliding capsule's `clean_slice`, `intent`, and `knobs`.

When `merge_context` is non-empty, do NOT emit conflicting edits or stop at the
`conflicts_with` flag. Produce ONE coherent implementation of that region that
honors every contributor's intent and knobs together (e.g. a loss that combines
entropy regularization, temperature scaling, and label smoothing in a single
term). Preserve each contributor's knob as a named, independently-toggleable
parameter so the merged result stays ablatable. State in your summary how you
reconciled the intents.
```

- [ ] **Step 6: Commit**

```bash
git add src/rgit/compose.py src/rgit/_plugin/agents/capsule-regenerator.md tests/test_compose.py
git commit -m "feat(compose): structured merge_context for conflict-aware regeneration"
```

---

### Task 9: CLI wiring — compare / ablation / provenance / metric-dir / run --with

**Files:**
- Modify: `src/rgit/cli.py` (add subparsers near line 77; add dispatch branches before the final `return 1`)
- Test: `tests/test_cli.py` (extend)

**Context:** CLI handlers open the store with `Store.open()` (already done at line 104) and print rendered output. Reuse `tables.render_table` for grids, the compute modules for data. `run` gains `--with`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli.py  (add to the existing file)
import json
from rgit.cli import main
from rgit.store.store import Store
from rgit.store.models import Capsule, CodeSlice, Run


def _cap(store, name):
    return store.add_feature(Capsule(
        id="", name=name, intent="x", status="approved", base_commit="abc",
        knobs={}, data_assumptions=None, resurrection_guide=None,
        result_summary=None, payload_hash=None,
        code_slices=[CodeSlice("loss.py", "Loss", None, "c", "wrap")]))


def test_cli_metric_dir_set_and_list(git_repo, capsys, monkeypatch):
    monkeypatch.chdir(git_repo)
    Store.init(git_repo)
    assert main(["metric-dir", "set", "eval_loss", "lower"]) == 0
    capsys.readouterr()
    assert main(["metric-dir", "list"]) == 0
    assert "eval_loss" in capsys.readouterr().out


def test_cli_compare_prints_table(git_repo, capsys, monkeypatch):
    monkeypatch.chdir(git_repo)
    store = Store.init(git_repo)
    store.set_metric_direction("eval_loss", "lower")
    a = _cap(store, "temperature")
    rid = store.add_run(Run(id="", cmd="t", artifact_hash="h",
                            metrics={"eval_loss": 1.1}, base_commit="abc",
                            env=None, created_at="2026-01-01T00:00:00"))
    store.add_edge(a, rid, "produced")
    assert main(["compare", "temperature"]) == 0
    assert "temperature" in capsys.readouterr().out


def test_cli_compare_unknown_target_returns_nonzero(git_repo, capsys, monkeypatch):
    monkeypatch.chdir(git_repo)
    Store.init(git_repo)
    rc = main(["compare", "nope"])
    assert rc == 1
    assert "no capsule" in capsys.readouterr().out.lower()


def test_cli_provenance_prints_summary(git_repo, capsys, monkeypatch):
    import io, tarfile
    monkeypatch.chdir(git_repo)
    store = Store.init(git_repo)
    a = _cap(store, "loss")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        data = b"c"
        info = tarfile.TarInfo("loss.py"); info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    h = store.objects.put(buf.getvalue())
    rid = store.add_run(Run(id="", cmd="t", artifact_hash=h, metrics=None,
                            base_commit="abc", env=None, created_at="2026-01-01T00:00:00"))
    store.add_edge(a, rid, "produced")
    assert main(["provenance", rid]) == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_cli.py -v`
Expected: FAIL — `argument cmd: invalid choice: 'metric-dir'`

- [ ] **Step 3a: Add `--with` to the run subparser**

In `src/rgit/cli.py`, in the `p_run` block (after line 39), add:

```python
    p_run.add_argument("--with", dest="active", action="append", metavar="CAPSULE",
                       help="declare an approved capsule active in this run (repeatable)")
```

And update the `run_experiment` call (line 108) to pass it:

```python
        run_id, prop_id = run_experiment(store, cmd, _segmenter(), now=_now(),
                                         from_features=args.from_features,
                                         active=args.active)
```

- [ ] **Step 3b: Add the new subparsers**

In `src/rgit/cli.py`, after the `install` subparser block (line 77), add:

```python
    p_cmp = sub.add_parser("compare")
    p_cmp.add_argument("target")
    p_cmp.add_argument("--metric")
    dgrp = p_cmp.add_mutually_exclusive_group()
    dgrp.add_argument("--higher", dest="direction", action="store_const", const="higher")
    dgrp.add_argument("--lower", dest="direction", action="store_const", const="lower")

    p_abl = sub.add_parser("ablation")
    p_abl.add_argument("capsules", nargs="+")
    p_abl.add_argument("--metric")

    p_prov = sub.add_parser("provenance")
    p_prov.add_argument("run")

    p_md = sub.add_parser("metric-dir")
    md_sub = p_md.add_subparsers(dest="md_cmd", required=True)
    p_md_set = md_sub.add_parser("set")
    p_md_set.add_argument("metric")
    p_md_set.add_argument("direction", choices=["higher", "lower"])
    md_sub.add_parser("list")
    md_sub.add_parser("suggest")
```

- [ ] **Step 3c: Add the dispatch branches**

In `src/rgit/cli.py`, before the final `return 1` (line 197), add:

```python
    if args.cmd == "compare":
        from . import compare as cmpmod
        from .tables import render_table
        try:
            res = cmpmod.compare(store, args.target, args.metric, args.direction)
        except KeyError as e:
            print(str(e).strip('"'))
            return 1
        def _cell(v):
            return str(v) if v is not None else "—"
        rows = [[r["feature"], _cell(r["value"]), _cell(r["delta"])]
                for r in res["rows"]]
        mark = {(i, 1): True for i, r in enumerate(res["rows"]) if r["winner"]}
        print(render_table(["feature", res["metric"] or "metric", "Δ"], rows, mark))
        return 0

    if args.cmd == "ablation":
        from . import ablation as ablmod
        from .tables import render_table
        grid = ablmod.ablation(store, args.capsules, args.metric)
        cols = sorted({m for row in grid["rows"] for m in row["cells"]})
        headers = ["subset"] + cols
        rows, mark = [], {}
        for i, row in enumerate(grid["rows"]):
            label = "+".join(row["subset"]) or "base"
            rows.append([label] + [str(row["cells"].get(m, "—")) if row["cells"].get(m) is not None else "—"
                                   for m in cols])
            for c, m in enumerate(cols, start=1):
                if grid["winners"].get(m) == row["subset"]:
                    mark[(i, c)] = True
        print(render_table(headers, rows, mark))
        return 0

    if args.cmd == "provenance":
        from . import provenance as provmod
        try:
            res = provmod.provenance(store, args.run)
        except KeyError as e:
            print(str(e).strip('"'))
            return 1
        for sl in res["slices"]:
            print(f"[{sl['flag']}] {sl['feature']}  {sl['symbol']}")
            if sl["diff"]:
                print(sl["diff"])
        print(f"summary: {res['summary']}")
        return 0

    if args.cmd == "metric-dir":
        from .metricdir import suggest
        if args.md_cmd == "set":
            store.set_metric_direction(args.metric, args.direction)
            print(f"{args.metric} -> {args.direction}")
            return 0
        if args.md_cmd == "list":
            for m, d in store.list_metric_directions().items():
                print(f"{m}: {d}")
            return 0
        if args.md_cmd == "suggest":
            names = sorted({k for r in store.conn.execute("SELECT metrics FROM runs")
                            if r["metrics"] for k in json.loads(r["metrics"])})
            for m, d in suggest(names).items():
                print(f"{m}: {d}  (apply with: rgit metric-dir set {m} {d})")
            return 0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_cli.py -v`
Expected: PASS (existing CLI tests still green + 4 new)

- [ ] **Step 5: Commit**

```bash
git add src/rgit/cli.py tests/test_cli.py
git commit -m "feat(cli): compare / ablation / provenance / metric-dir + run --with"
```

---

### Task 10: MCP wiring — read-only compare / ablation / provenance tools

**Files:**
- Modify: `src/rgit/mcp_server.py` (add three tools + register them)
- Test: `tests/test_mcp_server.py` (extend)

**Context:** MCP stays query-only (principle 7). The tools call the same compute modules and return structured data (not rendered tables). They must not write — note that `compare(...)` writes a direction only when a `direction` arg is passed, so the MCP tool omits it.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_mcp_server.py  (add to the existing file)
from rgit import mcp_server
from rgit.store.store import Store
from rgit.store.models import Capsule, CodeSlice, Run


def _cap(store, name):
    return store.add_feature(Capsule(
        id="", name=name, intent="x", status="approved", base_commit="abc",
        knobs={}, data_assumptions=None, resurrection_guide=None,
        result_summary=None, payload_hash=None,
        code_slices=[CodeSlice("loss.py", "Loss", None, "c", "wrap")]))


def test_compare_tool_returns_rows(git_repo, monkeypatch):
    monkeypatch.chdir(git_repo)
    store = Store.init(git_repo)
    store.set_metric_direction("eval_loss", "lower")
    a = _cap(store, "temperature")
    rid = store.add_run(Run(id="", cmd="t", artifact_hash="h",
                            metrics={"eval_loss": 1.1}, base_commit="abc",
                            env=None, created_at="2026-01-01T00:00:00"))
    store.add_edge(a, rid, "produced")
    out = mcp_server.compare_tool("temperature")
    assert out["metric"] == "eval_loss"
    assert out["rows"][0]["feature"] == "temperature"


def test_compare_tool_does_not_write_direction(git_repo, monkeypatch):
    monkeypatch.chdir(git_repo)
    store = Store.init(git_repo)
    a = _cap(store, "temperature")
    rid = store.add_run(Run(id="", cmd="t", artifact_hash="h",
                            metrics={"eval_loss": 1.1}, base_commit="abc",
                            env=None, created_at="2026-01-01T00:00:00"))
    store.add_edge(a, rid, "produced")
    mcp_server.compare_tool("temperature")
    # query-only: the tool must not have set a direction
    assert Store.open(git_repo).get_metric_direction("eval_loss") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_mcp_server.py -v`
Expected: FAIL with `AttributeError: module 'rgit.mcp_server' has no attribute 'compare_tool'`

- [ ] **Step 3: Add the tools**

In `src/rgit/mcp_server.py`, add imports at the top (next to the existing ones):

```python
from .compare import compare as compare_fn
from .ablation import ablation as ablation_fn
from .provenance import provenance as provenance_fn
```

Add the three tool functions (after `list_features_tool`, before the registration block):

```python
def compare_tool(target: str, metric: str | None = None) -> dict:
    """Rank a feature's variant cluster by a run metric (read-only)."""
    return compare_fn(Store.open(), target, metric)        # no direction arg -> never writes


def ablation_tool(capsule_ids: list[str], metric: str | None = None) -> dict:
    """Base/+A/+A+B metric grid over a set of capsules (read-only)."""
    return ablation_fn(Store.open(), capsule_ids, metric)


def provenance_tool(run_id: str) -> dict:
    """Per-slice clean-vs-adapted audit for a run (read-only)."""
    return provenance_fn(Store.open(), run_id)
```

Register them with the others:

```python
mcp.tool()(compare_tool)
mcp.tool()(ablation_tool)
mcp.tool()(provenance_tool)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_mcp_server.py -v`
Expected: PASS (existing MCP tests still green + 2 new)

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: PASS (all v1 + v2 + v3 tests green)

- [ ] **Step 6: Commit**

```bash
git add src/rgit/mcp_server.py tests/test_mcp_server.py
git commit -m "feat(mcp): read-only compare / ablation / provenance tools"
```

---

## Final Verification

- [ ] Run the entire suite: `.venv/bin/pytest -q` — all green.
- [ ] Manual smoke (in a throwaway repo with `rgit init`): `rgit metric-dir set eval_loss lower`, capture/approve two variant capsules, `rgit compare <name>`, `rgit ablation A B`, `rgit provenance <run>` — each prints a table/diff.
- [ ] Confirm no module imports a paid API client; `compare`/`ablation`/`provenance`/`metricdir` are import-light (stdlib + libcst + store only).
- [ ] Update `README.md` v3 section to list the four new subcommands (separate doc commit).

## Self-Review notes (spec coverage)

- §2.1 compare → Task 4 + CLI Task 9 + MCP Task 10.
- §2.2 ablation → Task 6 + CLI Task 9 + MCP Task 10.
- §2.3 provenance → Task 7 + CLI Task 9 + MCP Task 10.
- §2.4 conflict-merge → Task 8 (compose + regenerator prompt).
- §2.5 metric-direction config → Task 2 (storage) + Task 3 (resolve/suggest) + CLI Task 9.
- §3 data model: `active` edge → Task 5; `metric_directions` table → Task 2.
- §4 CLI surface → Task 9 (+ `run --with` in Task 5/9).
- §5 MCP read-only tools → Task 10.
- §8 error handling: unknown target/run → KeyError surfaced as non-zero CLI exit (Tasks 4, 7, 9); missing slice → `missing` flag (Task 7); unknown direction → no winner (Tasks 3, 4, 6).
- §9 testing → one test file per module + CLI/MCP integration extensions.
