# research-git v1 — The Memory Loop — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the thinnest end-to-end vertical of research-git — capture a messy diff into a Feature Capsule graph, recall it, regenerate it onto current code, and freeze a reproducible run artifact.

**Architecture:** A local Python engine owns a SQLite graph + content-addressed object store under `.rgit/` (beside `.git/`). A CLI (`rgit`) and git hooks drive capture/run triggers. A FastMCP server exposes `recall`/`compose`/`get` so the host agent (Claude Code) does retrieval + regeneration. Autonomous LLM use is confined to one module (the segmenter); everything else is deterministic and LLM-free.

**Tech Stack:** Python 3.11+, SQLite (stdlib `sqlite3`), `libcst` (AST symbol mapping), `mcp` / FastMCP (agent facade), `pytest`. Git is shelled out via `subprocess`.

---

> **Post-implementation update (no paid API).** During execution the user required that no
> pay-per-use Claude API be used. Task 9 therefore ships a **free, no-LLM `HeuristicSegmenter`**
> as the default (one rough candidate per changed file) instead of an `AnthropicSegmenter`; the
> `anthropic` dependency was dropped. High-quality segmentation is delegated to the host agent
> via two added MCP tools, `pending_captures` and `resegment` (Task 16), backed by a new
> `Store.set_proposal_candidates`. Task 15's CLI default segmenter is `HeuristicSegmenter`. The
> `Segmenter` protocol made this a localized change. Test commands use `python3` (no `python`
> on the target machine).

---

## File Structure

```
research-git/
  pyproject.toml
  src/rgit/
    __init__.py
    store/
      __init__.py
      ids.py          # new_id() helper
      objects.py      # content-addressed blob store
      db.py           # sqlite connection + schema
      models.py       # Capsule, CodeSlice, ResultSummary, Run, Edge, Proposal
      store.py        # Store facade: graph + object CRUD
    gitutil.py        # current_commit, diff_since, freeze/materialize worktree
    metrics.py        # parse rgit_metrics.json / RGIT_METRIC lines
    astmap.py         # libcst: map diff hunks -> enclosing symbols, read current symbol src
    segmenter.py      # Segmenter protocol, MockSegmenter, AnthropicSegmenter, segment_diff()
    curation.py       # list/approve/dismiss proposals -> capsules
    recall.py         # keyword+structural search + depends_on subgraph
    compose.py        # build regeneration brief
    runner.py         # rgit run: execute, freeze, run node, segment
    hooks.py          # git hook install
    cli.py            # argparse CLI: init/run/capture/review/features
    mcp_server.py     # FastMCP server
  tests/
    conftest.py       # git_repo fixture
    test_objects.py test_store.py test_gitutil.py test_metrics.py
    test_astmap.py test_segmenter.py test_curation.py test_recall.py
    test_compose.py test_runner.py test_cli.py test_e2e.py
```

Each module has one responsibility and no module imports `cli`/`mcp_server` (those are entry points only). The only LLM-touching module is `segmenter.py`, isolated behind the `Segmenter` protocol so all other tests use `MockSegmenter`.

---

## Task 1: Project scaffold

**Files:**
- Create: `pyproject.toml`
- Create: `src/rgit/__init__.py`
- Create: `src/rgit/store/__init__.py`
- Create: `tests/conftest.py`

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[project]
name = "research-git"
version = "0.1.0"
description = "A memory system that captures research features as semantic capsules"
requires-python = ">=3.11"
dependencies = [
    "libcst>=1.1",
    "anthropic>=0.40",
    "mcp>=1.2",
]

[project.optional-dependencies]
dev = ["pytest>=8.0"]

[project.scripts]
rgit = "rgit.cli:main"

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]
```

- [ ] **Step 2: Create empty package files**

`src/rgit/__init__.py`:
```python
__version__ = "0.1.0"
```
`src/rgit/store/__init__.py`:
```python
```

- [ ] **Step 3: Write the `git_repo` fixture**

`tests/conftest.py`:
```python
import subprocess
from pathlib import Path
import pytest


def _run(args, cwd):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """An initialized git repo with one commit, returned as its root path."""
    _run(["git", "init", "-q"], tmp_path)
    _run(["git", "config", "user.email", "t@t.t"], tmp_path)
    _run(["git", "config", "user.name", "t"], tmp_path)
    (tmp_path / "model.py").write_text("def forward(x):\n    return x\n")
    _run(["git", "add", "."], tmp_path)
    _run(["git", "commit", "-q", "-m", "init"], tmp_path)
    return tmp_path
```

- [ ] **Step 4: Install and verify the toolchain**

Run: `pip install -e ".[dev]" && pytest -q`
Expected: `no tests ran` (exit 0) — collection works, env is sane.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml src/rgit/__init__.py src/rgit/store/__init__.py tests/conftest.py
git commit -m "chore: scaffold research-git package and pytest"
```

---

## Task 2: Content-addressed object store

**Files:**
- Create: `src/rgit/store/objects.py`
- Test: `tests/test_objects.py`

- [ ] **Step 1: Write the failing test**

`tests/test_objects.py`:
```python
from rgit.store.objects import ObjectStore


def test_put_is_content_addressed_and_roundtrips(tmp_path):
    store = ObjectStore(tmp_path / "objects")
    h1 = store.put(b"hello")
    h2 = store.put(b"hello")
    assert h1 == h2                       # same content -> same hash
    assert len(h1) == 64                  # sha256 hex
    assert store.get(h1) == b"hello"


def test_put_json_roundtrips(tmp_path):
    store = ObjectStore(tmp_path / "objects")
    h = store.put_json({"b": 2, "a": 1})
    assert store.get_json(h) == {"b": 2, "a": 1}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_objects.py -v`
Expected: FAIL — `ModuleNotFoundError: rgit.store.objects`

- [ ] **Step 3: Implement**

`src/rgit/store/objects.py`:
```python
import hashlib
import json
from pathlib import Path
from typing import Any


class ObjectStore:
    """Immutable sha256-addressed blob store under a directory."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, digest: str) -> Path:
        return self.root / digest[:2] / digest[2:]

    def put(self, data: bytes) -> str:
        digest = hashlib.sha256(data).hexdigest()
        p = self._path(digest)
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
        return digest

    def get(self, digest: str) -> bytes:
        return self._path(digest).read_bytes()

    def put_json(self, obj: Any) -> str:
        return self.put(json.dumps(obj, sort_keys=True).encode())

    def get_json(self, digest: str) -> Any:
        return json.loads(self.get(digest))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_objects.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/rgit/store/objects.py tests/test_objects.py
git commit -m "feat: content-addressed object store"
```

---

## Task 3: Models and id helper

**Files:**
- Create: `src/rgit/store/ids.py`
- Create: `src/rgit/store/models.py`
- Test: `tests/test_models.py`

- [ ] **Step 1: Write the failing test**

