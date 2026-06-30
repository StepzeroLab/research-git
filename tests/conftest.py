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
    # Pin line endings so diffs are byte-identical regardless of the host's global
    # core.autocrlf (true by default on Windows installs would rewrite \n to \r\n).
    _run(["git", "config", "core.autocrlf", "false"], tmp_path)
    (tmp_path / "model.py").write_text("def forward(x):\n    return x\n")
    _run(["git", "add", "."], tmp_path)
    _run(["git", "commit", "-q", "-m", "init"], tmp_path)
    return tmp_path
