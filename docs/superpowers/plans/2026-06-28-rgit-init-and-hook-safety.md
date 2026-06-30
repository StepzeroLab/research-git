# rgit init / hook-install safety Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop `rgit init` from clobbering foreign git hooks; split hook install into its own command; make missing-store situations guide instead of crashing.

**Architecture:** `install_hooks` is rewritten to classify the existing `post-commit` (absent / ours / foreign) via a shared marker and return a status dict, never clobbering a foreign hook. Hook install moves out of `init` into a dedicated `rgit install-hooks` subcommand (with `--uninstall` / `--dry-run`). Bare `init` creates only the store. `run`/`capture` gain `--init`; the single `Store.open()` site is wrapped to print a clean actionable message instead of an uncaught traceback.

**Tech Stack:** Python 3.11+, argparse CLI, pytest. Tests run via `.venv/bin/pytest`.

**Reference spec:** `docs/superpowers/specs/2026-06-28-rgit-init-and-hook-safety-design.md`

---

## File Structure

- `src/rgit/hooks.py` — rewrite: marker constant, `_classify`, `install_hooks(repo, *, dry_run)`, `uninstall_hooks(repo)`, all returning status dicts.
- `src/rgit/cli.py` — drop hook install from `init`; add `install-hooks` subcommand; add `--init` to `run`/`capture`; wrap `Store.open()`.
- `tests/test_hooks.py` — update for the new return value + classification cases.
- `tests/test_cli.py` — update `init` test (no hook); add `install-hooks` and missing-store/`--init` tests.

---

## Task 1: Safe `install_hooks` with classification + status dict

**Files:**
- Modify: `src/rgit/hooks.py`
- Test: `tests/test_hooks.py`

- [ ] **Step 1: Write the failing tests**

Replace the entire contents of `tests/test_hooks.py` with:

```python
import os
import stat

from rgit.hooks import install_hooks, MARKER, CAPTURE_LINE


def _hook(repo):
    return repo / ".git" / "hooks" / "post-commit"


def test_install_writes_executable_marked_hook(git_repo):
    res = install_hooks(git_repo)
    hook = _hook(git_repo)
    assert res["action"] == "installed"
    assert res["path"] == str(hook)
    assert res["line"] == CAPTURE_LINE
    assert MARKER in hook.read_text()
    assert CAPTURE_LINE in hook.read_text()
    assert os.stat(hook).st_mode & stat.S_IXUSR        # executable


def test_reinstall_over_our_hook_is_idempotent(git_repo):
    install_hooks(git_repo)
    res = install_hooks(git_repo)
    assert res["action"] == "reinstalled"
    assert MARKER in _hook(git_repo).read_text()


def test_install_never_clobbers_foreign_hook(git_repo):
    hook = _hook(git_repo)
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/bin/sh\necho mine\n")
    res = install_hooks(git_repo)
    assert res["action"] == "skipped_foreign"
    assert res["line"] == CAPTURE_LINE
    assert hook.read_text() == "#!/bin/sh\necho mine\n"   # left byte-identical


def test_dry_run_writes_nothing(git_repo):
    res = install_hooks(git_repo, dry_run=True)
    assert res["action"] == "would_install"
    assert not _hook(git_repo).exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_hooks.py -v`
Expected: FAIL (ImportError: cannot import name `MARKER` / `install_hooks` returns `None`).

- [ ] **Step 3: Rewrite `src/rgit/hooks.py`**

```python
from __future__ import annotations
import stat
from pathlib import Path

MARKER = "# installed by research-git"
CAPTURE_LINE = "rgit capture --trigger commit"
_POST_COMMIT = f"#!/bin/sh\n{MARKER}\n{CAPTURE_LINE} || true\n"


def _hook_path(repo: Path) -> Path:
    return Path(repo) / ".git" / "hooks" / "post-commit"


def _classify(hook: Path) -> str:
    """absent | ours | foreign — based on presence and the rgit marker."""
    if not hook.exists():
        return "absent"
    return "ours" if MARKER in hook.read_text() else "foreign"


def install_hooks(repo: Path, *, dry_run: bool = False) -> dict:
    """Install the post-commit capture hook, never clobbering a foreign hook.

    Returns {"action", "path", "line"} where action is one of
    installed / reinstalled / skipped_foreign (or the would_* variants under
    dry_run). A foreign (non-marked) hook is left untouched; the caller decides
    whether to append `line` or ask the user.
    """
    hook = _hook_path(repo)
    kind = _classify(hook)
    if kind == "foreign":
        action = "would_skip_foreign" if dry_run else "skipped_foreign"
        return {"action": action, "path": str(hook), "line": CAPTURE_LINE}
    if dry_run:
        action = "would_reinstall" if kind == "ours" else "would_install"
        return {"action": action, "path": str(hook), "line": CAPTURE_LINE}
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text(_POST_COMMIT)
    hook.chmod(hook.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return {"action": "reinstalled" if kind == "ours" else "installed",
            "path": str(hook), "line": CAPTURE_LINE}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_hooks.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/rgit/hooks.py tests/test_hooks.py
git commit -m "feat: install_hooks classifies and never clobbers foreign post-commit"
```