`tests/test_models.py`:
```python
from rgit.store.ids import new_id
from rgit.store.models import Capsule, CodeSlice, ResultSummary, Run


def test_new_id_is_prefixed_and_unique():
    a = new_id("feat_")
    b = new_id("feat_")
    assert a.startswith("feat_") and a != b


def test_capsule_roundtrips_through_dict():
    cap = Capsule(
        id="feat_1", name="contrastive-loss", intent="add aux contrastive loss",
        status="approved", base_commit="abc", knobs={"lambda": 0.1},
        data_assumptions="expects normalized embeddings",
        resurrection_guide="wrap loss in compute_loss; add projection head",
        result_summary=ResultSummary(verdict="improved", key_delta="+1.8 acc",
                                     failure_reason=None, notes=None),
        payload_hash="deadbeef",
        code_slices=[CodeSlice(file="model.py", symbol="compute_loss",
                               anchor="L10-L14", code="loss += ...", kind="insert")],
    )
    assert Capsule.from_dict(cap.to_dict()) == cap


def test_run_roundtrips():
    r = Run(id="run_1", cmd="python train.py", artifact_hash="aa",
            metrics={"acc": 0.9}, base_commit="abc", env={"py": "3.11"},
            created_at="2026-06-16T00:00:00")
    assert Run.from_dict(r.to_dict()) == r
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: rgit.store.ids`

- [ ] **Step 3: Implement**

`src/rgit/store/ids.py`:
```python
import uuid


def new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:12]}"
```

`src/rgit/store/models.py`:
```python
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Any, Optional


@dataclass
class CodeSlice:
    file: str
    symbol: Optional[str]
    anchor: Optional[str]
    code: str
    kind: str  # "add" | "wrap" | "insert"


@dataclass
class ResultSummary:
    verdict: Optional[str] = None       # "improved" | "neutral" | "regressed"
    key_delta: Optional[str] = None
    failure_reason: Optional[str] = None
    notes: Optional[str] = None


@dataclass
class Capsule:
    id: str
    name: str
    intent: str
    status: str                         # "proposed" | "approved"
    base_commit: str
    knobs: dict = field(default_factory=dict)
    data_assumptions: Optional[str] = None
    resurrection_guide: Optional[str] = None
    result_summary: Optional[ResultSummary] = None
    payload_hash: Optional[str] = None
    code_slices: list[CodeSlice] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Capsule":
        d = dict(d)
        rs = d.get("result_summary")
        d["result_summary"] = ResultSummary(**rs) if rs else None
        d["code_slices"] = [CodeSlice(**c) for c in d.get("code_slices", [])]
        return cls(**d)


@dataclass
class Run:
    id: str
    cmd: str
    artifact_hash: str
    metrics: Optional[dict]
    base_commit: str
    env: Optional[dict]
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Run":
        return cls(**d)


@dataclass
class Edge:
    src: str
    dst: str
    type: str  # depends_on|variant_of|derived_from|supersedes|produced|touches|conflicts_with


@dataclass
class Proposal:
    id: str
    trigger: str                        # "run" | "commit" | "manual"
    diff_ref: str                       # object hash of the captured diff
    candidates: list[dict]
    status: str = "open"                # "open" | "resolved" | "dismissed"
    run_id: Optional[str] = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_models.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/rgit/store/ids.py src/rgit/store/models.py tests/test_models.py
git commit -m "feat: capsule/run/edge/proposal data models"
```

---

## Task 4: SQLite schema

**Files:**
- Create: `src/rgit/store/db.py`
- Test: `tests/test_db.py`

- [ ] **Step 1: Write the failing test**

`tests/test_db.py`:
```python
from rgit.store.db import connect, init_schema


def test_schema_creates_all_tables(tmp_path):
    conn = connect(tmp_path / "graph.db")
    init_schema(conn)
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"features", "runs", "edges", "proposals"} <= names


def test_init_schema_is_idempotent(tmp_path):
    conn = connect(tmp_path / "graph.db")
    init_schema(conn)
    init_schema(conn)  # must not raise
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_db.py -v`
Expected: FAIL — `ModuleNotFoundError: rgit.store.db`

- [ ] **Step 3: Implement**

`src/rgit/store/db.py`:
```python
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS features (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    intent TEXT NOT NULL,
    status TEXT NOT NULL,
    base_commit TEXT NOT NULL,
    knobs TEXT NOT NULL DEFAULT '{}',
    data_assumptions TEXT,
    resurrection_guide TEXT,
    result_summary TEXT,
    payload_hash TEXT
);
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    cmd TEXT NOT NULL,
    artifact_hash TEXT NOT NULL,
    metrics TEXT,
    base_commit TEXT NOT NULL,
    env TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS edges (
    src TEXT NOT NULL,
    dst TEXT NOT NULL,
    type TEXT NOT NULL,
    UNIQUE(src, dst, type)
);
CREATE TABLE IF NOT EXISTS proposals (
    id TEXT PRIMARY KEY,
    trigger TEXT NOT NULL,
    diff_ref TEXT NOT NULL,
    candidates TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    run_id TEXT
);
"""


def connect(path: Path) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_db.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/rgit/store/db.py tests/test_db.py
git commit -m "feat: sqlite graph schema"
```

---

## Task 5: Store facade (graph + object CRUD)

**Files:**
- Create: `src/rgit/store/store.py`
- Test: `tests/test_store.py`

- [ ] **Step 1: Write the failing test**

`tests/test_store.py`:
```python
import json
from rgit.store.store import Store
from rgit.store.models import Capsule, CodeSlice, Run


def make_cap(name="contrastive-loss", intent="add aux loss", base="abc"):
    return Capsule(
        id="", name=name, intent=intent, status="approved", base_commit=base,
        knobs={"lambda": 0.1}, data_assumptions="normalized embeddings",
        resurrection_guide="wrap compute_loss", result_summary=None,
        payload_hash=None,
        code_slices=[CodeSlice("model.py", "compute_loss", "L1", "loss+=x", "insert")],
    )


def test_add_and_get_feature_persists_payload(git_repo):
    store = Store.init(git_repo)
    cap = make_cap()
    fid = store.add_feature(cap)
    got = store.get_feature(fid)
    assert got.name == "contrastive-loss"
    assert got.code_slices[0].symbol == "compute_loss"   # came back from object store
    assert got.payload_hash is not None


def test_find_features_matches_intent_and_name(git_repo):
    store = Store.init(git_repo)
    store.add_feature(make_cap(name="contrastive-loss", intent="aux loss"))
    store.add_feature(make_cap(name="dropout-tweak", intent="raise dropout"))
    hits = {c.name for c in store.find_features("loss")}
    assert hits == {"contrastive-loss"}


def test_edges_and_neighbors(git_repo):
    store = Store.init(git_repo)
    a = store.add_feature(make_cap(name="a"))
    b = store.add_feature(make_cap(name="b"))
    store.add_edge(a, b, "depends_on")
    assert store.neighbors(a, "depends_on") == [b]


def test_add_and_get_run(git_repo):
    store = Store.init(git_repo)
    rid = store.add_run(Run("", "python train.py", "aa", {"acc": 0.9},
                            "abc", None, "2026-06-16T00:00:00"))
    assert store.get_run(rid).metrics == {"acc": 0.9}


def test_open_finds_rgit_upward(git_repo):
    Store.init(git_repo)
    sub = git_repo / "deep" / "nested"
    sub.mkdir(parents=True)
    store = Store.open(sub)
    assert store.root == git_repo
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_store.py -v`
Expected: FAIL — `ModuleNotFoundError: rgit.store.store`

- [ ] **Step 3: Implement**

