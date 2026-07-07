# History Digestion (`rgit digest`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Digest a mature repo's git history into `origin=backfill` capsules: a free deterministic scan/cluster/rank queue in the engine, drained batch-by-batch by a new `rgit-digest` plugin skill with no per-capsule approval gate.

**Architecture:** A new pure-planning module `digestscan.py` walks the first-parent mainline and clusters commits into scored "digestion units" (merge = PR unit, author streaks merge, revert pairs become dead-experiment units). `digestqueue.py` persists units in a new `digest_units` table and drives the lifecycle pending → staged (proposal via the existing `DiffSource`/`segment_diff` pipeline, `trigger="backfill"`) → done (`accept` materializes every candidate as an approved capsule with the new `Capsule.origin="backfill"` marker). CLI grows a `rgit digest` family plus an init-time offer; the plugin grows a third skill that dispatches the existing `capsule-segmenter`/`edge-judge` agents.

**Tech Stack:** Python 3.11 stdlib (sqlite3, subprocess, argparse), git plumbing, pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-07-05-rgit-history-digest-design.md`

## Global Constraints

- Python 3.11-compatible and Windows-safe: subprocess only through `_git`-style wrappers with `encoding="utf-8", errors="replace"`, or raw bytes + `os.fsdecode`; no `shell=True`; no POSIX-only APIs.
- The scan uses `git log --diff-merges=first-parent`, which needs **git ≥ 2.31** (2021). Older git fails the subprocess; the CLI surfaces the error, no silent fallback.
- CLI stdout stays clean JSON / machine-readable text; prompts and notices go to stderr (init's informational notes stay on stdout, matching today's init).
- The engine never calls an LLM. Tests never dispatch agents: use `MockSegmenter`, `HeuristicSegmenter`, or inject candidates via `store.set_proposal_candidates`.
- New test fixtures pin `core.autocrlf false` and pin commit timestamps via `GIT_AUTHOR_DATE`/`GIT_COMMITTER_DATE` env so results are byte-identical and deterministic on all platforms.
- Defaults (from the spec, all as named constants): scan window **400** first-parent commits; streak caps **10 commits / 48h gap / 4000 changed lines**; staged-diff oversized flag at **300_000 bytes**; batch size **10**; edge-judge quota **30** pairs per batch (skill-level, engine takes any `--limit`).
- Capsule origin vocabulary: exactly `"live"` (default) and `"backfill"`.
- `pyproject.toml` package-data globs (`_plugin/skills/*/*.md`) already cover a new skill directory — do not change them; do keep the new skill's files inside `src/rgit/_plugin/skills/rgit-digest/`.
- Run the full suite with `python -m pytest -q` (local venv: `.venv/bin/python -m pytest -q`) before each commit; a task is done only when the whole suite passes.

---

### Task 1: gitutil history plumbing + scripted-history test helpers

**Files:**
- Modify: `src/rgit/gitutil.py` (append after `RangeDiffSource`, before `_snapshot_paths`)
- Modify: `tests/conftest.py` (append helpers + fixture)
- Test: `tests/test_gitutil.py` (append)

**Interfaces:**
- Consumes: existing `_git(repo, *args)`, `resolve_commit`, `read_committed_python`, `current_commit` in `gitutil.py`.
- Produces (used by Tasks 2, 4, 8):
  - `EMPTY_TREE: str` — git's canonical empty-tree hash.
  - `is_shallow(repo: Path) -> bool`
  - `mainline_count(repo: Path) -> int` — first-parent commit count of HEAD (raises `subprocess.CalledProcessError` on unborn HEAD).
  - `mainline_commits(repo: Path, range_spec: Optional[str] = None, limit: Optional[int] = None) -> list[dict]` — records oldest→newest, each `{"sha", "parents": list[str], "at": int, "author", "subject", "body", "files": list[str], "churn": int}`.
  - `head_files(repo: Path) -> set[str]` — paths in HEAD's tree.
  - `class EmptyTreeRangeDiffSource` — a `DiffSource` whose base is the empty tree.
- Produces (test helpers in `conftest.py`, used by Tasks 2, 4, 6, 8, 10): `commit_file(repo, path, content, subject, *, when, author="t") -> str`, `revert_head(repo, *, when, author="t") -> str`, `merge_branch(repo, files, subject, *, when, author="t") -> str`, `history_repo` fixture (empty initialized repo, no commits).

- [ ] **Step 1: Add the test helpers to `tests/conftest.py`**

Append to `tests/conftest.py`:

```python
import os


def _commit_env(when: int, author: str) -> dict:
    """Env pinning author+committer identity and timestamps, for determinism."""
    return {
        "GIT_AUTHOR_NAME": author, "GIT_AUTHOR_EMAIL": f"{author}@t.t",
        "GIT_COMMITTER_NAME": author, "GIT_COMMITTER_EMAIL": f"{author}@t.t",
        "GIT_AUTHOR_DATE": f"{when} +0000", "GIT_COMMITTER_DATE": f"{when} +0000",
    }


def _head_sha(repo: Path) -> str:
    out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True,
                         capture_output=True, text=True)
    return out.stdout.strip()


def commit_file(repo: Path, path: str, content: str, subject: str, *,
                when: int, author: str = "t") -> str:
    """One commit touching one file, with pinned author/date. Returns the sha."""
    p = repo / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    _run(["git", "add", "-A"], repo)
    subprocess.run(["git", "commit", "-q", "-m", subject], cwd=repo, check=True,
                   capture_output=True, env={**os.environ, **_commit_env(when, author)})
    return _head_sha(repo)


def revert_head(repo: Path, *, when: int, author: str = "t") -> str:
    """`git revert --no-edit HEAD` (writes the standard 'This reverts commit'
    trailer the scanner detects). Returns the revert commit's sha."""
    subprocess.run(["git", "revert", "--no-edit", "HEAD"], cwd=repo, check=True,
                   capture_output=True, env={**os.environ, **_commit_env(when, author)})
    return _head_sha(repo)


def merge_branch(repo: Path, files: list, subject: str, *,
                 when: int, author: str = "t") -> str:
    """Side branch off HEAD with one commit per (path, content, subject) in
    `files`, merged back --no-ff. Returns the merge commit's sha."""
    main = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo,
                          check=True, capture_output=True, text=True).stdout.strip()
    _run(["git", "checkout", "-q", "-b", "side"], repo)
    for i, (path, content, sub) in enumerate(files):
        commit_file(repo, path, content, sub, when=when + i, author=author)
    _run(["git", "checkout", "-q", main], repo)
    subprocess.run(["git", "merge", "--no-ff", "-q", "-m", subject, "side"],
                   cwd=repo, check=True, capture_output=True,
                   env={**os.environ, **_commit_env(when + len(files), author)})
    _run(["git", "branch", "-q", "-D", "side"], repo)
    return _head_sha(repo)


@pytest.fixture
def history_repo(tmp_path: Path) -> Path:
    """Initialized git repo with NO commits — tests script the whole history
    with pinned dates so clustering decisions are deterministic."""
    _run(["git", "init", "-q"], tmp_path)
    _run(["git", "config", "user.email", "t@t.t"], tmp_path)
    _run(["git", "config", "user.name", "t"], tmp_path)
    _run(["git", "config", "core.autocrlf", "false"], tmp_path)
    return tmp_path
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_gitutil.py`:

```python
from conftest import commit_file, merge_branch, revert_head


T0 = 1_700_000_000  # pinned base timestamp for scripted histories


def test_mainline_commits_walks_oldest_to_newest(history_repo):
    from rgit.gitutil import mainline_commits, mainline_count
    a = commit_file(history_repo, "a.py", "x = 1\n", "first", when=T0)
    b = commit_file(history_repo, "a.py", "x = 2\n", "second", when=T0 + 60)
    commits = mainline_commits(history_repo)
    assert [c["sha"] for c in commits] == [a, b]
    assert commits[0]["parents"] == []            # root commit
    assert commits[1]["parents"] == [a]
    assert commits[0]["at"] == T0
    assert commits[0]["subject"] == "first"
    assert commits[0]["files"] == ["a.py"]
    assert commits[0]["churn"] == 1
    assert mainline_count(history_repo) == 2


def test_mainline_commits_merge_shows_first_parent_numstat(history_repo):
    from rgit.gitutil import mainline_commits
    commit_file(history_repo, "a.py", "x = 1\n", "base", when=T0)
    m = merge_branch(history_repo, [("b.py", "y = 1\ny = 2\n", "side work")],
                     "merge side", when=T0 + 100)
    commits = mainline_commits(history_repo)
    merge = commits[-1]
    assert merge["sha"] == m
    assert len(merge["parents"]) == 2
    assert merge["files"] == ["b.py"]             # diff vs first parent
    assert merge["churn"] == 2


def test_mainline_commits_limit_takes_most_recent(history_repo):
    from rgit.gitutil import mainline_commits
    commit_file(history_repo, "a.py", "1\n", "one", when=T0)
    commit_file(history_repo, "a.py", "2\n", "two", when=T0 + 1)
    c3 = commit_file(history_repo, "a.py", "3\n", "three", when=T0 + 2)
    commits = mainline_commits(history_repo, limit=1)
    assert [c["sha"] for c in commits] == [c3]


def test_revert_body_carries_trailer(history_repo):
    from rgit.gitutil import mainline_commits
    commit_file(history_repo, "a.py", "x = 1\n", "base", when=T0)
    exp = commit_file(history_repo, "a.py", "x = 99\n", "experiment", when=T0 + 60)
    revert_head(history_repo, when=T0 + 120)
    commits = mainline_commits(history_repo)
    assert f"This reverts commit {exp}" in commits[-1]["body"]


def test_head_files_lists_tracked_tree(history_repo):
    from rgit.gitutil import head_files
    commit_file(history_repo, "a.py", "x = 1\n", "one", when=T0)
    commit_file(history_repo, "pkg/b.py", "y = 1\n", "two", when=T0 + 1)
    assert head_files(history_repo) == {"a.py", "pkg/b.py"}


def test_empty_tree_range_diff_source(history_repo):
    from rgit.gitutil import EmptyTreeRangeDiffSource
    commit_file(history_repo, "a.py", "def f():\n    return 1\n", "one", when=T0)
    sha = commit_file(history_repo, "b.py", "def g():\n    return 2\n", "two",
                      when=T0 + 1)
    src = EmptyTreeRangeDiffSource(sha)
    diff = src.diff(history_repo)
    assert "+++ b/a.py" in diff and "+++ b/b.py" in diff
    assert src.source_commit(history_repo) == sha
    assert src.read_new_side(history_repo, "a.py") == "def f():\n    return 1\n"


def test_is_shallow_false_on_full_clone(history_repo):
    from rgit.gitutil import is_shallow
    commit_file(history_repo, "a.py", "x\n", "one", when=T0)
    assert is_shallow(history_repo) is False
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `python -m pytest tests/test_gitutil.py -k "mainline or empty_tree or head_files or is_shallow or revert_body" -v`
Expected: FAIL / ERROR with `ImportError: cannot import name 'mainline_commits'`.

- [ ] **Step 4: Implement in `src/rgit/gitutil.py`**

Insert after the `RangeDiffSource` class (before `_snapshot_paths`):

```python
# git's canonical empty-tree object: the diff base for a history slice that
# includes the root commit (there is no `root^` to anchor a range).
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"

# Record/field separators for the history walk. Control chars cannot appear in
# shas, timestamps, or author names, and a commit body containing them is
# pathological enough that a truncated body is an acceptable parse.
_LOG_RECORD = "\x01"
_LOG_FIELD = "\x1f"
_LOG_BODY_END = "\x02"


def is_shallow(repo: Path) -> bool:
    return _git(repo, "rev-parse", "--is-shallow-repository").strip() == "true"


def mainline_count(repo: Path) -> int:
    """First-parent commit count of HEAD. Raises CalledProcessError when HEAD
    is unborn (fresh `git init`) — callers treat that as 'no history'."""
    return int(_git(repo, "rev-list", "--first-parent", "--count", "HEAD").strip())


def mainline_commits(repo: Path, range_spec: Optional[str] = None,
                     limit: Optional[int] = None) -> list[dict]:
    """First-parent commit records, oldest -> newest.

    One `git log` invocation carries everything the scanner needs: metadata,
    the full body (revert trailers live there), and per-file numstat. Merge
    commits report their diff vs the first parent (`--diff-merges=first-parent`,
    git >= 2.31) — exactly the "what the PR brought in" view the digest wants.
    `limit` takes the most recent N; `range_spec` (A..B) overrides the default
    HEAD walk. Binary numstat entries ("-") count files but no churn.
    """
    fmt = (f"{_LOG_RECORD}%H{_LOG_FIELD}%P{_LOG_FIELD}%at{_LOG_FIELD}%an"
           f"{_LOG_FIELD}%s{_LOG_FIELD}%B{_LOG_BODY_END}")
    args = ["log", "--first-parent", "--no-renames",
            "--diff-merges=first-parent", "--numstat", f"--format={fmt}"]
    if limit is not None:
        args += ["-n", str(limit)]
    args.append(range_spec if range_spec else "HEAD")
    args.append("--")
    out = _git(repo, "-c", "core.quotePath=false", *args)
    commits: list[dict] = []
    for record in out.split(_LOG_RECORD):
        if not record.strip():
            continue
        header, _, tail = record.partition(_LOG_BODY_END)
        sha, parents, at, author, subject, body = header.split(_LOG_FIELD, 5)
        files: list[str] = []
        churn = 0
        for line in tail.splitlines():
            parts = line.split("\t")
            if len(parts) != 3 or not parts[2]:
                continue
            added, deleted, path = parts
            files.append(path)
            if added.isdigit():
                churn += int(added)
            if deleted.isdigit():
                churn += int(deleted)
        commits.append({"sha": sha, "parents": parents.split(), "at": int(at),
                        "author": author, "subject": subject, "body": body,
                        "files": files, "churn": churn})
    commits.reverse()                      # git log emits newest first
    return commits


def head_files(repo: Path) -> set[str]:
    """Paths present in HEAD's tree (committed state, not the index)."""
    out = subprocess.run(["git", "ls-tree", "-r", "--name-only", "-z", "HEAD"],
                         cwd=repo, check=True, capture_output=True)
    return {os.fsdecode(p) for p in out.stdout.split(b"\0") if p}


class EmptyTreeRangeDiffSource:
    """Capture source: everything up to `head`, diffed against the empty tree.

    A digest streak containing the root commit has no `first^` to anchor a
    RangeDiffSource, so the whole slice is one add-only patch from nothing.
    """

    def __init__(self, head: str):
        self.head = head
        self._sha: Optional[str] = None

    def _resolved(self, repo: Path) -> str:
        if self._sha is None:
            self._sha = resolve_commit(repo, self.head)
        return self._sha

    def diff(self, repo: Path) -> str:
        return _git(repo, "-c", "core.quotePath=false", "diff-tree", "-p",
                    "--no-renames", EMPTY_TREE, self._resolved(repo), "--",
                    ":(exclude).rgit")

    def read_new_side(self, repo: Path, file: str) -> Optional[str]:
        return read_committed_python(repo, self._resolved(repo), file)

    def source_commit(self, repo: Path) -> Optional[str]:
        return self._resolved(repo)

    def no_diff_reason(self, repo: Path) -> str:
        return f"history up to {self.head} has no diff"
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_gitutil.py -v`
Expected: PASS (all, including pre-existing tests).

- [ ] **Step 6: Run the full suite and commit**

Run: `python -m pytest -q` — expected: all pass.

```bash
git add src/rgit/gitutil.py tests/conftest.py tests/test_gitutil.py
git commit -m "feat(gitutil): mainline history walk + empty-tree diff source"
```

---

### Task 2: `digestscan.py` — cluster mainline history into scored units

**Files:**
- Create: `src/rgit/digestscan.py`
- Test: `tests/test_digestscan.py` (new)

**Interfaces:**
- Consumes: Task 1's `mainline_commits`, `mainline_count`, `head_files`, `is_shallow`, plus existing `current_commit`.
- Produces (used by Tasks 4, 6, 8):
  - Constants: `MODES = ("layered", "trunk", "dead", "archaeology")`, `DEFAULT_WINDOW = 400`, `STREAK_MAX_COMMITS = 10`, `STREAK_MAX_GAP_SECONDS = 48 * 3600`, `STREAK_MAX_CHURN = 4000`, `UNIT_MAX_DIFF_BYTES = 300_000`.
  - `unit_id(shas: list[str]) -> str` — `"dig_" + sha256(sorted shas)[:16]`, the idempotence key.
  - `scan(repo: Path, *, range_spec: Optional[str] = None, window: int = DEFAULT_WINDOW, all_history: bool = False) -> dict` returning `{"units": list[dict], "total_mainline": int, "window_applied": bool, "shallow": bool, "head": str}`. Each unit dict: `{"id", "kind": "landed"|"dead", "shas": list[str] oldest→newest, "score": float, "status": "pending"|"skipped", "skip_reason": None|"infra", "meta": dict}`. `meta` keys: `subjects` (list), `author`, `start_at`, `end_at`, `start_date`, `end_date` (ISO dates), `files` (≤50 sample), `files_count`, `code_files`, `churn`, `merge` (bool), `has_root` (bool), `oversized` (bool), and for dead units `dead` (`"reverted"`|`"deleted"`), plus for reverted ones `reverted_by`, `revert_at`, `revert_date`, `revert_subject`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_digestscan.py`:

```python
from conftest import commit_file, merge_branch, revert_head
from rgit.digestscan import scan, unit_id

T0 = 1_700_000_000
DAY = 86_400


def _unit_for(res, sha):
    return next(u for u in res["units"] if sha in u["shas"])


def test_streak_clusters_same_author_related_files(history_repo):
    a = commit_file(history_repo, "m.py", "x = 1\n", "one", when=T0)
    b = commit_file(history_repo, "m.py", "x = 2\n", "two", when=T0 + 3600)
    res = scan(history_repo)
    unit = _unit_for(res, a)
    assert unit["shas"] == [a, b]                 # one unit, oldest -> newest
    assert unit["kind"] == "landed"
    assert unit["meta"]["has_root"] is True
    assert unit["meta"]["subjects"] == ["one", "two"]


def test_streak_breaks_on_author_gap_and_disjoint_files(history_repo):
    a = commit_file(history_repo, "m.py", "x = 1\n", "one", when=T0)
    b = commit_file(history_repo, "m.py", "x = 2\n", "two", when=T0 + 3 * DAY)  # gap
    c = commit_file(history_repo, "other.py", "y = 1\n", "three",
                    when=T0 + 3 * DAY + 60)                                     # no file overlap
    d = commit_file(history_repo, "other.py", "y = 2\n", "four",
                    when=T0 + 3 * DAY + 120, author="someone-else")             # author switch
    res = scan(history_repo)
    units = {tuple(u["shas"]) for u in res["units"]}
    assert (a,) in units and (b,) in units and (c,) in units and (d,) in units


def test_merge_commit_is_its_own_unit(history_repo):
    commit_file(history_repo, "base.py", "b = 1\n", "base", when=T0)
    m = merge_branch(history_repo, [("feat.py", "f = 1\n", "feat work")],
                     "merge feature", when=T0 + 100)
    res = scan(history_repo)
    unit = _unit_for(res, m)
    assert unit["shas"] == [m]
    assert unit["meta"]["merge"] is True
    assert unit["meta"]["files"] == ["feat.py"]


def test_revert_pair_becomes_dead_unit_and_revert_disappears(history_repo):
    commit_file(history_repo, "m.py", "x = 1\n", "base", when=T0)
    exp = commit_file(history_repo, "m.py", "x = 99\n", "wild experiment",
                      when=T0 + 60)
    rev = revert_head(history_repo, when=T0 + 120)
    res = scan(history_repo)
    unit = _unit_for(res, exp)
    assert unit["kind"] == "dead"
    assert unit["shas"] == [exp]
    assert unit["meta"]["dead"] == "reverted"
    assert unit["meta"]["reverted_by"] == rev
    assert unit["meta"]["revert_subject"].startswith("Revert")
    assert all(rev not in u["shas"] for u in res["units"])   # revert consumed


def test_deleted_files_make_dead_unit(history_repo):
    import subprocess
    commit_file(history_repo, "keep.py", "k = 1\n", "keep", when=T0)
    commit_file(history_repo, "gone.py", "g = 1\n", "doomed feature",
                when=T0 + 5 * DAY)
    subprocess.run(["git", "rm", "-q", "gone.py"], cwd=history_repo, check=True,
                   capture_output=True)
    import os
    from conftest import _commit_env
    subprocess.run(["git", "commit", "-q", "-m", "remove it"], cwd=history_repo,
                   check=True, capture_output=True,
                   env={**os.environ, **_commit_env(T0 + 10 * DAY, "t")})
    res = scan(history_repo)
    doomed = next(u for u in res["units"] if u["meta"]["subjects"] == ["doomed feature"])
    assert doomed["kind"] == "dead"
    assert doomed["meta"]["dead"] == "deleted"


def test_infra_only_unit_is_preskipped(history_repo):
    commit_file(history_repo, "m.py", "x = 1\n", "code", when=T0)
    infra = commit_file(history_repo, "README.md", "# docs\n", "docs only",
                        when=T0 + 5 * DAY)
    res = scan(history_repo)
    unit = _unit_for(res, infra)
    assert unit["status"] == "skipped"
    assert unit["skip_reason"] == "infra"


def test_dead_outranks_similar_landed(history_repo):
    a = commit_file(history_repo, "m.py", "x = 1\n", "landed", when=T0)
    b = commit_file(history_repo, "n.py", "y = 1\n", "will die",
                    when=T0 + 5 * DAY, author="u2")
    revert_head(history_repo, when=T0 + 6 * DAY)
    res = scan(history_repo)
    dead = _unit_for(res, b)
    landed = _unit_for(res, a)
    assert dead["score"] > landed["score"]


def test_window_and_idempotent_ids(history_repo):
    shas = [commit_file(history_repo, "m.py", f"x = {i}\n", f"c{i}",
                        when=T0 + i * 3 * DAY) for i in range(4)]
    res = scan(history_repo, window=2)
    scanned = {s for u in res["units"] for s in u["shas"]}
    assert scanned == set(shas[-2:])              # most recent window only
    assert res["window_applied"] is True
    assert res["total_mainline"] == 4
    res2 = scan(history_repo, window=2)
    assert {u["id"] for u in res["units"]} == {u["id"] for u in res2["units"]}
    assert unit_id(["b", "a"]) == unit_id(["a", "b"])   # order-insensitive


def test_explicit_range_overrides_window(history_repo):
    shas = [commit_file(history_repo, "m.py", f"x = {i}\n", f"c{i}",
                        when=T0 + i * 3 * DAY) for i in range(4)]
    res = scan(history_repo, range_spec=f"{shas[0]}..{shas[2]}", window=1)
    scanned = {s for u in res["units"] for s in u["shas"]}
    assert scanned == {shas[1], shas[2]}
    assert res["window_applied"] is False
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_digestscan.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'rgit.digestscan'`.

- [ ] **Step 3: Implement `src/rgit/digestscan.py`**

```python
"""Deterministic history scan: cluster mainline commits into digestion units.

Pure planning — no LLM, no store writes. `scan()` walks the first-parent
lineage, pairs reverts into dead units, clusters the rest (a merge commit is
one unit; same-author streaks over overlapping files merge), pre-drops pure
infra units, detects deleted-from-HEAD work, and scores everything.
digestqueue persists the result; this module never touches the store.
"""
from __future__ import annotations
import datetime
import hashlib
import math
import re
from pathlib import Path
from typing import Optional

from .gitutil import (current_commit, head_files, is_shallow, mainline_commits,
                      mainline_count)

MODES = ("layered", "trunk", "dead", "archaeology")

DEFAULT_WINDOW = 400                 # first-parent commits scanned by default
STREAK_MAX_COMMITS = 10
STREAK_MAX_GAP_SECONDS = 48 * 3600
STREAK_MAX_CHURN = 4000              # changed lines: scan-time proxy for the
                                     # 300 KB diff cap (bytes are unknown here)
UNIT_MAX_DIFF_BYTES = 300_000        # staging-time oversized flag (digestqueue)

_REVERT_TRAILER = re.compile(r"This reverts commit ([0-9a-f]{7,40})", re.IGNORECASE)

_LOCKFILE_NAMES = {
    "uv.lock", "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock",
    "Pipfile.lock", "Cargo.lock", "go.sum", "composer.lock", "Gemfile.lock",
}
_INFRA_NAMES = {".gitignore", ".gitattributes", ".editorconfig", "LICENSE",
                "CODEOWNERS", ".pre-commit-config.yaml"}
_INFRA_PREFIXES = (".github/", ".gitlab/", "docs/")
_DOC_SUFFIXES = (".md", ".rst")


def _is_infra_path(path: str) -> bool:
    """Conservative noise classifier: only paths that are near-certainly not a
    feature. Ambiguity (pyproject, configs, code) stays with the segmenter."""
    name = path.rsplit("/", 1)[-1]
    if name in _LOCKFILE_NAMES or name in _INFRA_NAMES:
        return True
    if path.startswith(_INFRA_PREFIXES):
        return True
    return path.endswith(_DOC_SUFFIXES)


def unit_id(shas: list[str]) -> str:
    """Deterministic unit identity: hash of the commit-sha set. Rescans emit
    the same id for the same commits, so INSERT OR IGNORE makes scan idempotent."""
    h = hashlib.sha256("\n".join(sorted(shas)).encode("utf-8")).hexdigest()
    return f"dig_{h[:16]}"


def _iso_date(at: int) -> str:
    return datetime.datetime.fromtimestamp(
        at, tz=datetime.timezone.utc).date().isoformat()


def _pair_reverts(commits: list[dict]) -> tuple[dict, set]:
    """({original_sha: revert_meta}, consumed_shas).

    A revert whose original lies outside the scanned range stays a normal
    commit — its diff (the removal) is still real history.
    """
    shas = [c["sha"] for c in commits]
    dead: dict[str, dict] = {}
    consumed: set[str] = set()
    for c in commits:
        m = _REVERT_TRAILER.search(c["body"])
        if not m:
            continue
        prefix = m.group(1)
        target = next((s for s in shas if s.startswith(prefix)), None)
        if target is None or target in dead:
            continue
        dead[target] = {"reverted_by": c["sha"], "revert_at": c["at"],
                        "revert_date": _iso_date(c["at"]),
                        "revert_subject": c["subject"]}
        consumed.add(target)
        consumed.add(c["sha"])
    return dead, consumed


def _make_unit(commits: list[dict], *, merge: bool) -> dict:
    shas = [c["sha"] for c in commits]
    files = sorted({f for c in commits for f in c["files"]})
    churn = sum(c["churn"] for c in commits)
    meta = {
        "subjects": [c["subject"] for c in commits],
        "author": commits[-1]["author"],
        "start_at": commits[0]["at"], "end_at": commits[-1]["at"],
        "start_date": _iso_date(commits[0]["at"]),
        "end_date": _iso_date(commits[-1]["at"]),
        "files": files, "files_count": len(files),
        "code_files": sum(1 for f in files if not _is_infra_path(f)),
        "churn": churn, "merge": merge,
        "has_root": not commits[0]["parents"],
        "oversized": churn > STREAK_MAX_CHURN,
    }
    return {"id": unit_id(shas), "kind": "landed", "shas": shas, "score": 0.0,
            "status": "pending", "skip_reason": None, "meta": meta}


def _extends_streak(streak: list[dict], c: dict) -> bool:
    last = streak[-1]
    if c["author"] != last["author"]:
        return False
    # abs(): rebases and scripted fixtures make dates non-monotonic on the
    # first-parent chain; adjacency is what the 48h rule is really about.
    if abs(c["at"] - last["at"]) > STREAK_MAX_GAP_SECONDS:
        return False
    if len(streak) >= STREAK_MAX_COMMITS:
        return False
    if sum(x["churn"] for x in streak) + c["churn"] > STREAK_MAX_CHURN:
        return False
    streak_files = {f for x in streak for f in x["files"]}
    return bool(streak_files & set(c["files"]))


def _score(unit: dict, newest_at: int, oldest_at: int) -> float:
    m = unit["meta"]
    s = math.log2(1 + m["churn"])
    s += 2.0 * (m["code_files"] / max(1, m["files_count"]))
    if unit["kind"] == "dead":
        s += 3.0                     # the only knowledge otherwise lost
    if m["merge"]:
        s += 1.0                     # structurally "one feature" already
    span = max(1, newest_at - oldest_at)
    s += (m["end_at"] - oldest_at) / span          # recency, 0..1
    return round(s, 4)


def scan(repo: Path, *, range_spec: Optional[str] = None,
         window: int = DEFAULT_WINDOW, all_history: bool = False) -> dict:
    """Cluster the selected mainline slice into digestion-unit drafts."""
    total = mainline_count(repo)
    limit = None if (range_spec or all_history) else window
    commits = mainline_commits(repo, range_spec=range_spec, limit=limit)
    head_set = head_files(repo)
    dead_meta, consumed = _pair_reverts(commits)

    units: list[dict] = []
    streak: list[dict] = []

    def flush() -> None:
        if streak:
            units.append(_make_unit(list(streak), merge=False))
            streak.clear()

    for c in commits:
        sha = c["sha"]
        if sha in dead_meta:                       # reverted original
            flush()
            u = _make_unit([c], merge=False)
            u["kind"] = "dead"
            u["meta"]["dead"] = "reverted"
            u["meta"].update(dead_meta[sha])
            units.append(u)
            continue
        if sha in consumed:                        # the revert commit itself
            flush()                                # never let a streak span it
            continue
        if len(c["parents"]) > 1:                  # merge = one PR unit
            flush()
            units.append(_make_unit([c], merge=True))
            continue
        if streak and not _extends_streak(streak, c):
            flush()
        streak.append(c)
    flush()

    newest_at = max((c["at"] for c in commits), default=0)
    oldest_at = min((c["at"] for c in commits), default=0)
    for u in units:
        m = u["meta"]
        if u["kind"] == "landed" and m["files"] and \
                not any(f in head_set for f in m["files"]):
            u["kind"] = "dead"
            m["dead"] = "deleted"
        if m["files"] and all(_is_infra_path(f) for f in m["files"]):
            u["status"] = "skipped"
            u["skip_reason"] = "infra"
        u["score"] = _score(u, newest_at, oldest_at)
        m["files"] = m["files"][:50]               # cap AFTER detection above
    return {"units": units, "total_mainline": total,
            "window_applied": limit is not None and total > limit,
            "shallow": is_shallow(repo), "head": current_commit(repo)}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_digestscan.py -v`
Expected: PASS (9 tests).

- [ ] **Step 5: Run the full suite and commit**

Run: `python -m pytest -q` — expected: all pass.

```bash
git add src/rgit/digestscan.py tests/test_digestscan.py
git commit -m "feat(digestscan): cluster mainline history into scored digestion units"
```

---

### Task 3: Store plane — `Capsule.origin`, `DigestUnit`, migrations, queue CRUD

**Files:**
- Modify: `src/rgit/store/models.py`
- Modify: `src/rgit/store/db.py`
- Modify: `src/rgit/store/store.py`
- Modify: `tests/test_doctor.py:153` (positional features INSERT → named columns)
- Test: `tests/test_store.py` (append), `tests/test_db.py` (append)

**Interfaces:**
- Consumes: existing `Store`, `db.init_schema`, `Capsule`.
- Produces (used by Tasks 4, 6, 7):
  - `Capsule.origin: str = "live"` (new last dataclass field; flows through `to_dict`/`from_dict` automatically via `asdict`/`**d`).
  - `DigestUnit` dataclass in `models.py` (fields exactly as below).
  - `Store.delete_feature(fid: str) -> None` — deletes the capsule and every edge where it is src or dst; `KeyError` on unknown id.
  - `Store.add_digest_unit(unit: DigestUnit) -> bool` — INSERT OR IGNORE; True if newly inserted.
  - `Store.get_digest_unit(uid: str) -> DigestUnit` (KeyError), `Store.digest_unit_by_proposal(pid: str) -> Optional[DigestUnit]`, `Store.list_digest_units(status: Optional[str] = None) -> list[DigestUnit]` (ordered `score DESC, id ASC` — the queue order).
  - `Store.update_digest_unit(uid, *, status=None, skip_reason=None, proposal_id=None, capsule_ids=None) -> None` (only provided kwargs change; KeyError on unknown id), `Store.reset_digest_unit(uid) -> None` (status='pending', skip_reason/proposal_id/capsule_ids nulled).
  - `Store.set_digest_meta(key: str, value: str) -> None`, `Store.get_digest_meta(key: str) -> Optional[str]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_db.py`:

```python
def test_digest_tables_and_origin_column_exist(tmp_path):
    from rgit.store.db import connect, init_schema
    conn = connect(tmp_path / "g.db")
    init_schema(conn)
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"digest_units", "digest_meta"} <= tables
    fcols = {r[1] for r in conn.execute("PRAGMA table_info(features)")}
    assert "origin" in fcols


def test_origin_migration_adds_column_to_old_features(tmp_path):
    import sqlite3
    from rgit.store.db import init_schema
    conn = sqlite3.connect(tmp_path / "old.db")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE features (id TEXT PRIMARY KEY, name TEXT NOT NULL, "
                 "intent TEXT NOT NULL, status TEXT NOT NULL, base_commit TEXT NOT NULL, "
                 "knobs TEXT NOT NULL DEFAULT '{}', data_assumptions TEXT, "
                 "resurrection_guide TEXT, result_summary TEXT, payload_hash TEXT)")
    conn.execute("INSERT INTO features (id, name, intent, status, base_commit) "
                 "VALUES ('f1', 'n', 'i', 'approved', 'c')")
    conn.commit()
    init_schema(conn)
    row = conn.execute("SELECT origin FROM features WHERE id='f1'").fetchone()
    assert row["origin"] == "live"
```

Append to `tests/test_store.py`:

```python
def _capsule(name="cap", origin="live"):
    from rgit.store.models import Capsule, CodeSlice
    return Capsule(id="", name=name, intent="i", status="approved",
                   base_commit="c", knobs={}, data_assumptions=None,
                   resurrection_guide=None, result_summary=None, payload_hash=None,
                   code_slices=[CodeSlice("m.py", "f", None, "x", "wrap")],
                   origin=origin)


def test_capsule_origin_roundtrip(tmp_path):
    from rgit.store.store import Store
    store = Store(tmp_path)
    fid = store.add_feature(_capsule(origin="backfill"))
    assert store.get_feature(fid).origin == "backfill"
    live = store.add_feature(_capsule(name="live-one"))
    assert store.get_feature(live).origin == "live"


def test_delete_feature_cascades_edges(tmp_path):
    import pytest
    from rgit.store.store import Store
    store = Store(tmp_path)
    a = store.add_feature(_capsule(name="a"))
    b = store.add_feature(_capsule(name="b"))
    store.add_edge(a, b, "depends_on")
    store.add_edge(b, a, "overlaps")
    store.delete_feature(a)
    with pytest.raises(KeyError):
        store.get_feature(a)
    assert store.neighbors(b, "overlaps") == []
    rows = store.conn.execute("SELECT * FROM edges WHERE src=? OR dst=?",
                              (a, a)).fetchall()
    assert rows == []
    with pytest.raises(KeyError):
        store.delete_feature("feat_missing")


def test_digest_unit_crud_and_queue_order(tmp_path):
    import pytest
    from rgit.store.models import DigestUnit
    from rgit.store.store import Store
    store = Store(tmp_path)
    low = DigestUnit(id="dig_low", kind="landed", shas=["s1"], score=1.0,
                     meta={"subjects": ["low"]}, created_at="t")
    high = DigestUnit(id="dig_high", kind="dead", shas=["s2", "s3"], score=9.0,
                      meta={"subjects": ["high"]}, created_at="t")
    assert store.add_digest_unit(low) is True
    assert store.add_digest_unit(high) is True
    assert store.add_digest_unit(high) is False              # idempotent rescan
    units = store.list_digest_units()
    assert [u.id for u in units] == ["dig_high", "dig_low"]  # score DESC
    assert units[0].shas == ["s2", "s3"]
    assert units[0].meta == {"subjects": ["high"]}

    store.update_digest_unit("dig_high", status="staged", proposal_id="prop_1")
    assert store.get_digest_unit("dig_high").proposal_id == "prop_1"
    assert store.digest_unit_by_proposal("prop_1").id == "dig_high"
    assert store.digest_unit_by_proposal("prop_none") is None
    assert [u.id for u in store.list_digest_units("pending")] == ["dig_low"]

    store.update_digest_unit("dig_high", status="done",
                             capsule_ids=["feat_1", "feat_2"])
    assert store.get_digest_unit("dig_high").capsule_ids == ["feat_1", "feat_2"]

    store.reset_digest_unit("dig_high")
    fresh = store.get_digest_unit("dig_high")
    assert fresh.status == "pending"
    assert fresh.proposal_id is None and fresh.capsule_ids == []

    with pytest.raises(KeyError):
        store.get_digest_unit("dig_missing")
    with pytest.raises(KeyError):
        store.update_digest_unit("dig_missing", status="done")


def test_digest_meta_upsert(tmp_path):
    from rgit.store.store import Store
    store = Store(tmp_path)
    assert store.get_digest_meta("mode") is None
    store.set_digest_meta("mode", "layered")
    store.set_digest_meta("mode", "dead")
    assert store.get_digest_meta("mode") == "dead"
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_db.py tests/test_store.py -v`
Expected: new tests FAIL (`no such table: digest_units`, `TypeError: ... unexpected keyword argument 'origin'`, missing methods).

- [ ] **Step 3: Implement — `models.py`**

In `src/rgit/store/models.py`, add `origin` as the last `Capsule` field (after `code_slices`):

```python
    code_slices: list[CodeSlice] = field(default_factory=list)
    origin: str = "live"                # "live" | "backfill" (history digestion)
```

Add after the `Proposal` dataclass (and note in `Proposal.trigger`'s comment that `"backfill"` is now a legal value — change the comment on line 108 to `# "run" | "commit" | "manual" | "watch" | "backfill"`):

```python
@dataclass
class DigestUnit:
    """One clustered slice of mainline history awaiting digestion."""
    id: str                              # digestscan.unit_id(shas) — idempotence key
    kind: str                            # "landed" | "dead"
    shas: list[str]                      # oldest -> newest
    score: float
    status: str = "pending"              # "pending" | "staged" | "done" | "skipped"
    skip_reason: Optional[str] = None    # "infra" | "empty" | "user" | "error"
    proposal_id: Optional[str] = None
    capsule_ids: list[str] = field(default_factory=list)
    meta: dict = field(default_factory=dict)
    created_at: str = ""
```

- [ ] **Step 4: Implement — `db.py`**

In `SCHEMA`, change the `features` table to include the column (new databases), and append the two digest tables before `schema_metadata`:

```sql
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
    payload_hash TEXT,
    origin TEXT NOT NULL DEFAULT 'live'
);
```

```sql
CREATE TABLE IF NOT EXISTS digest_units (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    shas TEXT NOT NULL,
    score REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    skip_reason TEXT,
    proposal_id TEXT,
    capsule_ids TEXT,
    meta TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS digest_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
```

In `init_schema`, add the features migration alongside the existing PRAGMA checks (new tables need no migration — `executescript(SCHEMA)` creates them):

```python
    fcols = {r[1] for r in conn.execute("PRAGMA table_info(features)")}
    if "origin" not in fcols:
        conn.execute(
            "ALTER TABLE features ADD COLUMN origin TEXT NOT NULL DEFAULT 'live'")
```

- [ ] **Step 5: Implement — `store.py`**

Change `add_feature`'s INSERT to **named columns** (the positional form breaks the moment a column is added — this is the migration-safety fix):

```python
        self.conn.execute(
            "INSERT INTO features (id, name, intent, status, base_commit, knobs, "
            "data_assumptions, resurrection_guide, result_summary, payload_hash, "
            "origin) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (fid, cap.name, cap.intent, cap.status, cap.base_commit,
             json.dumps(cap.knobs), cap.data_assumptions, cap.resurrection_guide,
             rs, payload_hash, cap.origin))
```

In `_row_to_capsule`, add `origin=row["origin"],` alongside `payload_hash=row["payload_hash"]`.

Update the models import line to `from .models import Capsule, DigestUnit, Run, Proposal, Event`. Then append after the events section:

```python
    def delete_feature(self, fid: str) -> None:
        """Remove a capsule and every edge that references it (either side).

        The digest `clear` path: origin=backfill capsules are bulk-removable,
        so deletion must not strand dangling edges in the graph.
        """
        cur = self.conn.execute("DELETE FROM features WHERE id=?", (fid,))
        if cur.rowcount == 0:
            raise KeyError(fid)
        self.conn.execute("DELETE FROM edges WHERE src=? OR dst=?", (fid, fid))
        self.conn.commit()

    # ---- digest queue --------------------------------------------------
    def add_digest_unit(self, unit: DigestUnit) -> bool:
        """INSERT OR IGNORE (unit ids are deterministic sha-set hashes, so a
        rescan is naturally idempotent). True when the row is new."""
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO digest_units (id, kind, shas, score, status, "
            "skip_reason, proposal_id, capsule_ids, meta, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (unit.id, unit.kind, json.dumps(unit.shas), unit.score, unit.status,
             unit.skip_reason, unit.proposal_id, json.dumps(unit.capsule_ids),
             json.dumps(unit.meta), unit.created_at))
        self.conn.commit()
        return cur.rowcount > 0

    def _row_to_digest_unit(self, row) -> DigestUnit:
        return DigestUnit(
            id=row["id"], kind=row["kind"], shas=json.loads(row["shas"]),
            score=row["score"], status=row["status"],
            skip_reason=row["skip_reason"], proposal_id=row["proposal_id"],
            capsule_ids=json.loads(row["capsule_ids"]) if row["capsule_ids"] else [],
            meta=json.loads(row["meta"]), created_at=row["created_at"])

    def get_digest_unit(self, uid: str) -> DigestUnit:
        row = self.conn.execute("SELECT * FROM digest_units WHERE id=?",
                                (uid,)).fetchone()
        if row is None:
            raise KeyError(uid)
        return self._row_to_digest_unit(row)

    def digest_unit_by_proposal(self, pid: str) -> Optional[DigestUnit]:
        row = self.conn.execute(
            "SELECT * FROM digest_units WHERE proposal_id=? ORDER BY id LIMIT 1",
            (pid,)).fetchone()
        return self._row_to_digest_unit(row) if row else None

    def list_digest_units(self, status: Optional[str] = None) -> list[DigestUnit]:
        """Score-descending — the listing order IS the queue order."""
        if status:
            rows = self.conn.execute(
                "SELECT * FROM digest_units WHERE status=? "
                "ORDER BY score DESC, id ASC", (status,)).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM digest_units ORDER BY score DESC, id ASC").fetchall()
        return [self._row_to_digest_unit(r) for r in rows]

    def update_digest_unit(self, uid: str, *, status: Optional[str] = None,
                           skip_reason: Optional[str] = None,
                           proposal_id: Optional[str] = None,
                           capsule_ids: Optional[list[str]] = None) -> None:
        """Update only the provided fields (None = leave untouched; use
        reset_digest_unit to null columns)."""
        sets, vals = [], []
        if status is not None:
            sets.append("status=?"); vals.append(status)
        if skip_reason is not None:
            sets.append("skip_reason=?"); vals.append(skip_reason)
        if proposal_id is not None:
            sets.append("proposal_id=?"); vals.append(proposal_id)
        if capsule_ids is not None:
            sets.append("capsule_ids=?"); vals.append(json.dumps(capsule_ids))
        if not sets:
            return
        vals.append(uid)
        cur = self.conn.execute(
            f"UPDATE digest_units SET {', '.join(sets)} WHERE id=?", vals)
        if cur.rowcount == 0:
            raise KeyError(uid)
        self.conn.commit()

    def reset_digest_unit(self, uid: str) -> None:
        """Back to pending with staging/outcome columns nulled (digest clear)."""
        cur = self.conn.execute(
            "UPDATE digest_units SET status='pending', skip_reason=NULL, "
            "proposal_id=NULL, capsule_ids=NULL WHERE id=?", (uid,))
        if cur.rowcount == 0:
            raise KeyError(uid)
        self.conn.commit()

    def set_digest_meta(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO digest_meta VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
        self.conn.commit()

    def get_digest_meta(self, key: str) -> Optional[str]:
        row = self.conn.execute("SELECT value FROM digest_meta WHERE key=?",
                                (key,)).fetchone()
        return row["value"] if row else None
```

- [ ] **Step 6: Fix the raw positional INSERT in `tests/test_doctor.py:153`**

Replace the statement using `"INSERT INTO features VALUES (?,?,?,?,?,?,?,?,?,?)"` with the named-column form (values tuple unchanged — origin takes its `'live'` default):

```python
        "INSERT INTO features (id, name, intent, status, base_commit, knobs, "
        "data_assumptions, resurrection_guide, result_summary, payload_hash) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `python -m pytest tests/test_db.py tests/test_store.py tests/test_doctor.py tests/test_models.py -v`
Expected: PASS. If `tests/test_models.py` asserts an exact `to_dict()` key set, add `"origin": "live"` to its expected dict — the roundtrip itself needs no change.

- [ ] **Step 8: Run the full suite and commit**

Run: `python -m pytest -q` — expected: all pass.

```bash
git add src/rgit/store/models.py src/rgit/store/db.py src/rgit/store/store.py tests/test_db.py tests/test_store.py tests/test_doctor.py
git commit -m "feat(store): capsule origin + digest_units queue tables"
```

---

### Task 4: `digestqueue.py` — persist, stage, accept, skip, clear, status

**Files:**
- Create: `src/rgit/digestqueue.py`
- Modify: `src/rgit/curation.py` (thread `origin` through `_capsule_from_candidate`)
- Test: `tests/test_digestqueue.py` (new)

**Interfaces:**
- Consumes: Task 2 `digestscan.scan/MODES/DEFAULT_WINDOW/UNIT_MAX_DIFF_BYTES`, Task 3 store methods + `DigestUnit`, Task 1 `EmptyTreeRangeDiffSource`, existing `CommitDiffSource`, `RangeDiffSource`, `segment_diff`, `curation._capsule_from_candidate`.
- Produces (used by Tasks 6, 8, 10):
  - `BATCH_DEFAULT = 10`
  - `scan_into_store(store, *, range_spec=None, mode=None, window=None, all_history=False, now="") -> dict` — persists units + meta; returns `{"mode", "units_new", "units_total", "pending", "batches_remaining", "total_mainline", "window_applied", "shallow", "head_at_scan"}`. Raises `ValueError` on unknown mode.
  - `pending_units(store) -> list[DigestUnit]` — pending, mode-filtered (`trunk`→landed, `dead`→dead, else all), score order.
  - `next_batch(store, *, batch=BATCH_DEFAULT, segmenter=None, now="") -> list[dict]` — reconciles staged units, stages pending ones; items `{"unit_id", "kind", "score", "proposal_id", "meta", "diff", "candidates", "oversized"}`.
  - `accept(store, proposal_id, now="") -> dict` — `{"unit_id", "capsules": [[name, fid], ...]}` or `{"unit_id", "capsules": [], "skipped": "infra"}`; KeyError (no unit for proposal), ValueError (proposal not open).
  - `skip_unit(store, unit_id) -> None`; `clear(store) -> dict` (`{"capsules_removed", "units_reset"}`); `status(store) -> dict` (`{"mode", "range", "head_at_scan", "units_total", "by_status", "pending_in_mode", "dead_pending", "batches_remaining"}`).
- Modified: `curation._capsule_from_candidate(store, prop, idx, base, origin="live")` — new keyword-only-style trailing param with default; `approve()`/`decide()` call sites unchanged (default applies).

- [ ] **Step 1: Add the `origin` parameter in `src/rgit/curation.py`**

Change the signature and the `Capsule(...)` construction:

```python
def _capsule_from_candidate(store: Store, prop, idx: int, base: str,
                            origin: str = "live") -> str:
```

and inside it add `origin=origin` to the `Capsule(...)` call (after `code_slices=...`). `approve()` and `decide()` stay as-is — the default keeps live capture behavior identical.

- [ ] **Step 2: Write the failing tests**

Create `tests/test_digestqueue.py`:

```python
import pytest
from conftest import commit_file, make_candidate, revert_head
from rgit import digestqueue
from rgit.segmenter import HeuristicSegmenter
from rgit.store.store import Store

T0 = 1_700_000_000
DAY = 86_400
NOW = "2026-07-05T00:00:00"


def _scripted_store(history_repo):
    """landed feature commit + reverted experiment + docs-only commit."""
    commit_file(history_repo, "model.py", "def f(x):\n    return x\n",
                "feat: base model", when=T0)
    commit_file(history_repo, "exp.py", "def trick(x):\n    return x + 1\n",
                "exp: additive trick", when=T0 + 5 * DAY, author="u2")
    revert_head(history_repo, when=T0 + 6 * DAY, author="u2")
    commit_file(history_repo, "README.md", "# hi\n", "docs", when=T0 + 10 * DAY)
    store = Store.init(history_repo)
    return store


def test_scan_into_store_persists_and_is_idempotent(history_repo):
    store = _scripted_store(history_repo)
    res = digestqueue.scan_into_store(store, now=NOW)
    assert res["mode"] == "layered"
    assert res["units_new"] == res["units_total"] >= 3
    assert store.get_digest_meta("mode") == "layered"
    assert store.get_digest_meta("head_at_scan")
    again = digestqueue.scan_into_store(store, now=NOW)
    assert again["units_new"] == 0                       # INSERT OR IGNORE
    with pytest.raises(ValueError):
        digestqueue.scan_into_store(store, mode="bogus", now=NOW)


def test_mode_filters_pending(history_repo):
    store = _scripted_store(history_repo)
    digestqueue.scan_into_store(store, mode="dead", now=NOW)
    pend = digestqueue.pending_units(store)
    assert pend and all(u.kind == "dead" for u in pend)
    store.set_digest_meta("mode", "trunk")
    assert all(u.kind == "landed" for u in digestqueue.pending_units(store))


def test_next_batch_stages_proposals_with_backfill_trigger(history_repo):
    store = _scripted_store(history_repo)
    digestqueue.scan_into_store(store, now=NOW)
    items = digestqueue.next_batch(store, segmenter=HeuristicSegmenter(), now=NOW)
    assert items
    first = items[0]
    prop = store.get_proposal(first["proposal_id"])
    assert prop.trigger == "backfill"
    assert prop.source_commit                             # pinned to history
    assert "diff --git" in first["diff"]
    unit = store.get_digest_unit(first["unit_id"])
    assert unit.status == "staged"
    # a second call re-emits still-open staged items instead of restaging
    again = digestqueue.next_batch(store, segmenter=HeuristicSegmenter(), now=NOW)
    assert {i["proposal_id"] for i in items} >= {i["proposal_id"] for i in again[:len(items)]}


def test_accept_ingests_all_candidates_as_backfill(history_repo):
    store = _scripted_store(history_repo)
    digestqueue.scan_into_store(store, now=NOW)
    items = digestqueue.next_batch(store, segmenter=HeuristicSegmenter(), now=NOW)
    dead_item = next(i for i in items if i["kind"] == "dead")
    store.set_proposal_candidates(dead_item["proposal_id"],
                                  [make_candidate("dead-trick", file="exp.py",
                                                  symbol="trick")])
    res = digestqueue.accept(store, dead_item["proposal_id"], now=NOW)
    (name, fid), = res["capsules"]
    assert name == "dead-trick"
    cap = store.get_feature(fid)
    assert cap.origin == "backfill"
    assert cap.status == "approved"
    assert cap.base_commit == store.get_proposal(dead_item["proposal_id"]).source_commit
    assert "reverted by" in cap.result_summary.notes      # engine-written outcome
    assert store.get_proposal(dead_item["proposal_id"]).status == "resolved"
    unit = store.get_digest_unit(dead_item["unit_id"])
    assert unit.status == "done" and unit.capsule_ids == [fid]
    with pytest.raises(ValueError):
        digestqueue.accept(store, dead_item["proposal_id"], now=NOW)  # not open
    with pytest.raises(KeyError):
        digestqueue.accept(store, "prop_unknown", now=NOW)


def test_accept_zero_candidates_resolves_as_infra(history_repo):
    store = _scripted_store(history_repo)
    digestqueue.scan_into_store(store, now=NOW)
    items = digestqueue.next_batch(store, segmenter=HeuristicSegmenter(), now=NOW)
    item = items[0]
    store.set_proposal_candidates(item["proposal_id"], [])
    res = digestqueue.accept(store, item["proposal_id"], now=NOW)
    assert res["capsules"] == [] and res["skipped"] == "infra"
    assert store.get_digest_unit(item["unit_id"]).skip_reason == "infra"
    assert store.get_proposal(item["proposal_id"]).status == "dismissed"


def test_reconcile_externally_resolved_and_dismissed(history_repo):
    store = _scripted_store(history_repo)
    digestqueue.scan_into_store(store, now=NOW)
    items = digestqueue.next_batch(store, segmenter=HeuristicSegmenter(), now=NOW)
    a, b = items[0], items[1]
    store.set_proposal_status(a["proposal_id"], "resolved")   # someone else resolved it
    store.set_proposal_status(b["proposal_id"], "dismissed")
    digestqueue.next_batch(store, segmenter=HeuristicSegmenter(), now=NOW)
    assert store.get_digest_unit(a["unit_id"]).status == "done"
    got_b = store.get_digest_unit(b["unit_id"])
    assert got_b.status == "skipped" and got_b.skip_reason == "user"


def test_skip_unit_dismisses_open_proposal(history_repo):
    store = _scripted_store(history_repo)
    digestqueue.scan_into_store(store, now=NOW)
    items = digestqueue.next_batch(store, segmenter=HeuristicSegmenter(), now=NOW)
    item = items[0]
    digestqueue.skip_unit(store, item["unit_id"])
    unit = store.get_digest_unit(item["unit_id"])
    assert unit.status == "skipped" and unit.skip_reason == "user"
    assert store.get_proposal(item["proposal_id"]).status == "dismissed"


def test_clear_removes_backfill_capsules_and_resets_units(history_repo):
    store = _scripted_store(history_repo)
    digestqueue.scan_into_store(store, now=NOW)
    items = digestqueue.next_batch(store, segmenter=HeuristicSegmenter(), now=NOW)
    item = items[0]
    store.set_proposal_candidates(item["proposal_id"], [make_candidate("bf")])
    digestqueue.accept(store, item["proposal_id"], now=NOW)
    from rgit.store.models import Capsule, CodeSlice
    hand = store.add_feature(Capsule(
        id="", name="hand-made", intent="i", status="approved", base_commit="c",
        knobs={}, data_assumptions=None, resurrection_guide=None,
        result_summary=None, payload_hash=None,
        code_slices=[CodeSlice("m.py", "f", None, "x", "wrap")]))
    res = digestqueue.clear(store)
    assert res["capsules_removed"] == 1
    assert res["units_reset"] >= 1
    assert store.get_feature(hand).name == "hand-made"     # live capsule untouched
    assert store.get_digest_unit(item["unit_id"]).status == "pending"


def test_status_reports_progress(history_repo):
    store = _scripted_store(history_repo)
    digestqueue.scan_into_store(store, now=NOW)
    st = digestqueue.status(store)
    assert st["mode"] == "layered"
    assert st["units_total"] >= 3
    assert st["pending_in_mode"] >= 1
    assert st["dead_pending"] >= 1
    assert st["batches_remaining"] >= 1
    assert st["by_status"]["pending"] >= 1
```

- [ ] **Step 3: Run to verify failure**

Run: `python -m pytest tests/test_digestqueue.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'rgit.digestqueue'`.

- [ ] **Step 4: Implement `src/rgit/digestqueue.py`**

```python
"""The backfill queue: persist scan results, stage units as proposals, ingest.

The lifecycle is pending -> staged -> done|skipped, all recorded in the
digest_units table so any session can resume where the last one stopped.
Staging reuses the live-capture pipeline (DiffSource -> segment_diff ->
Proposal, trigger="backfill"); `accept` is the non-interactive counterpart of
review: every candidate becomes an approved origin=backfill capsule, and dead
units get their outcome written from git facts, never from an agent.
"""
from __future__ import annotations
import math
from typing import Optional

from . import digestscan
from .curation import _capsule_from_candidate
from .gitutil import (CommitDiffSource, EmptyTreeRangeDiffSource,
                      RangeDiffSource)
from .segmenter import Segmenter, segment_diff
from .store.models import DigestUnit, ResultSummary
from .store.store import Store

BATCH_DEFAULT = 10


def scan_into_store(store: Store, *, range_spec: Optional[str] = None,
                    mode: Optional[str] = None, window: Optional[int] = None,
                    all_history: bool = False, now: str = "") -> dict:
    """Run the deterministic scan and persist it. Idempotent and incremental:
    unit ids are sha-set hashes, so re-running only adds unseen units."""
    mode = mode or store.get_digest_meta("mode") or "layered"
    if mode not in digestscan.MODES:
        raise ValueError(f"unknown digest mode {mode!r}; "
                         f"expected one of {', '.join(digestscan.MODES)}")
    res = digestscan.scan(store.root, range_spec=range_spec,
                          window=window if window is not None
                          else digestscan.DEFAULT_WINDOW,
                          all_history=all_history)
    new = 0
    for u in res["units"]:
        unit = DigestUnit(id=u["id"], kind=u["kind"], shas=u["shas"],
                          score=u["score"], status=u["status"],
                          skip_reason=u["skip_reason"], meta=u["meta"],
                          created_at=now)
        if store.add_digest_unit(unit):
            new += 1
    store.set_digest_meta("mode", mode)
    store.set_digest_meta("head_at_scan", res["head"])
    if range_spec:
        store.set_digest_meta("range", range_spec)
    pending = pending_units(store)
    return {"mode": mode, "units_new": new,
            "units_total": len(store.list_digest_units()),
            "pending": len(pending),
            "batches_remaining": math.ceil(len(pending) / BATCH_DEFAULT),
            "total_mainline": res["total_mainline"],
            "window_applied": res["window_applied"],
            "shallow": res["shallow"], "head_at_scan": res["head"]}


def pending_units(store: Store) -> list[DigestUnit]:
    """Pending units the current mode wants, in queue (score) order."""
    mode = store.get_digest_meta("mode") or "layered"
    units = store.list_digest_units("pending")
    if mode == "trunk":
        return [u for u in units if u.kind == "landed"]
    if mode == "dead":
        return [u for u in units if u.kind == "dead"]
    return units                     # layered / archaeology: everything ranked


def _source_for(unit: DigestUnit):
    shas = unit.shas
    if unit.meta.get("merge"):
        return RangeDiffSource(f"{shas[-1]}^1..{shas[-1]}")
    if len(shas) == 1:
        return CommitDiffSource(shas[0])       # --root handles a root commit
    if unit.meta.get("has_root"):
        return EmptyTreeRangeDiffSource(shas[-1])
    return RangeDiffSource(f"{shas[0]}^..{shas[-1]}")


def _staged_item(store: Store, unit: DigestUnit, prop) -> dict:
    diff = store.objects.get(prop.diff_ref).decode(errors="replace")
    oversized = bool(unit.meta.get("oversized")) or \
        len(diff.encode("utf-8", errors="replace")) > digestscan.UNIT_MAX_DIFF_BYTES
    return {"unit_id": unit.id, "kind": unit.kind, "score": unit.score,
            "proposal_id": prop.id, "meta": unit.meta, "diff": diff,
            "candidates": prop.candidates, "oversized": oversized}


def next_batch(store: Store, *, batch: int = BATCH_DEFAULT,
               segmenter: Optional[Segmenter] = None, now: str = "") -> list[dict]:
    """Reconcile staged work, then stage the next highest-ranked units.

    Crash-safe by construction: staged units whose proposal is still open are
    re-emitted (never re-staged); ones resolved or dismissed through another
    path are reconciled to done / skipped=user instead of duplicating work.
    """
    if segmenter is None:
        from .segmenter import HeuristicSegmenter
        segmenter = HeuristicSegmenter()
    out: list[dict] = []
    for unit in store.list_digest_units("staged"):
        prop = store.get_proposal(unit.proposal_id)
        if prop.status == "resolved":
            store.update_digest_unit(unit.id, status="done")
        elif prop.status == "dismissed":
            store.update_digest_unit(unit.id, status="skipped", skip_reason="user")
        elif len(out) < batch:
            out.append(_staged_item(store, unit, prop))
    for unit in pending_units(store):
        if len(out) >= batch:
            break
        pid = segment_diff(store, "backfill", segmenter, run_id=None, now=now,
                           source=_source_for(unit))
        if pid is None:
            store.update_digest_unit(unit.id, status="skipped",
                                     skip_reason="empty")
            continue
        store.update_digest_unit(unit.id, status="staged", proposal_id=str(pid))
        out.append(_staged_item(store, unit, store.get_proposal(str(pid))))
    return out


def _dead_outcome(unit: DigestUnit) -> ResultSummary:
    """Outcome facts come from git, never from the agent."""
    m = unit.meta
    if m.get("reverted_by"):
        when = m.get("revert_date", "")
        notes = f"reverted by {m['reverted_by'][:12]}"
        if when:
            notes += f" on {when}"
        return ResultSummary(verdict=None, key_delta=None,
                             failure_reason=m.get("revert_subject"), notes=notes)
    return ResultSummary(verdict=None, key_delta=None, failure_reason=None,
                         notes="files deleted from HEAD")


def accept(store: Store, proposal_id: str, now: str = "") -> dict:
    """Non-interactive ingestion: every candidate -> approved backfill capsule."""
    unit = store.digest_unit_by_proposal(proposal_id)
    if unit is None:
        raise KeyError(f"no digest unit staged for proposal {proposal_id!r}")
    prop = store.get_proposal(proposal_id)
    if prop.status != "open":
        raise ValueError(
            f"proposal {proposal_id!r} is {prop.status}, not open; cannot accept")
    if not prop.candidates:
        store.set_proposal_status(proposal_id, "dismissed")
        store.update_digest_unit(unit.id, status="skipped", skip_reason="infra")
        return {"unit_id": unit.id, "capsules": [], "skipped": "infra"}
    base = prop.source_commit or unit.shas[-1]
    capsules: list[list[str]] = []
    for idx, cand in enumerate(prop.candidates):
        fid = _capsule_from_candidate(store, prop, idx, base, origin="backfill")
        if unit.kind == "dead":
            store.update_capsule(fid, result_summary=_dead_outcome(unit))
        capsules.append([cand["name"], fid])
    store.set_proposal_status(proposal_id, "resolved")
    store.update_digest_unit(unit.id, status="done",
                             capsule_ids=[fid for _, fid in capsules])
    return {"unit_id": unit.id, "capsules": capsules}


def skip_unit(store: Store, unit_id: str) -> None:
    unit = store.get_digest_unit(unit_id)
    if unit.proposal_id:
        prop = store.get_proposal(unit.proposal_id)
        if prop.status == "open":
            store.set_proposal_status(unit.proposal_id, "dismissed")
    store.update_digest_unit(unit_id, status="skipped", skip_reason="user")


def clear(store: Store) -> dict:
    """The regret channel: delete every backfill capsule (edges cascade) and
    put digested/staged units back in the queue. Hand-made capsules and
    deliberate skips (infra/user) are untouched."""
    removed = 0
    for cap in store.list_features():
        if cap.origin == "backfill":
            store.delete_feature(cap.id)
            removed += 1
    reset = 0
    for unit in store.list_digest_units():
        if unit.status == "staged" and unit.proposal_id:
            prop = store.get_proposal(unit.proposal_id)
            if prop.status == "open":
                store.set_proposal_status(unit.proposal_id, "dismissed")
        if unit.status in ("done", "staged"):
            store.reset_digest_unit(unit.id)
            reset += 1
    return {"capsules_removed": removed, "units_reset": reset}


def status(store: Store) -> dict:
    units = store.list_digest_units()
    by_status: dict[str, int] = {}
    for u in units:
        by_status[u.status] = by_status.get(u.status, 0) + 1
    pending = pending_units(store)
    return {"mode": store.get_digest_meta("mode"),
            "range": store.get_digest_meta("range"),
            "head_at_scan": store.get_digest_meta("head_at_scan"),
            "units_total": len(units), "by_status": by_status,
            "pending_in_mode": len(pending),
            "dead_pending": sum(1 for u in pending if u.kind == "dead"),
            "batches_remaining": math.ceil(len(pending) / BATCH_DEFAULT)}
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_digestqueue.py tests/test_curation.py -v`
Expected: PASS (curation suite proves the `origin` default changed nothing).

- [ ] **Step 6: Run the full suite and commit**

Run: `python -m pytest -q` — expected: all pass.

```bash
git add src/rgit/digestqueue.py src/rgit/curation.py tests/test_digestqueue.py
git commit -m "feat(digestqueue): stage, accept, skip, clear, status for the backfill queue"
```

---

### Task 5: edges `--scope` / `--limit` — incremental candidates with a judge quota

**Files:**
- Modify: `src/rgit/edges.py`
- Modify: `src/rgit/cli.py` (edges parser + handler)
- Test: `tests/test_edges.py` (append), `tests/test_cli.py` (append)

**Interfaces:**
- Consumes: existing `overlap_pairs`, `apply_overlaps`, `depends_candidates`, `Store.resolve_feature`.
- Produces (used by the skill in Task 9):
  - `overlap_pairs(store, scope: Optional[set[str]] = None)` — pairs where at least one side is in `scope` (all pairs when None).
  - `apply_overlaps(store, scope: Optional[set[str]] = None) -> int`
  - `depends_candidates(store, scope: Optional[set[str]] = None, limit: Optional[int] = None)` — scope-filtered; when `limit` is given, sorted by `(-len(evidence), src, dst)` and capped. Without `limit`, existing order/behavior unchanged.
  - CLI: `rgit edges --apply|--candidates [--scope ID[,ID...]] [--limit N]` (scope tokens accept capsule names or ids).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_edges.py` (mirror the file's existing store-construction pattern — it builds capsules via `store.add_feature`; reuse its local helper if one exists, else this standalone one):

```python
def _cap(store, name, symbol, code):
    from rgit.store.models import Capsule, CodeSlice
    return store.add_feature(Capsule(
        id="", name=name, intent=f"intent {name}", status="approved",
        base_commit="c", knobs={}, data_assumptions=None, resurrection_guide=None,
        result_summary=None, payload_hash=None,
        code_slices=[CodeSlice("m.py", symbol, None, code, "wrap")]))


def test_overlap_pairs_scope_filters_to_new_capsules(tmp_path):
    from rgit.edges import overlap_pairs
    from rgit.store.store import Store
    store = Store(tmp_path)
    a = _cap(store, "a", "shared", "x = 1")
    b = _cap(store, "b", "shared", "x = 2")
    c = _cap(store, "c", "shared", "x = 3")
    assert len(overlap_pairs(store)) == 3
    scoped = overlap_pairs(store, scope={c})
    assert len(scoped) == 2
    assert all(c in pair for pair in scoped)


def test_depends_candidates_limit_ranks_by_evidence(tmp_path):
    from rgit.edges import depends_candidates
    from rgit.store.store import Store
    store = Store(tmp_path)
    _cap(store, "def-one", "helper_one", "def helper_one():\n    pass")
    _cap(store, "def-two", "helper_two", "def helper_two():\n    pass")
    _cap(store, "user-weak", "weak", "helper_one()")
    _cap(store, "user-strong", "strong", "helper_one()\nhelper_two()")
    all_cands = depends_candidates(store)
    assert len(all_cands) >= 3
    top = depends_candidates(store, limit=1)
    assert len(top) == 1
    strong = store.resolve_feature("user-strong")
    # user-strong -> def-* carries 1 shared name each; ties break deterministically,
    # so just assert the cap + determinism:
    assert depends_candidates(store, limit=1) == top
    scoped = depends_candidates(store, scope={strong})
    assert scoped and all(strong in (c["src"], c["dst"]) for c in scoped)
```

Append to `tests/test_cli.py`:

```python
def test_edges_apply_scope_and_limit(git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    cli.main(["init"])
    store = Store.open(git_repo)
    a = store.add_feature(Capsule(
        id="", name="edge-a", intent="i", status="approved", base_commit="c",
        knobs={}, data_assumptions=None, resurrection_guide=None,
        result_summary=None, payload_hash=None,
        code_slices=[CodeSlice("m.py", "s", None, "x", "wrap")]))
    store.add_feature(Capsule(
        id="", name="edge-b", intent="i", status="approved", base_commit="c",
        knobs={}, data_assumptions=None, resurrection_guide=None,
        result_summary=None, payload_hash=None,
        code_slices=[CodeSlice("m.py", "s", None, "y", "wrap")]))
    assert cli.main(["edges", "--apply", "--scope", "edge-a", "--limit", "5"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["overlaps_written"] == 1
    assert cli.main(["edges", "--apply", "--scope", "no-such-capsule"]) == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_edges.py tests/test_cli.py::test_edges_apply_scope_and_limit -v`
Expected: new tests FAIL (`TypeError: ... unexpected keyword argument 'scope'`, CLI `unrecognized arguments: --scope`).

- [ ] **Step 3: Implement — `src/rgit/edges.py`**

```python
def overlap_pairs(store: Store,
                  scope: Optional[set] = None) -> list[tuple[str, str]]:
    """Unordered capsule pairs sharing a (file, top-level symbol). Deterministic.
    `scope` keeps only pairs touching at least one of the given capsule ids —
    the incremental path after a digest batch (new x graph, never old x old)."""
    caps = _approved(store)
    keys = {c.id: {(s.file, _top_symbol(s.symbol)) for s in c.code_slices if s.symbol}
            for c in caps}
    pairs = []
    for i in range(len(caps)):
        for j in range(i + 1, len(caps)):
            if scope is not None and caps[i].id not in scope \
                    and caps[j].id not in scope:
                continue
            if keys[caps[i].id] & keys[caps[j].id]:
                pairs.append((caps[i].id, caps[j].id))
    return pairs


def apply_overlaps(store: Store, scope: Optional[set] = None) -> int:
    """Write overlaps for each same-region pair, symmetric. Idempotent. Returns
    the number of overlapping pairs."""
    pairs = overlap_pairs(store, scope)
    for a, b in pairs:
        store.add_edge(a, b, "overlaps")
        store.add_edge(b, a, "overlaps")
    return len(pairs)


def depends_candidates(store: Store, scope: Optional[set] = None,
                       limit: Optional[int] = None) -> list[dict]:
    """Emit depends_on CANDIDATES (writes nothing). X is a candidate to depend_on
    Y when a name used in X's slice code intersects the symbols Y defines. Skips
    pairs that already carry a depends_on edge. `scope` filters to pairs touching
    the given ids; `limit` is the edge-judge quota — strongest evidence first
    (shared-identifier count), deterministic tie-break, the rest stay unjudged."""
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
            if scope is not None and x.id not in scope and y.id not in scope:
                continue
            shared = uses[x.id] & defines[y.id]
            if shared:
                out.append({"src": x.id, "dst": y.id, "evidence": sorted(shared)})
    if limit is not None:
        out.sort(key=lambda c: (-len(c["evidence"]), c["src"], c["dst"]))
        out = out[:limit]
    return out
```

(Also add `from typing import Optional` to the imports.)

- [ ] **Step 4: Implement — `src/rgit/cli.py` edges parser + handler**

In `build_parser()`, extend the edges subparser:

```python
    p_edges.add_argument("--scope", action="append", default=None,
                         metavar="ID[,ID...]",
                         help="restrict --apply/--candidates to pairs touching "
                              "these capsules (ids or names); the incremental "
                              "path after a digest batch")
    p_edges.add_argument("--limit", type=int, default=None,
                         help="cap depends candidates at the N strongest "
                              "(the edge-judge quota)")
```

In the `edges` dispatch branch, before the `--apply` handling:

```python
        scope = None
        if args.scope:
            tokens = [t for chunk in args.scope for t in chunk.split(",") if t]
            try:
                scope = {store.resolve_feature(t) for t in tokens}
            except KeyError as e:
                print(str(e).strip('"'))
                return 1
```

then thread it through: `edgesmod.overlap_pairs(store, scope)`, `edgesmod.apply_overlaps(store, scope)`, `edgesmod.depends_candidates(store, scope, args.limit)` in both `--apply` and `--candidates` branches.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_edges.py tests/test_cli.py -v`
Expected: PASS.

- [ ] **Step 6: Run the full suite and commit**

Run: `python -m pytest -q` — expected: all pass.

```bash
git add src/rgit/edges.py src/rgit/cli.py tests/test_edges.py tests/test_cli.py
git commit -m "feat(edges): --scope/--limit for incremental candidate generation"
```

---

### Task 6: CLI `rgit digest` family + keep backfill off the live review surface

**Files:**
- Modify: `src/rgit/cli.py`
- Test: `tests/test_cli.py` (append)

**Interfaces:**
- Consumes: Task 4 `digestqueue` functions, Task 2 `digestscan.MODES/DEFAULT_WINDOW`.
- Produces:
  - `rgit digest scan [A..B] [--mode M] [--window N] [--all] [--json]`
  - `rgit digest status [--json]`
  - `rgit digest next [--batch N] [--json]` (human default: one line per staged item; `--json`: the full `next_batch` items)
  - `rgit digest accept <proposal_id>` / `rgit digest skip <unit_id>` / `rgit digest clear`
  - `rgit pending`, bare `rgit review` listing, and `_sole_open_proposal` all exclude `trigger == "backfill"` proposals (explicit `rgit review --decide <pid>` on a backfill proposal still works).
- Top-of-file import added: `from .digestscan import MODES as DIGEST_MODES`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli.py`:

```python
from conftest import commit_file

T0 = 1_700_000_000


def test_digest_scan_status_next_accept_roundtrip(git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    commit_file(git_repo, "feat.py", "def f():\n    return 1\n", "a feature",
                when=T0)
    assert cli.main(["init"]) == 0
    capsys.readouterr()

    assert cli.main(["digest", "scan", "--json"]) == 0
    scanned = json.loads(capsys.readouterr().out)
    assert scanned["units_total"] >= 1 and scanned["mode"] == "layered"

    assert cli.main(["digest", "status", "--json"]) == 0
    st = json.loads(capsys.readouterr().out)
    assert st["pending_in_mode"] >= 1

    assert cli.main(["digest", "next", "--batch", "1", "--json"]) == 0
    items = json.loads(capsys.readouterr().out)
    assert items and items[0]["proposal_id"].startswith("prop_")

    pid = items[0]["proposal_id"]
    payload = json.dumps([make_candidate("backfilled-feature")])
    monkeypatch.setattr(sys, "stdin", __import__("io").StringIO(payload))
    assert cli.main(["resegment", pid, "--from-json", "-"]) == 0
    capsys.readouterr()

    assert cli.main(["digest", "accept", pid]) == 0
    out = capsys.readouterr().out
    assert "approved ->" in out and "[backfill]" in out
    store = Store.open(git_repo)
    caps = [c for c in store.list_features() if c.origin == "backfill"]
    assert len(caps) == 1 and caps[0].name == "backfilled-feature"

    assert cli.main(["digest", "accept", pid]) == 1        # already resolved
    capsys.readouterr()
    assert cli.main(["digest", "clear"]) == 0
    assert "removed" in capsys.readouterr().out


def test_digest_scan_unknown_mode_fails(git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    cli.main(["init"])
    capsys.readouterr()
    import pytest as _pytest
    with _pytest.raises(SystemExit):                       # argparse choices
        cli.main(["digest", "scan", "--mode", "bogus"])


def test_backfill_proposals_hidden_from_live_surfaces(git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    commit_file(git_repo, "feat.py", "def f():\n    return 1\n", "a feature",
                when=T0)
    cli.main(["init"])
    cli.main(["digest", "scan"])
    cli.main(["digest", "next", "--batch", "1"])
    capsys.readouterr()
    assert cli.main(["pending"]) == 0
    assert "no pending proposals" in capsys.readouterr().out
    assert cli.main(["review"]) == 0
    assert "no pending proposals" in capsys.readouterr().out
    # bare --approve must not resolve to a backfill proposal either
    assert cli.main(["review", "--approve"]) == 1
    assert "no pending proposals" in capsys.readouterr().out
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_cli.py -k digest -v`
Expected: FAIL (`invalid choice: 'digest'`).

- [ ] **Step 3: Implement — parser**

Add near the top imports of `cli.py` (with the other `from .` imports at line ~225):

```python
from .digestscan import MODES as DIGEST_MODES
```

In `build_parser()` (after the `pending` parser block):

```python
    p_dig = sub.add_parser("digest")   # history backfill queue (engine plane)
    dig_sub = p_dig.add_subparsers(dest="dig_cmd", required=True)
    d_scan = dig_sub.add_parser("scan")
    d_scan.add_argument("range", nargs="?", metavar="A..B",
                        help="explicit history range; omit for the "
                             "recent-window default")
    d_scan.add_argument("--mode", choices=list(DIGEST_MODES), default=None)
    d_scan.add_argument("--window", type=int, default=None)
    d_scan.add_argument("--all", dest="all_history", action="store_true",
                        help="scan the whole mainline, ignoring the window")
    d_scan.add_argument("--json", action="store_true")
    d_status = dig_sub.add_parser("status")
    d_status.add_argument("--json", action="store_true")
    d_next = dig_sub.add_parser("next")
    d_next.add_argument("--batch", type=int, default=10)
    d_next.add_argument("--json", action="store_true")
    d_acc = dig_sub.add_parser("accept")
    d_acc.add_argument("proposal_id")
    d_skip = dig_sub.add_parser("skip")
    d_skip.add_argument("unit_id")
    dig_sub.add_parser("clear")
```

- [ ] **Step 4: Implement — dispatch**

Add a `digest` branch in `_dispatch` after the `pending` branch (it runs below `Store.open()`, so `store` exists):

```python
    if args.cmd == "digest":
        import subprocess as _sp
        from . import digestqueue
        if args.dig_cmd == "scan":
            try:
                res = digestqueue.scan_into_store(
                    store, range_spec=args.range, mode=args.mode,
                    window=args.window, all_history=args.all_history, now=_now())
            except ValueError as e:
                print(str(e))
                return 1
            except _sp.CalledProcessError as e:
                err = (e.stderr or "").strip() if isinstance(e.stderr, str) else ""
                print(f"scan failed: {err or e}")
                return 1
            if args.json:
                print(json.dumps(res, indent=2, ensure_ascii=False))
                return 0
            print(f"digest plan: {res['pending']} unit(s) queued "
                  f"(~{res['batches_remaining']} batch(es)); mode {res['mode']}")
            if res["window_applied"]:
                print(f"window applied: scanned the most recent slice of "
                      f"{res['total_mainline']} mainline commits "
                      "(pass a range or --all to go deeper)")
            if res["shallow"]:
                print("note: shallow clone — only the visible history is digestible")
            print("next: ask your agent to run the rgit-digest skill "
                  "(or `rgit digest next --json`)")
            return 0
        if args.dig_cmd == "status":
            st = digestqueue.status(store)
            if args.json:
                print(json.dumps(st, indent=2, ensure_ascii=False))
            else:
                print(f"mode {st['mode']}; {st['units_total']} unit(s): "
                      + ", ".join(f"{k}={v}" for k, v in sorted(st["by_status"].items())))
                print(f"pending in mode: {st['pending_in_mode']} "
                      f"({st['dead_pending']} dead) — "
                      f"~{st['batches_remaining']} batch(es) remaining")
            return 0
        if args.dig_cmd == "next":
            items = digestqueue.next_batch(store, batch=args.batch,
                                           segmenter=_segmenter(), now=_now())
            if args.json:
                print(json.dumps(items, indent=2, ensure_ascii=False))
            else:
                if not items:
                    print("digest queue is empty")
                for it in items:
                    subj = (it["meta"].get("subjects") or ["?"])[0]
                    print(f"{it['unit_id']}  [{it['kind']}]  -> {it['proposal_id']}"
                          f"  \"{subj}\"")
            return 0
        if args.dig_cmd == "accept":
            try:
                res = digestqueue.accept(store, args.proposal_id, now=_now())
            except (KeyError, ValueError) as e:
                print(str(e).strip('"'))
                return 1
            for name, fid in res["capsules"]:
                print(f"approved -> {fid}  {name}  [backfill]")
            if res.get("skipped"):
                print(f"unit {res['unit_id']} skipped ({res['skipped']}): "
                      "no genuine feature in this slice")
            return 0
        if args.dig_cmd == "skip":
            try:
                digestqueue.skip_unit(store, args.unit_id)
            except KeyError as e:
                print(str(e).strip('"'))
                return 1
            print(f"skipped {args.unit_id}")
            return 0
        if args.dig_cmd == "clear":
            res = digestqueue.clear(store)
            print(f"removed {res['capsules_removed']} backfill capsule(s); "
                  f"reset {res['units_reset']} unit(s) to pending")
            return 0
```

- [ ] **Step 5: Implement — hide backfill proposals from live surfaces**

In `_sole_open_proposal`, change the first line and the docstring's last sentence:

```python
    opens = [p for p in store.list_proposals("open") if p.trigger != "backfill"]
```

(add to the docstring: `Backfill proposals belong to the digest queue surface and never count here.`)

In the `pending` branch: `for p in store.list_proposals("open"):` → 

```python
        for p in store.list_proposals("open"):
            if p.trigger == "backfill":     # the digest queue's business
                continue
```

In the bare-`review` listing branch: `proposals = store.list_proposals("open")` →

```python
        proposals = [p for p in store.list_proposals("open")
                     if p.trigger != "backfill"]
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python -m pytest tests/test_cli.py -v`
Expected: PASS.

- [ ] **Step 7: Run the full suite and commit**

Run: `python -m pytest -q` — expected: all pass.

```bash
git add src/rgit/cli.py tests/test_cli.py
git commit -m "feat(cli): rgit digest subcommand family; keep backfill proposals off the live review surface"
```

---

### Task 7: recall/MCP `exclude_backfill` + origin tag in `rgit features`

**Files:**
- Modify: `src/rgit/recall.py`, `src/rgit/mcp_server.py`, `src/rgit/cli.py` (features branch)
- Test: `tests/test_recall.py`, `tests/test_mcp_server.py`, `tests/test_cli.py` (append)

**Interfaces:**
- Produces:
  - `recall(store, query, *, exclude_backfill: bool = False)` — backfill capsules dropped from scoring AND from neighbor subgraphs when the flag is set.
  - MCP `recall_tool(query: str, exclude_backfill: bool = False)`.
  - `rgit features` appends `  [backfill]` to backfill capsules' lines.
  - (Capsule origin already flows through every `to_dict()`-based payload — features/recall/MCP — via Task 3's dataclass field.)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_recall.py` (reuse its existing capsule-building helper if present; otherwise):

```python
def test_recall_exclude_backfill(tmp_path):
    from rgit.recall import recall
    from rgit.store.models import Capsule, CodeSlice
    from rgit.store.store import Store
    store = Store(tmp_path)
    for name, origin in (("live-rerank", "live"), ("old-rerank", "backfill")):
        store.add_feature(Capsule(
            id="", name=name, intent="rerank results", status="approved",
            base_commit="c", knobs={}, data_assumptions=None,
            resurrection_guide=None, result_summary=None, payload_hash=None,
            code_slices=[CodeSlice("m.py", "rerank", None, "def rerank(): pass",
                                   "wrap")], origin=origin))
    names = {r["capsule"].name for r in recall(store, "rerank")}
    assert names == {"live-rerank", "old-rerank"}
    filtered = {r["capsule"].name
                for r in recall(store, "rerank", exclude_backfill=True)}
    assert filtered == {"live-rerank"}
```

Append to `tests/test_mcp_server.py`:

```python
def test_recall_tool_exclude_backfill(git_repo, monkeypatch):
    from rgit.mcp_server import recall_tool
    from rgit.store.models import Capsule, CodeSlice
    from rgit.store.store import Store
    monkeypatch.chdir(git_repo)
    store = Store.init(git_repo)
    store.add_feature(Capsule(
        id="", name="bf-cache", intent="cache layer", status="approved",
        base_commit="c", knobs={}, data_assumptions=None, resurrection_guide=None,
        result_summary=None, payload_hash=None,
        code_slices=[CodeSlice("m.py", "cache", None, "cache()", "wrap")],
        origin="backfill"))
    hits = recall_tool("cache")
    assert hits and hits[0]["capsule"]["origin"] == "backfill"
    assert recall_tool("cache", exclude_backfill=True) == []
```

Append to `tests/test_cli.py`:

```python
def test_features_tags_backfill(git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    cli.main(["init"])
    store = Store.open(git_repo)
    store.add_feature(Capsule(
        id="", name="bf", intent="i", status="approved", base_commit="c",
        knobs={}, data_assumptions=None, resurrection_guide=None,
        result_summary=None, payload_hash=None,
        code_slices=[CodeSlice("m.py", "f", None, "x", "wrap")],
        origin="backfill"))
    capsys.readouterr()
    assert cli.main(["features"]) == 0
    assert "[backfill]" in capsys.readouterr().out
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_recall.py tests/test_mcp_server.py tests/test_cli.py::test_features_tags_backfill -v`
Expected: new tests FAIL (`unexpected keyword argument 'exclude_backfill'`, missing tag).

- [ ] **Step 3: Implement**

`src/rgit/recall.py` — change the signature and the caps line:

```python
def recall(store: Store, query: str, *, exclude_backfill: bool = False) -> list[dict]:
```

```python
    caps = [c for c in store.list_features() if c.status == "approved"
            and not (exclude_backfill and c.origin == "backfill")]
```

(add one line to the docstring: `exclude_backfill drops history-digested capsules from both hits and neighbor subgraphs.` — the subgraph part is automatic: `by_id`/`lex` are built from the filtered `caps`.)

`src/rgit/mcp_server.py`:

```python
def recall_tool(query: str, exclude_backfill: bool = False) -> list[dict]:
    """Find feature capsules by keyword/structure; ranked, with subgraphs.
    exclude_backfill=True hides history-digested capsules."""
    store = Store.open()
    return [{"capsule": _capsule_dict(r["capsule"]),
             "score": r["score"],
             "depends_on": [_capsule_dict(d) for d in r["depends_on"]],
             "overlaps": [_capsule_dict(d) for d in r["overlaps"]]}
            for r in recall(store, query, exclude_backfill=exclude_backfill)]
```

`src/rgit/cli.py` features branch:

```python
    if args.cmd == "features":
        for c in store.list_features():
            tag = "  [backfill]" if c.origin == "backfill" else ""
            print(f"{c.id}  {c.name}  — {c.intent}{tag}")
        return 0
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_recall.py tests/test_mcp_server.py tests/test_cli.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite and commit**

Run: `python -m pytest -q` — expected: all pass.

```bash
git add src/rgit/recall.py src/rgit/mcp_server.py src/rgit/cli.py tests/test_recall.py tests/test_mcp_server.py tests/test_cli.py
git commit -m "feat(recall): exclude-backfill filter; origin in features/MCP payloads"
```

---

### Task 8: `rgit init` offers digestion — flags, TTY prompt, non-TTY hint

**Files:**
- Modify: `src/rgit/cli.py`
- Test: `tests/test_cli.py` (append)

**Interfaces:**
- Consumes: Task 4 `digestqueue.scan_into_store/BATCH_DEFAULT`, Task 2 `DEFAULT_WINDOW`, Task 1 `mainline_count`, existing `_GuidancePromptCancelled`.
- Produces:
  - `rgit init [--digest [MODE]] [--range A..B] [--all] [--no-digest]` — `--digest` alone defaults to mode `layered`; `--range`/`--all` require `--digest`.
  - Behavior: after store creation, with ≥ 2 mainline commits — TTY: numbered mode picker (4 modes + skip) then, only when history exceeds the window, a range picker (recent window / all / custom `A..B`); non-TTY: a one-line stdout hint; `--digest ...`: non-interactive scan. Init only ever plans (scan); agent digestion happens in a host session.
  - Helpers: `_prompt_digest_mode(total: int) -> Optional[str]` (None = skip), `_prompt_digest_range(total: int, window: int) -> tuple[Optional[str], bool]` (`(range_spec, all_history)`), `_init_digest_offer(args, root) -> int`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli.py`:

```python
import io


class _FakeTTY(io.StringIO):
    def isatty(self):
        return True


def test_init_non_tty_prints_digest_hint_with_history(git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    commit_file(git_repo, "a.py", "x = 1\n", "second commit", when=T0)
    assert cli.main(["init"]) == 0
    out = capsys.readouterr().out
    assert "rgit digest scan" in out
    store = Store.open(git_repo)
    assert store.list_digest_units() == []                 # hint only, no scan


def test_init_single_commit_repo_stays_quiet(git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    assert cli.main(["init"]) == 0
    assert "digest" not in capsys.readouterr().out


def test_init_no_digest_flag_suppresses_offer(git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    commit_file(git_repo, "a.py", "x = 1\n", "second commit", when=T0)
    assert cli.main(["init", "--no-digest"]) == 0
    assert "digest" not in capsys.readouterr().out


def test_init_digest_flag_scans_non_interactively(git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    commit_file(git_repo, "a.py", "x = 1\n", "second commit", when=T0)
    assert cli.main(["init", "--digest", "dead"]) == 0
    out = capsys.readouterr().out
    assert "digest plan" in out
    store = Store.open(git_repo)
    assert store.get_digest_meta("mode") == "dead"
    assert store.list_digest_units()


def test_init_tty_prompt_scans_selected_mode(git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    commit_file(git_repo, "a.py", "x = 1\n", "second commit", when=T0)
    monkeypatch.setattr(sys, "stdin", _FakeTTY("1\n"))     # pick "layered"
    assert cli.main(["init"]) == 0
    assert "digest plan" in capsys.readouterr().out
    assert Store.open(git_repo).get_digest_meta("mode") == "layered"


def test_init_tty_prompt_skip_leaves_no_plan(git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    commit_file(git_repo, "a.py", "x = 1\n", "second commit", when=T0)
    monkeypatch.setattr(sys, "stdin", _FakeTTY("5\n"))     # "skip"
    assert cli.main(["init"]) == 0
    assert Store.open(git_repo).list_digest_units() == []
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_cli.py -k init -v`
Expected: new tests FAIL (`unrecognized arguments: --no-digest`, missing hint), while `test_init_creates_store_but_no_hook` still passes.

- [ ] **Step 3: Implement — parser**

Replace `sub.add_parser("init")` in `build_parser()`:

```python
    p_init = sub.add_parser("init")
    p_init.add_argument("--digest", nargs="?", const="layered", default=None,
                        choices=list(DIGEST_MODES), metavar="MODE",
                        help="scan history into a digestion plan now "
                             "(default mode: layered)")
    p_init.add_argument("--range", dest="range_spec", default=None,
                        metavar="A..B", help="with --digest: explicit range")
    p_init.add_argument("--all", dest="all_history", action="store_true",
                        help="with --digest: scan the whole mainline")
    p_init.add_argument("--no-digest", action="store_true",
                        help="skip the history-digestion offer")
```

- [ ] **Step 4: Implement — prompt helpers + offer**

Add near `_prompt_guidance_mode_numbered` in `cli.py`:

```python
_DIGEST_MODE_OPTIONS = [
    ("layered", "everything ranked; dead experiments boosted (recommended)"),
    ("trunk", "only features alive in today's code"),
    ("dead", "only reverted/deleted experiments"),
    ("archaeology", "layered + evolution-chain edge candidates"),
]


def _prompt_digest_mode(total: int) -> Optional[str]:
    """Numbered digest-mode picker; returns None when the user picks skip.
    Prompts go to stderr so stdout stays clean."""
    sys.stderr.write(f"\n{total} mainline commit(s) of history detected — "
                     "digest them into capsules?\n\n")
    for i, (mode, desc) in enumerate(_DIGEST_MODE_OPTIONS, 1):
        sys.stderr.write(f"  {i}) {mode:<12} {desc}\n")
    sys.stderr.write("  5) skip         don't digest history now\n\nSelect [1-5]: ")
    choices: dict = {str(i): m for i, (m, _) in enumerate(_DIGEST_MODE_OPTIONS, 1)}
    choices.update({m: m for m, _ in _DIGEST_MODE_OPTIONS})
    choices.update({"5": None, "skip": None})
    while True:
        sys.stderr.flush()
        try:
            answer = input().strip().lower()
        except EOFError as e:
            raise _GuidancePromptCancelled from e
        if answer in choices:
            return choices[answer]
        sys.stderr.write("Please enter 1-5: ")


def _prompt_digest_range(total: int, window: int) -> tuple:
    """(range_spec, all_history). Only shown when history exceeds the window."""
    if total <= window:
        return None, False
    sys.stderr.write(
        f"\nhistory is {total} commits; the default digests the most "
        f"recent {window}.\n\n"
        f"  1) recent {window} (default)\n"
        "  2) all history\n"
        "  3) custom range (A..B)\n\nSelect [1-3]: ")
    while True:
        sys.stderr.flush()
        try:
            answer = input().strip().lower()
        except EOFError as e:
            raise _GuidancePromptCancelled from e
        if answer in ("", "1"):
            return None, False
        if answer == "2":
            return None, True
        if answer == "3":
            sys.stderr.write("range (A..B): ")
            sys.stderr.flush()
            try:
                spec = input().strip()
            except EOFError as e:
                raise _GuidancePromptCancelled from e
            if spec:
                return spec, False
        sys.stderr.write("Please enter 1-3: ")


def _init_digest_offer(args, root) -> int:
    """After store creation: plan history digestion (free scan only — the
    engine never dispatches agents; the rgit-digest skill drains the queue)."""
    import subprocess as _sp
    from . import digestqueue
    from .digestscan import DEFAULT_WINDOW
    from .gitutil import mainline_count
    if args.no_digest:
        return 0
    try:
        total = mainline_count(root)
    except _sp.CalledProcessError:                # unborn HEAD: no history
        return 0
    if total < 2 and args.digest is None:
        return 0
    mode, range_spec, all_history = args.digest, args.range_spec, args.all_history
    if mode is None:
        if not sys.stdin.isatty():
            print(f"note: {total} mainline commit(s) of history detected; run "
                  "`rgit digest scan` and the rgit-digest skill to backfill "
                  "them into capsules")
            return 0
        try:
            mode = _prompt_digest_mode(total)
            if mode is None:
                return 0
            range_spec, all_history = _prompt_digest_range(total, DEFAULT_WINDOW)
        except (KeyboardInterrupt, _GuidancePromptCancelled):
            print("\ndigest skipped", file=sys.stderr)
            return 0
    store = Store.open(root)
    try:
        res = digestqueue.scan_into_store(store, range_spec=range_spec, mode=mode,
                                          all_history=all_history, now=_now())
    except (_sp.CalledProcessError, ValueError) as e:
        print(f"digest scan failed: {e}")
        return 1
    print(f"digest plan: {res['pending']} unit(s) queued "
          f"(~{res['batches_remaining']} batch(es)); mode {res['mode']}")
    print("next: ask your agent to run the rgit-digest skill to digest them")
    return 0
```

Change the init dispatch branch:

```python
    if args.cmd == "init":
        root = _find_root()
        Store.init(root)
        print(f"initialized .rgit/ in {root}")
        print("note: run `rgit install-hooks` to capture on every commit")
        return _init_digest_offer(args, root)
```

Also add validation right at the top of the branch, before `Store.init`:

```python
        if (args.range_spec or args.all_history) and args.digest is None:
            print("--range/--all require --digest")
            return 1
        if args.no_digest and args.digest is not None:
            print("--no-digest conflicts with --digest")
            return 1
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_cli.py -v`
Expected: PASS (including the pre-existing init test — a 1-commit repo stays below the ≥2 threshold).

- [ ] **Step 6: Run the full suite and commit**

Run: `python -m pytest -q` — expected: all pass.

```bash
git add src/rgit/cli.py tests/test_cli.py
git commit -m "feat(cli): init offers history digestion (TTY prompt, flags, non-TTY hint)"
```

---

### Task 9: Plugin — `rgit-digest` skill, segmenter history context, guidance, README

**Files:**
- Create: `src/rgit/_plugin/skills/rgit-digest/SKILL.md`
- Modify: `src/rgit/_plugin/agents/capsule-segmenter.md`
- Modify: `src/rgit/agent_guidance.py`
- Modify: `README.md` ("More commands" section)
- Test: existing `tests/test_guidance_coupling.py` + `tests/test_installer.py` (no new tests — the coupling test auto-validates the new command and skill name)

**Interfaces:**
- Consumes: the CLI surface from Tasks 5, 6 and the agents from the existing plugin.
- Produces: a third bundled skill (auto-discovered by `installer._skill_links` and covered by the existing `pyproject.toml` glob), an optional `history_context` input on `capsule-segmenter`, and a guidance bullet that teaches host agents the digest flow.

- [ ] **Step 1: Create `src/rgit/_plugin/skills/rgit-digest/SKILL.md`**

```markdown
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
rgit edges --apply --scope <fid1,fid2,...> --limit 30 --json
```

Dispatch `agents/edge-judge.md` once with the `overlap_pairs` and `depends_candidates` from that output plus the referenced capsules' names/intents/slices. Include each backfill capsule's base-commit date; in archaeology mode explicitly ask the judge to consider `supersedes`/`variant_of` for same-region pairs across time. Write confirmed edges with `rgit edges --add ...` exactly as rgit-capture does (symmetric types in BOTH directions).

### 6. Loop and report

Repeat 2–5 until the agreed batches are done or `rgit digest status` shows nothing pending. Then report: units digested → capsules created (dead count), units remaining, and that running this skill again resumes where it left off.

## Notes

- **Sibling flows:** live capture (human-gated) is `rgit-capture`; recall/regeneration is `rgit-recall`.
- Interrupted sessions are safe: `rgit digest next` recycles anything staged but not yet accepted.
```

- [ ] **Step 2: Extend `src/rgit/_plugin/agents/capsule-segmenter.md`**

In "## Your input", append:

```markdown
- `history_context` — OPTIONAL: present when the diff is a historical digestion unit rather than fresh work. Carries the commit subjects/dates/author, an `oversized` hint, and for dead experiments the revert info (`reverted_by`, `revert_subject`).
```

In "## Rules", append:

```markdown
- **Historical mode** (when `history_context` is present): today's code may have refactored past this diff — anchor the `resurrection_guide` to intent and structure (the symbols as they were), fold the commit subjects into your reading of intent, and never invent outcome claims; the engine records revert facts itself.
```

- [ ] **Step 3: Add the guidance bullet in `src/rgit/agent_guidance.py`**

In `render_global_block`'s `body`, insert before the final-feedback bullet (`"- In final feedback, ..."`):

```python
        "- To backfill a mature repo's git history into capsules: `rgit digest "
        "scan` stages a plan (plain `rgit init` offers it), then the "
        "`rgit-digest` skill drains the queue batch by batch.\n"
```

(The block's `h=` fingerprint is computed from the body at render time, so no hash bookkeeping is needed; `tests/test_guidance_coupling.py` will parse `rgit digest scan` and `rgit init` against the real parser and check `rgit-digest` exists as a plugin skill directory — both now true.)

- [ ] **Step 4: Add the README entry**

In `README.md`'s "## More commands" section, append (match the section's existing list formatting):

```markdown
- `rgit digest scan [A..B]` — cluster a mature repo's git history into a scored digestion plan (`rgit init` offers this interactively). `rgit digest status` shows progress; the **rgit-digest** skill drains the queue into `origin=backfill` capsules, and `rgit digest clear` removes them all if you change your mind.
```

- [ ] **Step 5: Run the guidance/installer/plugin tests**

Run: `python -m pytest tests/test_guidance_coupling.py tests/test_agent_guidance.py tests/test_installer.py -v`
Expected: PASS — the coupling test now parametrizes over the new `rgit digest scan` command (parses via Task 6's parser) and finds the `rgit-digest` skill directory. If `test_agent_guidance.py` pins exact block text or a hash constant, update that fixture string to include the new bullet (the `h=` stamp self-computes; `HISTORICAL_HASHES` must NOT be touched).

- [ ] **Step 6: Run the full suite and commit**

Run: `python -m pytest -q` — expected: all pass.

```bash
git add src/rgit/_plugin/skills/rgit-digest/SKILL.md src/rgit/_plugin/agents/capsule-segmenter.md src/rgit/agent_guidance.py README.md
git commit -m "feat(plugin): rgit-digest skill + segmenter history context + guidance"
```

---

### Task 10: End-to-end backfill loop test

**Files:**
- Test: `tests/test_e2e.py` (append)

**Interfaces:** consumes everything above; proves the spec's loop — scripted history → scan → stage → (simulated agent) resegment → accept → recall with filter → clear.

- [ ] **Step 1: Write the test**

Append to `tests/test_e2e.py`:

```python
def test_history_digest_backfill_loop(history_repo):
    from conftest import commit_file, make_candidate, revert_head
    from rgit import digestqueue
    from rgit.curation import validate_candidates
    from rgit.recall import recall as recall_fn
    from rgit.segmenter import HeuristicSegmenter

    T0 = 1_700_000_000
    DAY = 86_400
    # scripted mature history: a landed feature, a reverted experiment, docs noise
    commit_file(history_repo, "model.py",
                "def forward(x):\n    return x * 2\n", "feat: scaled forward",
                when=T0)
    commit_file(history_repo, "exp.py",
                "def trick(x):\n    return x + 1\n", "exp: additive trick",
                when=T0 + 5 * DAY, author="u2")
    revert_head(history_repo, when=T0 + 6 * DAY, author="u2")
    commit_file(history_repo, "README.md", "# readme\n", "docs", when=T0 + 9 * DAY)

    store = Store.init(history_repo)

    # 1. plan (free) — the reverted experiment is a dead unit, docs pre-skipped
    plan = digestqueue.scan_into_store(store, now="2026-07-05T00:00:00")
    assert plan["pending"] == 2                     # landed + dead; docs skipped

    # 2. stage — proposals pinned to historical commits, trigger=backfill
    items = digestqueue.next_batch(store, segmenter=HeuristicSegmenter(),
                                   now="2026-07-05T00:00:01")
    assert {i["kind"] for i in items} == {"landed", "dead"}

    # 3. simulated capsule-segmenter output -> resegment -> accept (no gate)
    fids = {}
    for item in items:
        name = "scaled-forward" if item["kind"] == "landed" else "additive-trick"
        file = "model.py" if item["kind"] == "landed" else "exp.py"
        cands = [make_candidate(name, file=file, symbol="forward"
                                if item["kind"] == "landed" else "trick")]
        validate_candidates(cands)
        store.set_proposal_candidates(item["proposal_id"], cands)
        res = digestqueue.accept(store, item["proposal_id"],
                                 now="2026-07-05T00:00:02")
        (n, fid), = res["capsules"]
        fids[n] = fid

    dead_cap = store.get_feature(fids["additive-trick"])
    assert dead_cap.origin == "backfill"
    assert "reverted by" in dead_cap.result_summary.notes
    assert dead_cap.base_commit                     # pinned in history

    # 4. recall sees backfill; the filter hides it
    hits = {r["capsule"].name for r in recall_fn(store, "additive trick")}
    assert "additive-trick" in hits
    hits_live = {r["capsule"].name
                 for r in recall_fn(store, "additive trick", exclude_backfill=True)}
    assert "additive-trick" not in hits_live

    # 5. queue is drained; clear is the regret channel
    assert digestqueue.status(store)["pending_in_mode"] == 0
    cleared = digestqueue.clear(store)
    assert cleared["capsules_removed"] == 2
    assert digestqueue.status(store)["pending_in_mode"] == 2
```

- [ ] **Step 2: Run the test**

Run: `python -m pytest tests/test_e2e.py -v`
Expected: PASS (both the existing loop test and the new one).

- [ ] **Step 3: Run the full suite and commit**

Run: `python -m pytest -q` — expected: all pass.

```bash
git add tests/test_e2e.py
git commit -m "test(e2e): history digestion backfill loop"
```

---

## Plan Self-Review Notes

- **Spec coverage:** §1 scan/cluster/rank → Tasks 1–2; §2 storage/origin/trigger → Task 3; §3 staging/accept/skip/clear/status + pending exclusion + reconciliation → Tasks 4, 6; §4 edge scope/quota → Task 5 (archaeology chronology rides the existing overlap+judge flow with dates passed by the skill — no extra engine machinery, matching the spec's "same quota"); §5 init → Task 8; §6 skill/plugin → Task 9; §7 errors/recovery → covered inside Tasks 2, 4, 6, 8 (shallow notice, empty/error skips, staged recovery, non-Python = status quo); §8 testing → per-task suites + Task 10.
- **Spec deviation (deliberate):** the spec's `rgit recall --exclude-backfill` line assumed a CLI recall command that does not exist — recall is an API/MCP surface. Implemented as `recall(..., exclude_backfill=)` + the MCP tool parameter (Task 7). `rgit graph` rendering is left unchanged for v1 (origin is in every capsule payload already).
- **Type consistency spot-checks:** `unit_id` prefix `dig_`; `DigestUnit.capsule_ids: list[str]`; `accept` returns `capsules: [[name, fid], ...]` (JSON-friendly lists, unpacked as tuples in tests); `next_batch` item keys match the skill doc (`unit_id, kind, score, proposal_id, meta, diff, candidates, oversized`); `scan_into_store` window `None` → `digestscan.DEFAULT_WINDOW`.