---

## Task 2: `uninstall_hooks`

**Files:**
- Modify: `src/rgit/hooks.py`
- Test: `tests/test_hooks.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_hooks.py`:

```python
from rgit.hooks import uninstall_hooks


def test_uninstall_removes_our_hook(git_repo):
    install_hooks(git_repo)
    res = uninstall_hooks(git_repo)
    assert res["action"] == "uninstalled"
    assert not _hook(git_repo).exists()


def test_uninstall_refuses_foreign_hook(git_repo):
    hook = _hook(git_repo)
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/bin/sh\necho mine\n")
    res = uninstall_hooks(git_repo)
    assert res["action"] == "skipped_foreign"
    assert hook.read_text() == "#!/bin/sh\necho mine\n"   # left intact


def test_uninstall_when_absent(git_repo):
    res = uninstall_hooks(git_repo)
    assert res["action"] == "absent"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_hooks.py -k uninstall -v`
Expected: FAIL (ImportError: cannot import name `uninstall_hooks`).

- [ ] **Step 3: Add `uninstall_hooks` to `src/rgit/hooks.py`**

```python
def uninstall_hooks(repo: Path) -> dict:
    """Remove the post-commit hook only if it is ours (marked).

    Returns {"action", "path"} where action is uninstalled / skipped_foreign /
    absent. A foreign hook is left untouched.
    """
    hook = _hook_path(repo)
    kind = _classify(hook)
    if kind == "absent":
        return {"action": "absent", "path": str(hook)}
    if kind == "foreign":
        return {"action": "skipped_foreign", "path": str(hook)}
    hook.unlink()
    return {"action": "uninstalled", "path": str(hook)}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_hooks.py -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add src/rgit/hooks.py tests/test_hooks.py
git commit -m "feat: uninstall_hooks removes only our marked hook"
```

---

## Task 3: `rgit install-hooks` subcommand

**Files:**
- Modify: `src/rgit/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli.py`:

```python
def test_install_hooks_subcommand(git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    assert cli.main(["install-hooks"]) == 0
    res = json.loads(capsys.readouterr().out)
    assert res["action"] == "installed"
    assert (git_repo / ".git" / "hooks" / "post-commit").exists()


def test_install_hooks_dry_run_writes_nothing(git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    assert cli.main(["install-hooks", "--dry-run"]) == 0
    res = json.loads(capsys.readouterr().out)
    assert res["action"] == "would_install"
    assert not (git_repo / ".git" / "hooks" / "post-commit").exists()


def test_install_hooks_uninstall(git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    cli.main(["install-hooks"])
    capsys.readouterr()
    assert cli.main(["install-hooks", "--uninstall"]) == 0
    res = json.loads(capsys.readouterr().out)
    assert res["action"] == "uninstalled"
    assert not (git_repo / ".git" / "hooks" / "post-commit").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_cli.py -k install_hooks -v`
Expected: FAIL (argparse: invalid choice `install-hooks`).

- [ ] **Step 3: Add the parser and dispatch in `src/rgit/cli.py`**

Add the parser next to the other `sub.add_parser` calls (e.g. after the `install` parser block, before `compare`):

```python
    p_ih = sub.add_parser("install-hooks")   # git post-commit capture hook
    p_ih.add_argument("--uninstall", action="store_true")
    p_ih.add_argument("--dry-run", action="store_true")
```

Add the dispatch branch alongside the other no-store branches (after the `install` branch, before `store = Store.open()`):

```python
    if args.cmd == "install-hooks":
        from .hooks import install_hooks, uninstall_hooks
        if args.uninstall:
            res = uninstall_hooks(_find_root())
        else:
            res = install_hooks(_find_root(), dry_run=args.dry_run)
        print(json.dumps(res, indent=2, ensure_ascii=False))
        return 0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_cli.py -k install_hooks -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/rgit/cli.py tests/test_cli.py
git commit -m "feat: add rgit install-hooks subcommand"
```

---

## Task 4: `rgit init` creates the store only