`src/rgit/store/store.py`:
```python
from __future__ import annotations
import json
from pathlib import Path
from typing import Optional

from .db import connect, init_schema
from .ids import new_id
from .models import Capsule, Run, Proposal
from .objects import ObjectStore


class Store:
    """Facade over the graph DB and object store under <root>/.rgit/."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.dir = self.root / ".rgit"
        self.objects = ObjectStore(self.dir / "objects")
        self.conn = connect(self.dir / "graph.db")

    @classmethod
    def init(cls, root: Path) -> "Store":
        s = cls(root)
        init_schema(s.conn)
        return s

    @classmethod
    def open(cls, start: Optional[Path] = None) -> "Store":
        cur = Path(start or Path.cwd()).resolve()
        for cand in [cur, *cur.parents]:
            if (cand / ".rgit").is_dir():
                return cls(cand)
        raise FileNotFoundError("no .rgit/ found (run `rgit init`)")

    # ---- features -----------------------------------------------------
    def add_feature(self, cap: Capsule) -> str:
        fid = cap.id or new_id("feat_")
        payload = [c.__dict__ for c in cap.code_slices]
        payload_hash = self.objects.put_json(payload)
        rs = json.dumps(cap.result_summary.__dict__) if cap.result_summary else None
        self.conn.execute(
            "INSERT INTO features VALUES (?,?,?,?,?,?,?,?,?,?)",
            (fid, cap.name, cap.intent, cap.status, cap.base_commit,
             json.dumps(cap.knobs), cap.data_assumptions, cap.resurrection_guide,
             rs, payload_hash))
        self.conn.commit()
        return fid

    def _row_to_capsule(self, row) -> Capsule:
        from .models import CodeSlice, ResultSummary
        slices = [CodeSlice(**c) for c in self.objects.get_json(row["payload_hash"])]
        rs = json.loads(row["result_summary"]) if row["result_summary"] else None
        return Capsule(
            id=row["id"], name=row["name"], intent=row["intent"],
            status=row["status"], base_commit=row["base_commit"],
            knobs=json.loads(row["knobs"]), data_assumptions=row["data_assumptions"],
            resurrection_guide=row["resurrection_guide"],
            result_summary=ResultSummary(**rs) if rs else None,
            payload_hash=row["payload_hash"], code_slices=slices)

    def get_feature(self, fid: str) -> Capsule:
        row = self.conn.execute("SELECT * FROM features WHERE id=?", (fid,)).fetchone()
        if row is None:
            raise KeyError(fid)
        return self._row_to_capsule(row)

    def list_features(self) -> list[Capsule]:
        rows = self.conn.execute("SELECT * FROM features").fetchall()
        return [self._row_to_capsule(r) for r in rows]

    def find_features(self, query: str) -> list[Capsule]:
        like = f"%{query}%"
        rows = self.conn.execute(
            "SELECT * FROM features WHERE status='approved' AND "
            "(name LIKE ? OR intent LIKE ? OR data_assumptions LIKE ?)",
            (like, like, like)).fetchall()
        return [self._row_to_capsule(r) for r in rows]

    # ---- edges --------------------------------------------------------
    def add_edge(self, src: str, dst: str, type: str) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO edges VALUES (?,?,?)", (src, dst, type))
        self.conn.commit()

    def neighbors(self, src: str, type: str) -> list[str]:
        return [r["dst"] for r in self.conn.execute(
            "SELECT dst FROM edges WHERE src=? AND type=?", (src, type))]

    # ---- runs ---------------------------------------------------------
    def add_run(self, run: Run) -> str:
        rid = run.id or new_id("run_")
        self.conn.execute(
            "INSERT INTO runs VALUES (?,?,?,?,?,?,?)",
            (rid, run.cmd, run.artifact_hash,
             json.dumps(run.metrics) if run.metrics is not None else None,
             run.base_commit, json.dumps(run.env) if run.env else None,
             run.created_at))
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
                   created_at=row["created_at"])

    # ---- proposals ----------------------------------------------------
    def add_proposal(self, p: Proposal) -> str:
        pid = p.id or new_id("prop_")
        self.conn.execute(
            "INSERT INTO proposals VALUES (?,?,?,?,?,?)",
            (pid, p.trigger, p.diff_ref, json.dumps(p.candidates), p.status, p.run_id))
        self.conn.commit()
        return pid

    def get_proposal(self, pid: str) -> Proposal:
        row = self.conn.execute("SELECT * FROM proposals WHERE id=?", (pid,)).fetchone()
        if row is None:
            raise KeyError(pid)
        return Proposal(id=row["id"], trigger=row["trigger"], diff_ref=row["diff_ref"],
                        candidates=json.loads(row["candidates"]), status=row["status"],
                        run_id=row["run_id"])

    def list_proposals(self, status: Optional[str] = None) -> list[Proposal]:
        if status:
            rows = self.conn.execute(
                "SELECT id FROM proposals WHERE status=?", (status,)).fetchall()
        else:
            rows = self.conn.execute("SELECT id FROM proposals").fetchall()
        return [self.get_proposal(r["id"]) for r in rows]

    def set_proposal_status(self, pid: str, status: str) -> None:
        self.conn.execute("UPDATE proposals SET status=? WHERE id=?", (status, pid))
        self.conn.commit()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_store.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add src/rgit/store/store.py tests/test_store.py
git commit -m "feat: store facade over graph + object store"
```

---

## Task 6: Git utilities (commit, diff, freeze, materialize)

**Files:**
- Create: `src/rgit/gitutil.py`
- Test: `tests/test_gitutil.py`

- [ ] **Step 1: Write the failing test**

`tests/test_gitutil.py`:
```python
from rgit.gitutil import current_commit, diff_since, freeze_worktree, materialize
from rgit.store.objects import ObjectStore


def test_current_commit_returns_sha(git_repo):
    sha = current_commit(git_repo)
    assert len(sha) == 40


def test_diff_since_head_shows_working_changes(git_repo):
    (git_repo / "model.py").write_text("def forward(x):\n    return x * 2\n")
    diff = diff_since(git_repo, "HEAD")
    assert "x * 2" in diff and "model.py" in diff


def test_freeze_is_deterministic_and_materializes(git_repo, tmp_path):
    objs = ObjectStore(tmp_path / "objects")
    (git_repo / "model.py").write_text("CHANGED\n")
    h1 = freeze_worktree(git_repo, objs)
    h2 = freeze_worktree(git_repo, objs)
    assert h1 == h2                                  # byte-identical snapshot
    dest = tmp_path / "restored"
    materialize(objs, h1, dest)
    assert (dest / "model.py").read_text() == "CHANGED\n"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_gitutil.py -v`
Expected: FAIL — `ModuleNotFoundError: rgit.gitutil`

- [ ] **Step 3: Implement**

`src/rgit/gitutil.py`:
```python
from __future__ import annotations
import io
import subprocess
import tarfile
from pathlib import Path

from .store.objects import ObjectStore


def _git(repo: Path, *args: str) -> str:
    out = subprocess.run(["git", *args], cwd=repo, check=True,
                         capture_output=True, text=True)
    return out.stdout


def current_commit(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD").strip()


def diff_since(repo: Path, base: str = "HEAD") -> str:
    """Unified diff of the working tree (tracked changes) vs `base`."""
    return _git(repo, "diff", base, "--")


def _snapshot_paths(repo: Path) -> list[str]:
    """Tracked + untracked files, excluding ignored, .git and .rgit."""
    out = _git(repo, "ls-files", "-co", "--exclude-standard")
    paths = [p for p in out.splitlines() if p and not p.startswith(".rgit/")]
    return sorted(paths)


def freeze_worktree(repo: Path, objects: ObjectStore) -> str:
    """Deterministic tar of the working tree -> content-addressed hash."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for rel in _snapshot_paths(repo):
            data = (repo / rel).read_bytes()
            info = tarfile.TarInfo(name=rel)
            info.size = len(data)
            info.mtime = 0          # normalize for byte-identical snapshots
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            tar.addfile(info, io.BytesIO(data))
    return objects.put(buf.getvalue())


def materialize(objects: ObjectStore, artifact_hash: str, dest: Path) -> None:
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(objects.get(artifact_hash))) as tar:
        tar.extractall(dest)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_gitutil.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/rgit/gitutil.py tests/test_gitutil.py
git commit -m "feat: git diff + deterministic worktree freeze/materialize"
```

