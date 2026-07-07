import os
import subprocess
from pathlib import Path
import pytest


def make_candidate(name, intent=None, *, anchor="L1", code=None, kind="wrap",
                   file="model.py", symbol="forward", knobs=None, guide=None):
    """The standard test candidate dict, one schema for every call site.

    A plain helper (import it), not a fixture. Defaults derive the intent,
    guide, and code from `name` so the common case is `make_candidate("rerank")`;
    override any field for the variants.
    """
    return {
        "name": name,
        "intent": intent if intent is not None else f"intent of {name}",
        "code_slices": [{"file": file, "symbol": symbol, "anchor": anchor,
                         "code": code if code is not None else f"# {name}",
                         "kind": kind}],
        "knobs": knobs if knobs is not None else {},
        "data_assumptions": None,
        "resurrection_guide": guide if guide is not None else f"guide for {name}",
        "confidence": 0.9,
    }


def _run(args, cwd):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """An initialized git repo with one commit, returned as its root path."""
    _run(["git", "init", "-q"], tmp_path)
    _run(["git", "config", "user.email", "t@t.t"], tmp_path)
    _run(["git", "config", "user.name", "t"], tmp_path)
    # Pin line endings so diffs are byte-identical regardless of the host's global
    # core.autocrlf (true by default on Windows installs would rewrite \n to \r\n).
    _run(["git", "config", "core.autocrlf", "false"], tmp_path)
    (tmp_path / "model.py").write_text("def forward(x):\n    return x\n")
    _run(["git", "add", "."], tmp_path)
    _run(["git", "commit", "-q", "-m", "init"], tmp_path)
    return tmp_path


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
    """One commit touching one file, with pinned author/date. Returns the sha.

    newline="" disables newline translation so the committed blob is byte-exact
    `content` on every platform — Windows' text mode would otherwise write \\r\\n
    to disk and the blob (autocrlf is pinned false) would no longer match the
    literal a test asserts against.
    """
    p = repo / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8", newline="")
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
