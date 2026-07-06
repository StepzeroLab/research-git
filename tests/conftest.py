import subprocess
import sys
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


def python_noop_cmd() -> list[str]:
    return [sys.executable, "-c", "pass"]


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