---

## Task 7: Metrics parsing

**Files:**
- Create: `src/rgit/metrics.py`
- Test: `tests/test_metrics.py`

- [ ] **Step 1: Write the failing test**

`tests/test_metrics.py`:
```python
from rgit.metrics import parse_metrics


def test_parses_json_file_when_present(tmp_path):
    (tmp_path / "rgit_metrics.json").write_text('{"acc": 0.91, "loss": 0.3}')
    assert parse_metrics("noise", tmp_path) == {"acc": 0.91, "loss": 0.3}


def test_parses_stdout_metric_lines(tmp_path):
    stdout = "epoch 1\nRGIT_METRIC acc=0.88\nRGIT_METRIC loss=0.42\ndone\n"
    assert parse_metrics(stdout, tmp_path) == {"acc": 0.88, "loss": 0.42}


def test_returns_none_when_no_metrics(tmp_path):
    assert parse_metrics("nothing here", tmp_path) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_metrics.py -v`
Expected: FAIL — `ModuleNotFoundError: rgit.metrics`

- [ ] **Step 3: Implement**

`src/rgit/metrics.py`:
```python
from __future__ import annotations
import json
import re
from pathlib import Path
from typing import Optional

_LINE = re.compile(r"RGIT_METRIC\s+(\w+)=([-\d.eE]+)")


def parse_metrics(stdout: str, run_dir: Path) -> Optional[dict]:
    """JSON file wins; otherwise scrape RGIT_METRIC lines from stdout."""
    f = Path(run_dir) / "rgit_metrics.json"
    if f.exists():
        return json.loads(f.read_text())
    found = {}
    for key, val in _LINE.findall(stdout):
        found[key] = float(val)
    return found or None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_metrics.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/rgit/metrics.py tests/test_metrics.py
git commit -m "feat: metrics parsing (json file or stdout lines)"
```

---

## Task 8: AST symbol mapping

**Files:**
- Create: `src/rgit/astmap.py`
- Test: `tests/test_astmap.py`

- [ ] **Step 1: Write the failing test**

`tests/test_astmap.py`:
```python
from rgit.astmap import changed_symbols, read_symbol_source


def test_changed_symbols_finds_enclosing_function(git_repo):
    src = "def a():\n    return 1\n\ndef b():\n    return 2\n"
    (git_repo / "model.py").write_text(src)
    diff = (
        "diff --git a/model.py b/model.py\n"
        "--- a/model.py\n+++ b/model.py\n"
        "@@ -4,2 +4,2 @@ def b():\n-    return 2\n+    return 3\n"
    )
    syms = changed_symbols(diff, git_repo)
    assert {"file": "model.py", "symbol": "b"} in syms


def test_read_symbol_source_extracts_function(git_repo):
    (git_repo / "model.py").write_text("def a():\n    return 1\n\ndef b():\n    return 2\n")
    code = read_symbol_source(git_repo, "model.py", "b")
    assert code.strip().startswith("def b():")
    assert "return 2" in code
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_astmap.py -v`
Expected: FAIL — `ModuleNotFoundError: rgit.astmap`

- [ ] **Step 3: Implement**

`src/rgit/astmap.py`:
```python
from __future__ import annotations
import re
from pathlib import Path
from typing import Optional

import libcst as cst
from libcst.metadata import MetadataWrapper, PositionProvider

_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", re.M)
_FILE = re.compile(r"^\+\+\+ b/(.+)$", re.M)


def _changed_line_ranges(diff: str) -> dict[str, list[tuple[int, int]]]:
    """file -> list of (start, end) line ranges touched on the new side."""
    result: dict[str, list[tuple[int, int]]] = {}
    current: Optional[str] = None
    for line in diff.splitlines():
        m = _FILE.match(line)
        if m:
            current = m.group(1)
            result.setdefault(current, [])
            continue
        h = _HUNK.match(line)
        if h and current:
            start = int(h.group(1))
            length = int(h.group(2) or "1")
            result[current].append((start, start + max(length, 1) - 1))
    return result


class _SymbolFinder(cst.CSTVisitor):
    METADATA_DEPENDENCIES = (PositionProvider,)

    def __init__(self, ranges: list[tuple[int, int]]):
        self.ranges = ranges
        self.found: set[str] = set()

    def _overlaps(self, node) -> bool:
        pos = self.get_metadata(PositionProvider, node)
        for s, e in self.ranges:
            if pos.start.line <= e and pos.end.line >= s:
                return True
        return False

    def visit_FunctionDef(self, node: cst.FunctionDef) -> None:
        if self._overlaps(node):
            self.found.add(node.name.value)

    def visit_ClassDef(self, node: cst.ClassDef) -> None:
        if self._overlaps(node):
            self.found.add(node.name.value)


def changed_symbols(diff: str, repo: Path) -> list[dict]:
    """[{file, symbol}] for each top-level def/class overlapping a diff hunk."""
    out: list[dict] = []
    for file, ranges in _changed_line_ranges(diff).items():
        path = repo / file
        if not path.suffix == ".py" or not path.exists() or not ranges:
            continue
        wrapper = MetadataWrapper(cst.parse_module(path.read_text()))
        finder = _SymbolFinder(ranges)
        wrapper.visit(finder)
        for sym in sorted(finder.found):
            out.append({"file": file, "symbol": sym})
    return out


def read_symbol_source(repo: Path, file: str, symbol: str) -> Optional[str]:
    """Current source text of a top-level def/class, or None if absent."""
    path = repo / file
    if not path.exists():
        return None
    module = cst.parse_module(path.read_text())
    for stmt in module.body:
        if isinstance(stmt, (cst.FunctionDef, cst.ClassDef)) and stmt.name.value == symbol:
            return module.code_for_node(stmt)
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_astmap.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/rgit/astmap.py tests/test_astmap.py
git commit -m "feat: libcst diff-hunk to symbol mapping"
```

---

## Task 9: Segmenter (protocol, mock, real, segment_diff)

**Files:**
- Create: `src/rgit/segmenter.py`
- Test: `tests/test_segmenter.py`

- [ ] **Step 1: Write the failing test**

`tests/test_segmenter.py`:
```python
from rgit.segmenter import MockSegmenter, segment_diff
from rgit.store.store import Store


def test_segment_diff_creates_open_proposal(git_repo):
    store = Store.init(git_repo)
    (git_repo / "model.py").write_text("def forward(x):\n    return x * 2\n")
    candidate = {
        "name": "double-forward", "intent": "scale forward output by 2",
        "code_slices": [{"file": "model.py", "symbol": "forward",
                         "anchor": "L1-L2", "code": "return x * 2", "kind": "wrap"}],
        "knobs": {}, "data_assumptions": None,
        "resurrection_guide": "multiply forward() output by 2", "confidence": 0.9,
    }
    seg = MockSegmenter([candidate])
    pid = segment_diff(store, trigger="manual", segmenter=seg, run_id=None)
    prop = store.get_proposal(pid)
    assert prop.status == "open"
    assert prop.candidates[0]["name"] == "double-forward"
    assert prop.diff_ref  # diff was stored as an object


def test_mock_segmenter_sees_symbol_map(git_repo):
    store = Store.init(git_repo)
    (git_repo / "model.py").write_text("def forward(x):\n    return x * 2\n")
    seg = MockSegmenter([])
    segment_diff(store, trigger="manual", segmenter=seg, run_id=None)
    assert seg.last_symbols == [{"file": "model.py", "symbol": "forward"}]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_segmenter.py -v`