**Files:**
- Modify: `src/rgit/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Update the failing test**

Replace `test_init_creates_rgit_and_hook` in `tests/test_cli.py` with:

```python
def test_init_creates_store_but_no_hook(git_repo, monkeypatch):
    monkeypatch.chdir(git_repo)
    assert cli.main(["init"]) == 0
    assert (git_repo / ".rgit" / "graph.db").exists()
    assert not (git_repo / ".git" / "hooks" / "post-commit").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_cli.py::test_init_creates_store_but_no_hook -v`
Expected: FAIL (hook still created by `init`).

- [ ] **Step 3: Edit the `init` branch in `src/rgit/cli.py`**

Change the `init` branch from:

```python
    if args.cmd == "init":
        Store.init(_find_root())
        install_hooks(_find_root())
        print(f"initialized .rgit/ in {_find_root()}")
        return 0
```

to:

```python
    if args.cmd == "init":
        Store.init(_find_root())
        print(f"initialized .rgit/ in {_find_root()}")
        print("note: run `rgit install-hooks` to capture on every commit")
        return 0
```

Then remove the now-unused top-level import on line 8:

```python
from .hooks import install_hooks
```

(`install_hooks` is imported lazily inside the `install-hooks` branch from Task 3.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_cli.py -v`
Expected: PASS (init test green; no other test depends on `init` installing a hook).

- [ ] **Step 5: Commit**

```bash
git add src/rgit/cli.py tests/test_cli.py
git commit -m "feat: rgit init creates the store only, not the git hook"
```

---

## Task 5: Missing-store guidance + `--init` on run/capture

**Files:**
- Modify: `src/rgit/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli.py`:

```python
def test_readonly_command_without_store_is_clean_error(git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)                 # git repo, but no `rgit init`
    assert cli.main(["features"]) == 1
    assert "no .rgit/" in capsys.readouterr().out


def test_run_without_store_suggests_init_flag(git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    assert cli.main(["run", "--", "true"]) == 1
    out = capsys.readouterr().out
    assert "no .rgit/" in out and "--init" in out


def test_run_with_init_flag_bootstraps_store(git_repo, monkeypatch):
    monkeypatch.chdir(git_repo)
    cli._SEGMENTER = MockSegmenter([])
    assert cli.main(["run", "--init", "--", "true"]) == 0
    assert (git_repo / ".rgit" / "graph.db").exists()
    assert not (git_repo / ".git" / "hooks" / "post-commit").exists()   # --init never installs hooks
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_cli.py -k "without_store or init_flag" -v`
Expected: FAIL (`run`/`capture` have no `--init`; `Store.open()` raises an uncaught `FileNotFoundError`).

- [ ] **Step 3: Add `--init` to the run and capture parsers**

In the `p_run` block, add:

```python
    p_run.add_argument("--init", action="store_true",
                       help="create .rgit/ at the git root if missing (no hooks)")
```

In the `p_cap` block, add:

```python
    p_cap.add_argument("--init", action="store_true",
                       help="create .rgit/ at the git root if missing (no hooks)")
```

- [ ] **Step 4: Wrap the `Store.open()` site in `src/rgit/cli.py`**

Replace the bare line:

```python
    store = Store.open()
```

with:

```python
    try:
        store = Store.open()
    except FileNotFoundError:
        if getattr(args, "init", False):
            Store.init(_find_root())
            store = Store.open()
        else:
            msg = "no .rgit/ found; run `rgit init` at the git root"
            if args.cmd in ("run", "capture"):
                msg += " (or pass --init to create it now)"
            print(msg)
            return 1
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_cli.py -v`
Expected: PASS.

- [ ] **Step 6: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: PASS (all tests; no regressions).

- [ ] **Step 7: Commit**

```bash
git add src/rgit/cli.py tests/test_cli.py
git commit -m "feat: guide on missing store; --init bootstraps run/capture"
```

---

## Self-Review Notes

- **Spec coverage:** §1 install_hooks safe → Task 1; §2 install-hooks subcommand (install/uninstall/dry-run) → Tasks 2–3; §3 init store-only → Task 4; §4 missing-store guidance + `--init` → Task 5. All covered.
- **Type consistency:** `install_hooks(repo, *, dry_run=False) -> dict`, `uninstall_hooks(repo) -> dict`, marker constants `MARKER` / `CAPTURE_LINE` are defined in Task 1 and reused unchanged in Tasks 2–3.
- **Known doc follow-up (out of scope for tests):** `README.md` mentions `rgit init` installing the hook; update its wording to reference `rgit install-hooks` when convenient. Not gated by this plan.