Expected: FAIL — `ModuleNotFoundError: rgit.segmenter`

- [ ] **Step 3: Implement**

`src/rgit/segmenter.py`:
```python
from __future__ import annotations
import json
import os
from typing import Optional, Protocol

from .astmap import changed_symbols
from .gitutil import diff_since
from .store.models import Proposal
from .store.store import Store


class Segmenter(Protocol):
    def segment(self, diff: str, symbols: list[dict]) -> list[dict]:
        """Return a list of candidate capsule dicts:
        {name, intent, code_slices[{file,symbol,anchor,code,kind}],
         knobs, data_assumptions, resurrection_guide, confidence}."""
        ...


class MockSegmenter:
    """Deterministic segmenter for tests."""

    def __init__(self, candidates: list[dict]):
        self.candidates = candidates
        self.last_symbols: list[dict] = []

    def segment(self, diff: str, symbols: list[dict]) -> list[dict]:
        self.last_symbols = symbols
        return self.candidates


SYSTEM_PROMPT = (
    "You are a research-engineering assistant that segments a git diff into "
    "coherent experimental FEATURES. For each feature return a JSON object with "
    "keys: name (kebab-case), intent (why this experiment exists), code_slices "
    "(list of {file, symbol, anchor, code, kind in [add,wrap,insert]}), knobs "
    "(hyperparameters/flags as an object), data_assumptions (string or null), "
    "resurrection_guide (how to re-implement this on a changed codebase), "
    "confidence (0..1). Separate unrelated infrastructure edits from real "
    "features; do NOT emit a feature for pure refactors. Respond with a JSON "
    'object: {"features": [ ... ]}.'
)


class AnthropicSegmenter:
    """Real segmenter backed by the Claude API."""

    def __init__(self, model: str = "claude-opus-4-8", client=None):
        self.model = model
        if client is None:
            import anthropic
            client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        self.client = client

    def segment(self, diff: str, symbols: list[dict]) -> list[dict]:
        msg = self.client.messages.create(
            model=self.model, max_tokens=4096, system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content":
                       f"Changed symbols:\n{json.dumps(symbols)}\n\nDiff:\n{diff}"}])
        text = "".join(b.text for b in msg.content if b.type == "text")
        start, end = text.find("{"), text.rfind("}")
        return json.loads(text[start:end + 1]).get("features", [])


def segment_diff(store: Store, trigger: str, segmenter: Segmenter,
                 run_id: Optional[str]) -> str:
    """Diff the working tree vs HEAD, segment it, store an open Proposal."""
    diff = diff_since(store.root, "HEAD")
    symbols = changed_symbols(diff, store.root)
    candidates = segmenter.segment(diff, symbols)
    diff_ref = store.objects.put(diff.encode())
    return store.add_proposal(Proposal(
        id="", trigger=trigger, diff_ref=diff_ref,
        candidates=candidates, status="open", run_id=run_id))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_segmenter.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/rgit/segmenter.py tests/test_segmenter.py
git commit -m "feat: segmenter protocol, mock + anthropic impls, segment_diff"
```

---

## Task 10: Curation (approve / dismiss proposals)

**Files:**
- Create: `src/rgit/curation.py`
- Test: `tests/test_curation.py`

- [ ] **Step 1: Write the failing test**

`tests/test_curation.py`:
```python
from rgit.curation import approve, dismiss
from rgit.segmenter import MockSegmenter, segment_diff
from rgit.store.store import Store
from rgit.store.models import Run


def _seed_proposal(store, run_id=None):
    candidate = {
        "name": "double-forward", "intent": "scale forward output by 2",
        "code_slices": [{"file": "model.py", "symbol": "forward",
                         "anchor": "L1", "code": "return x*2", "kind": "wrap"}],
        "knobs": {"factor": 2}, "data_assumptions": None,
        "resurrection_guide": "multiply forward output", "confidence": 0.9,
    }
    return segment_diff(store, "manual", MockSegmenter([candidate]), run_id)


def test_approve_creates_capsule_and_resolves_proposal(git_repo):
    store = Store.init(git_repo)
    (git_repo / "model.py").write_text("def forward(x):\n    return x*2\n")
    pid = _seed_proposal(store)
    fid = approve(store, pid, candidate_index=0, name="double-forward")
    cap = store.get_feature(fid)
    assert cap.status == "approved"
    assert cap.knobs == {"factor": 2}
    assert store.get_proposal(pid).status == "resolved"


def test_approve_links_feature_to_run(git_repo):
    store = Store.init(git_repo)
    (git_repo / "model.py").write_text("def forward(x):\n    return x*2\n")
    rid = store.add_run(Run("", "python train.py", "aa", {"acc": 0.9},
                            "abc", None, "2026-06-16T00:00:00"))
    pid = _seed_proposal(store, run_id=rid)
    fid = approve(store, pid, 0)
    assert store.neighbors(fid, "produced") == [rid]


def test_dismiss_marks_proposal(git_repo):
    store = Store.init(git_repo)
    (git_repo / "model.py").write_text("def forward(x):\n    return x*2\n")
    pid = _seed_proposal(store)
    dismiss(store, pid)
    assert store.get_proposal(pid).status == "dismissed"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_curation.py -v`
Expected: FAIL — `ModuleNotFoundError: rgit.curation`

- [ ] **Step 3: Implement**

`src/rgit/curation.py`:
```python
from __future__ import annotations
from typing import Optional

from .gitutil import current_commit
from .store.models import Capsule, CodeSlice
from .store.store import Store


def approve(store: Store, proposal_id: str, candidate_index: int = 0,
            name: Optional[str] = None) -> str:
    """Turn one candidate into an approved Capsule; link it to the run."""
    prop = store.get_proposal(proposal_id)
    cand = prop.candidates[candidate_index]
    cap = Capsule(
        id="", name=name or cand["name"], intent=cand["intent"],
        status="approved", base_commit=current_commit(store.root),
        knobs=cand.get("knobs", {}), data_assumptions=cand.get("data_assumptions"),
        resurrection_guide=cand.get("resurrection_guide"), result_summary=None,
        payload_hash=None,
        code_slices=[CodeSlice(**c) for c in cand["code_slices"]])
    fid = store.add_feature(cap)
    for slice_ in cap.code_slices:                       # touches edges
        store.add_edge(fid, f"module:{slice_.file}", "touches")
    if prop.run_id:                                      # produced edge
        store.add_edge(fid, prop.run_id, "produced")
    store.set_proposal_status(proposal_id, "resolved")
    return fid


def dismiss(store: Store, proposal_id: str) -> None:
    store.set_proposal_status(proposal_id, "dismissed")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_curation.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/rgit/curation.py tests/test_curation.py
git commit -m "feat: curation — approve/dismiss proposals into capsules"
```

---

## Task 11: Recall (keyword + structural search with subgraph)

**Files:**
- Create: `src/rgit/recall.py`
- Test: `tests/test_recall.py`

- [ ] **Step 1: Write the failing test**

`tests/test_recall.py`:
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


def test_recall_no_match_returns_empty(git_repo):
    store = Store.init(git_repo)
    store.add_feature(_cap("dropout", "raise dropout"))
    assert recall(store, "transformer") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_recall.py -v`
Expected: FAIL — `ModuleNotFoundError: rgit.recall`

- [ ] **Step 3: Implement**

`src/rgit/recall.py`:
```python
from __future__ import annotations
from .store.store import Store


def recall(store: Store, query: str) -> list[dict]:
    """Keyword+structural search; each hit carries its depends_on subgraph."""
    results = []
    for cap in store.find_features(query):
        deps = [store.get_feature(fid)
                for fid in store.neighbors(cap.id, "depends_on")]
        results.append({"capsule": cap, "depends_on": deps})
    return results
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_recall.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/rgit/recall.py tests/test_recall.py
git commit -m "feat: keyword+structural recall with depends_on subgraph"
```

---

## Task 12: Compose (regeneration brief)

**Files:**
- Create: `src/rgit/compose.py`
- Test: `tests/test_compose.py`

- [ ] **Step 1: Write the failing test**

`tests/test_compose.py`:
```python
from rgit.compose import compose
from rgit.store.store import Store
from rgit.store.models import Capsule, CodeSlice


def _cap(name, symbol):
    return Capsule(id="", name=name, intent=f"{name} intent", status="approved",
                   base_commit="abc", knobs={"lambda": 0.1},
                   data_assumptions="normalized inputs",
                   resurrection_guide=f"reapply {name}", result_summary=None,
                   payload_hash=None,
                   code_slices=[CodeSlice("model.py", symbol, "L1",
                                          "original code", "wrap")])


def test_compose_includes_capsule_fields_and_current_source(git_repo):
    store = Store.init(git_repo)
    (git_repo / "model.py").write_text("def forward(x):\n    return x + 1\n")
    fid = store.add_feature(_cap("scale", "forward"))
    brief = compose(store, [fid])
    item = brief["features"][0]
    assert item["intent"] == "scale intent"
    assert item["resurrection_guide"] == "reapply scale"
    assert item["data_assumptions"] == "normalized inputs"
    assert "return x + 1" in item["current_source"]["forward"]   # current code, not stored
    assert brief["conflicts"] == []


def test_compose_flags_conflicts_on_shared_symbol(git_repo):
    store = Store.init(git_repo)
    (git_repo / "model.py").write_text("def forward(x):\n    return x\n")
    a = store.add_feature(_cap("a", "forward"))
    b = store.add_feature(_cap("b", "forward"))
    brief = compose(store, [a, b])
    assert brief["conflicts"] == [{"file": "model.py", "symbol": "forward",
                                   "features": ["a", "b"]}]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_compose.py -v`
Expected: FAIL — `ModuleNotFoundError: rgit.compose`

- [ ] **Step 3: Implement**

`src/rgit/compose.py`:
```python
from __future__ import annotations
from collections import defaultdict

from .astmap import read_symbol_source
from .store.store import Store


def compose(store: Store, feature_ids: list[str]) -> dict:
    """Assemble a regeneration brief for the host agent."""
    features = []
    touch: dict[tuple[str, str], list[str]] = defaultdict(list)
    for fid in feature_ids:
        cap = store.get_feature(fid)
        current = {}
        for s in cap.code_slices:
            if s.symbol:
                current[s.symbol] = read_symbol_source(store.root, s.file, s.symbol) or ""
                touch[(s.file, s.symbol)].append(cap.name)
        features.append({
            "id": fid, "name": cap.name, "intent": cap.intent,
            "knobs": cap.knobs, "data_assumptions": cap.data_assumptions,
            "resurrection_guide": cap.resurrection_guide,
            "code_slices": [s.__dict__ for s in cap.code_slices],
            "current_source": current,
        })
    conflicts = [{"file": f, "symbol": s, "features": names}
                 for (f, s), names in touch.items() if len(names) > 1]
    return {"features": features, "conflicts": conflicts}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_compose.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/rgit/compose.py tests/test_compose.py
git commit -m "feat: compose regeneration brief with conflict detection"
```

---

## Task 13: Runner (rgit run orchestration)

**Files:**
- Create: `src/rgit/runner.py`
- Test: `tests/test_runner.py`

- [ ] **Step 1: Write the failing test**

`tests/test_runner.py`:
```python
from rgit.runner import run_experiment
from rgit.segmenter import MockSegmenter
from rgit.store.store import Store


def test_run_experiment_freezes_records_and_segments(git_repo):
    store = Store.init(git_repo)
    # an experiment that mutates code AND emits a metric
    (git_repo / "model.py").write_text("def forward(x):\n    return x * 3\n")
    (git_repo / "train.py").write_text("print('RGIT_METRIC acc=0.95')\n")
    candidate = {"name": "triple", "intent": "scale by 3",
                 "code_slices": [{"file": "model.py", "symbol": "forward",
                                  "anchor": "L1", "code": "x*3", "kind": "wrap"}],
                 "knobs": {}, "data_assumptions": None,
                 "resurrection_guide": "x3", "confidence": 0.9}
    run_id, prop_id = run_experiment(
        store, cmd=["python", "train.py"], segmenter=MockSegmenter([candidate]),
        now="2026-06-16T00:00:00")

    run = store.get_run(run_id)
    assert run.metrics == {"acc": 0.95}
    assert run.artifact_hash                       # froze the worktree
    prop = store.get_proposal(prop_id)
    assert prop.run_id == run_id                   # run linked to its proposal
    assert prop.candidates[0]["name"] == "triple"


def test_run_experiment_handles_missing_metrics(git_repo):
    store = Store.init(git_repo)
    (git_repo / "train.py").write_text("print('hello')\n")
    run_id, _ = run_experiment(store, ["python", "train.py"],
                               MockSegmenter([]), now="2026-06-16T00:00:00")
    assert store.get_run(run_id).metrics is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_runner.py -v`
Expected: FAIL — `ModuleNotFoundError: rgit.runner`

- [ ] **Step 3: Implement**

`src/rgit/runner.py`:
```python
from __future__ import annotations
import subprocess
from typing import Optional

from .gitutil import current_commit, freeze_worktree
from .metrics import parse_metrics
from .segmenter import Segmenter, segment_diff
from .store.models import Run
from .store.store import Store


def run_experiment(store: Store, cmd: list[str], segmenter: Segmenter,
                   now: str, env: Optional[dict] = None) -> tuple[str, str]:
    """Execute an experiment, freeze the artifact, record the run, segment the diff.

    Returns (run_id, proposal_id). `now` is an ISO timestamp injected by the
    caller (keeps the function deterministic for tests).
    """
    base = current_commit(store.root)
    proc = subprocess.run(cmd, cwd=store.root, capture_output=True, text=True)
    metrics = parse_metrics(proc.stdout, store.root)
    artifact = freeze_worktree(store.root, store.objects)
    run_id = store.add_run(Run(
        id="", cmd=" ".join(cmd), artifact_hash=artifact, metrics=metrics,
        base_commit=base, env=env, created_at=now))
    prop_id = segment_diff(store, trigger="run", segmenter=segmenter, run_id=run_id)
    return run_id, prop_id
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_runner.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/rgit/runner.py tests/test_runner.py
git commit -m "feat: rgit run orchestration (execute, freeze, record, segment)"
```

---

## Task 14: Git hooks installer

**Files:**
- Create: `src/rgit/hooks.py`
- Test: `tests/test_hooks.py`

- [ ] **Step 1: Write the failing test**

`tests/test_hooks.py`:
```python
import os
import stat
from rgit.hooks import install_hooks


def test_install_writes_executable_post_commit_hook(git_repo):
    install_hooks(git_repo)
    hook = git_repo / ".git" / "hooks" / "post-commit"
    assert hook.exists()
    assert "rgit capture --trigger commit" in hook.read_text()
    assert os.stat(hook).st_mode & stat.S_IXUSR        # executable
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_hooks.py -v`
Expected: FAIL — `ModuleNotFoundError: rgit.hooks`

- [ ] **Step 3: Implement**

`src/rgit/hooks.py`:
```python
from __future__ import annotations
import os
import stat
from pathlib import Path

_POST_COMMIT = "#!/bin/sh\n# installed by research-git\nrgit capture --trigger commit || true\n"


def install_hooks(repo: Path) -> None:
    hooks_dir = Path(repo) / ".git" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    hook = hooks_dir / "post-commit"
    hook.write_text(_POST_COMMIT)
    hook.chmod(hook.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_hooks.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add src/rgit/hooks.py tests/test_hooks.py
git commit -m "feat: git post-commit hook installer"
```

---

## Task 15: CLI

**Files:**
- Create: `src/rgit/cli.py`
- Test: `tests/test_cli.py`

The CLI uses the real `AnthropicSegmenter` by default, but reads an injected segmenter from a module-level hook (`_SEGMENTER`) so tests can substitute `MockSegmenter` without network calls.

- [ ] **Step 1: Write the failing test**

`tests/test_cli.py`:
```python
import json
import rgit.cli as cli
from rgit.segmenter import MockSegmenter
from rgit.store.store import Store


def test_init_creates_rgit_and_hook(git_repo, monkeypatch):
    monkeypatch.chdir(git_repo)
    assert cli.main(["init"]) == 0
    assert (git_repo / ".rgit" / "graph.db").exists()
    assert (git_repo / ".git" / "hooks" / "post-commit").exists()


def test_run_then_review_then_features(git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    cli.main(["init"])
    (git_repo / "train.py").write_text("print('RGIT_METRIC acc=0.95')\n")
    (git_repo / "model.py").write_text("def forward(x):\n    return x * 3\n")
    candidate = {"name": "triple", "intent": "scale by 3",
                 "code_slices": [{"file": "model.py", "symbol": "forward",
                                  "anchor": "L1", "code": "x*3", "kind": "wrap"}],
                 "knobs": {}, "data_assumptions": None,
                 "resurrection_guide": "x3", "confidence": 0.9}
    cli._SEGMENTER = MockSegmenter([candidate])           # inject, no network

    assert cli.main(["run", "--", "python", "train.py"]) == 0
    out = capsys.readouterr().out
    assert "proposal" in out.lower()

    # one open proposal exists; approve it by index 0
    store = Store.open(git_repo)
    pid = store.list_proposals("open")[0].id
    assert cli.main(["review", "--approve", pid, "--name", "triple"]) == 0

    assert cli.main(["features"]) == 0
    assert "triple" in capsys.readouterr().out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli.py -v`
Expected: FAIL — `AttributeError: module 'rgit.cli' has no attribute 'main'`

- [ ] **Step 3: Implement**

`src/rgit/cli.py`:
```python
from __future__ import annotations
import argparse
import datetime
from typing import Optional

from .curation import approve, dismiss
from .hooks import install_hooks
from .runner import run_experiment
from .segmenter import Segmenter, segment_diff
from .store.store import Store

# Test seam: when set, used instead of constructing AnthropicSegmenter.
_SEGMENTER: Optional[Segmenter] = None


def _segmenter() -> Segmenter:
    if _SEGMENTER is not None:
        return _SEGMENTER
    from .segmenter import AnthropicSegmenter
    return AnthropicSegmenter()


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="rgit")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init")

    p_run = sub.add_parser("run")
    p_run.add_argument("rest", nargs=argparse.REMAINDER)  # after `--`

    p_cap = sub.add_parser("capture")
    p_cap.add_argument("--trigger", default="manual")

    p_rev = sub.add_parser("review")
    p_rev.add_argument("--approve")
    p_rev.add_argument("--name")
    p_rev.add_argument("--index", type=int, default=0)
    p_rev.add_argument("--dismiss")

    sub.add_parser("features")

    args = parser.parse_args(argv)

    if args.cmd == "init":
        Store.init(_find_root())
        install_hooks(_find_root())
        print(f"initialized .rgit/ in {_find_root()}")
        return 0

    store = Store.open()

    if args.cmd == "run":
        cmd = [a for a in args.rest if a != "--"]
        run_id, prop_id = run_experiment(store, cmd, _segmenter(), now=_now())
        print(f"run {run_id} recorded; proposal {prop_id} awaiting review")
        return 0

    if args.cmd == "capture":
        pid = segment_diff(store, args.trigger, _segmenter(), run_id=None)
        print(f"proposal {pid} created")
        return 0

    if args.cmd == "review":
        if args.dismiss:
            dismiss(store, args.dismiss)
            print(f"dismissed {args.dismiss}")
            return 0
        if args.approve:
            fid = approve(store, args.approve, args.index, args.name)
            print(f"approved -> feature {fid}")
            return 0
        for p in store.list_proposals("open"):
            names = ", ".join(c["name"] for c in p.candidates)
            print(f"{p.id}  [{p.trigger}]  candidates: {names}")
        return 0

    if args.cmd == "features":
        for c in store.list_features():
            print(f"{c.id}  {c.name}  — {c.intent}")
        return 0

    return 1


def _find_root():
    import subprocess
    from pathlib import Path
    out = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                         capture_output=True, text=True, check=True)
    return Path(out.stdout.strip())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_cli.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/rgit/cli.py tests/test_cli.py
git commit -m "feat: rgit CLI (init/run/capture/review/features)"
```

---

## Task 16: MCP server

**Files:**
- Create: `src/rgit/mcp_server.py`
- Test: `tests/test_mcp_server.py`

The MCP tools are thin wrappers over `recall`/`compose`/store getters. We test the underlying tool *functions* directly (FastMCP wiring needs no separate test); capsules are returned as plain dicts so they cross the MCP boundary as JSON.

- [ ] **Step 1: Write the failing test**

`tests/test_mcp_server.py`:
```python
import rgit.mcp_server as srv
from rgit.store.store import Store
from rgit.store.models import Capsule, CodeSlice


def _cap(name):
    return Capsule(id="", name=name, intent=f"{name} intent", status="approved",
                   base_commit="abc", knobs={}, data_assumptions=None,
                   resurrection_guide="reapply", result_summary=None,
                   payload_hash=None,
                   code_slices=[CodeSlice("model.py", "forward", "L1", "x", "wrap")])


def test_recall_tool_returns_serializable_dicts(git_repo, monkeypatch):
    monkeypatch.chdir(git_repo)
    store = Store.init(git_repo)
    store.add_feature(_cap("contrastive-loss"))
    out = srv.recall_tool("contrastive")
    assert out[0]["capsule"]["name"] == "contrastive-loss"
    assert "depends_on" in out[0]


def test_compose_tool_returns_brief(git_repo, monkeypatch):
    monkeypatch.chdir(git_repo)
    store = Store.init(git_repo)
    (git_repo / "model.py").write_text("def forward(x):\n    return x\n")
    fid = store.add_feature(_cap("scale"))
    brief = srv.compose_tool([fid])
    assert brief["features"][0]["name"] == "scale"
    assert brief["conflicts"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_mcp_server.py -v`
Expected: FAIL — `ModuleNotFoundError: rgit.mcp_server`

- [ ] **Step 3: Implement**

`src/rgit/mcp_server.py`:
```python
from __future__ import annotations
from typing import Any

from mcp.server.fastmcp import FastMCP

from .compose import compose
from .recall import recall
from .store.store import Store

mcp = FastMCP("research-git")


def _capsule_dict(cap) -> dict[str, Any]:
    return cap.to_dict()


def recall_tool(query: str) -> list[dict]:
    """Find feature capsules by keyword/structure; include depends_on subgraph."""
    store = Store.open()
    return [{"capsule": _capsule_dict(r["capsule"]),
             "depends_on": [_capsule_dict(d) for d in r["depends_on"]]}
            for r in recall(store, query)]


def compose_tool(feature_ids: list[str]) -> dict:
    """Build a regeneration brief for the given capsules onto current code."""
    return compose(Store.open(), feature_ids)


def get_feature_tool(feature_id: str) -> dict:
    """Fetch a single capsule by id."""
    return _capsule_dict(Store.open().get_feature(feature_id))


def list_features_tool() -> list[dict]:
    """List all approved/proposed capsules."""
    return [_capsule_dict(c) for c in Store.open().list_features()]


# Register as MCP tools (functions remain directly unit-testable).
mcp.tool()(recall_tool)
mcp.tool()(compose_tool)
mcp.tool()(get_feature_tool)
mcp.tool()(list_features_tool)


def run() -> None:  # pragma: no cover - entry point
    mcp.run()


if __name__ == "__main__":  # pragma: no cover
    run()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_mcp_server.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/rgit/mcp_server.py tests/test_mcp_server.py
git commit -m "feat: FastMCP server exposing recall/compose/get tools"
```

---

## Task 17: End-to-end + reproducibility test

**Files:**
- Test: `tests/test_e2e.py`

This is the §9.4 demo narrative and the §9.5 reproducibility contract, end to end, with a mocked segmenter (no network).

- [ ] **Step 1: Write the failing test**

`tests/test_e2e.py`:
```python
import subprocess
from rgit.runner import run_experiment
from rgit.curation import approve
from rgit.recall import recall
from rgit.compose import compose
from rgit.gitutil import materialize
from rgit.segmenter import MockSegmenter
from rgit.store.store import Store


def _commit(repo, msg):
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-q", "-m", msg], cwd=repo, check=True,
                   capture_output=True)


def test_full_memory_loop(git_repo):
    store = Store.init(git_repo)

    # 1. messy work: add a contrastive loss + emit a metric
    (git_repo / "model.py").write_text(
        "def forward(x):\n    return x\n\n"
        "def compute_loss(p, y):\n    return ((p - y) ** 2).mean() + 0.1 * aux(p)\n")
    (git_repo / "train.py").write_text("print('RGIT_METRIC acc=0.93')\n")
    candidate = {
        "name": "contrastive-loss-aux", "intent": "add aux contrastive loss term",
        "code_slices": [{"file": "model.py", "symbol": "compute_loss",
                         "anchor": "L4-L5", "code": "+ 0.1 * aux(p)", "kind": "insert"}],
        "knobs": {"lambda": 0.1}, "data_assumptions": "normalized embeddings",
        "resurrection_guide": "add 0.1*aux(p) inside compute_loss", "confidence": 0.95}

    # 2. rgit run -> freeze + run node + proposal
    run_id, prop_id = run_experiment(store, ["python", "train.py"],
                                     MockSegmenter([candidate]), now="2026-06-16T00:00:00")
    assert store.get_run(run_id).metrics == {"acc": 0.93}
    frozen_hash = store.get_run(run_id).artifact_hash

    # 3. approve -> capsule with produced edge to the run
    fid = approve(store, prop_id, 0, name="contrastive-loss-aux")
    assert store.neighbors(fid, "produced") == [run_id]

    # commit so HEAD advances, then refactor infra under the feature
    _commit(git_repo, "feature + infra")
    (git_repo / "model.py").write_text(
        "def forward(x, scale=1):\n    return x * scale\n\n"
        "def compute_loss(pred, target):\n    return ((pred - target) ** 2).mean()\n")

    # 4. recall + compose against the *refactored* code
    hits = recall(store, "contrastive")
    assert hits[0]["capsule"].name == "contrastive-loss-aux"
    brief = compose(store, [fid])
    item = brief["features"][0]
    assert item["resurrection_guide"] == "add 0.1*aux(p) inside compute_loss"
    assert "pred" in item["current_source"]["compute_loss"]   # sees current, refactored code

    # 5. reproducibility: the frozen artifact replays byte-identically
    dest = git_repo / ".rgit" / "replay"
    materialize(store.objects, frozen_hash, dest)
    assert "0.1 * aux(p)" in (dest / "model.py").read_text()   # exact code that ran
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_e2e.py -v`
Expected: FAIL (if any wiring is off) — fix until green. With Tasks 1–16 complete it should pass directly.

- [ ] **Step 3: Make it pass**

No new product code should be needed. If it fails, fix the offending module (not the test) until green.

- [ ] **Step 4: Run the full suite**

Run: `pytest -q`
Expected: PASS (all tests green)

- [ ] **Step 5: Commit**

```bash
git add tests/test_e2e.py
git commit -m "test: end-to-end memory loop + reproducibility"
```

---

## Self-Review

**Spec coverage:**
- §5 subsystems → Observer (Task 6 diff + Task 14 hooks + Task 15 CLI), Segmenter (Task 9), Graph store (Tasks 2–5), Curation (Task 10), Retrieval (Task 11 + Task 16), Regeneration brief (Task 12 + Task 16; regeneration itself done by host agent), Freeze+run (Tasks 6, 13). ✓
- §6 Feature Capsule (intent, code slices w/ symbol, knobs, data_assumptions, result_summary, resurrection_guide) → models Task 3, persisted Task 5, drafted by segmenter Task 9, consumed by compose Task 12. ✓ `result_summary` is modeled and persisted; authoritative metrics on run nodes via `produced` edge (Task 10/13). ✓
- §7 reproducibility (frozen, content-addressed artifact; replay byte-identical) → Task 6 + Task 17. ✓
- §9.2 CLI surface (init/run/capture/review/features) → Task 15. ✓
- §9.3 MCP tools (recall/compose/get/list) → Task 16. ✓
- §9.4 demo narrative → Task 17. ✓
- §9.6 error handling: no prior run → diff vs HEAD (Task 6/9); missing metrics → None (Task 13); conflicts surfaced (Task 12); nothing auto-committed (regeneration is host-agent + explicit `rgit run`). ✓

**Deferred by design (v2/v3, not gaps):** embeddings/semantic recall, idle daemon, merge/split UX, conflict auto-resolution, comment-toggle events, resurrection-guide auto-refresh-on-success (the seam exists in compose; wiring the write-back is a v2 follow-up).

**Placeholder scan:** none — every step has runnable code/commands.

**Type consistency:** `Capsule`/`CodeSlice`/`ResultSummary`/`Run`/`Edge`/`Proposal` fields are identical across Tasks 3, 5, 9, 10, 12, 16. `segment_diff(store, trigger, segmenter, run_id)`, `approve(store, proposal_id, candidate_index, name)`, `compose(store, feature_ids)`, `recall(store, query)`, `run_experiment(store, cmd, segmenter, now, env)` signatures match all call sites. ✓
