import io
import os
import subprocess
import tarfile

import pytest

from rgit.gitutil import (
    MAX_UNTRACKED_DIFF_BYTES,
    current_commit,
    diff_since,
    freeze_worktree,
    materialize,
    parse_git_diff_path,
)
from rgit.store.objects import ObjectStore


def _write_or_skip(path, text: str) -> None:
    try:
        path.write_text(text)
    except OSError as e:
        pytest.skip(f"filesystem does not support this test path: {e}")


def _skip_unless_git_sees_symlink(repo, rel: str) -> None:
    raw = subprocess.run(["git", "diff", "--raw", "HEAD", "--", rel], cwd=repo,
                         check=True, capture_output=True).stdout
    if b"120000" not in raw:
        pytest.skip("git does not report symlinks as symlinks on this platform")


def _commit_tracked_symlink_or_skip(repo, rel: str) -> None:
    subprocess.run(["git", "add", rel], cwd=repo, check=True,
                   capture_output=True)
    mode = subprocess.run(["git", "ls-files", "-s", rel], cwd=repo, check=True,
                          capture_output=True, text=True).stdout
    if not mode.startswith("120000 "):
        pytest.skip("git does not store symlinks as symlinks on this platform")
    subprocess.run(["git", "commit", "-q", "-m", "tracked symlink"],
                   cwd=repo, check=True, capture_output=True)


def test_current_commit_returns_sha(git_repo):
    sha = current_commit(git_repo)
    assert len(sha) == 40


def test_diff_since_head_shows_working_changes(git_repo):
    (git_repo / "model.py").write_text("def forward(x):\n    return x * 2\n")
    diff = diff_since(git_repo, "HEAD")
    assert "x * 2" in diff and "model.py" in diff


def test_diff_since_includes_untracked_new_file(git_repo):
    # a feature living in a brand-new module must not be invisible to capture
    (git_repo / "newmod.py").write_text("def brand_new():\n    return 7\n")
    diff = diff_since(git_repo, "HEAD")
    assert "newmod.py" in diff and "brand_new" in diff


def test_diff_since_includes_untracked_unicode_path_when_quotepath_true(git_repo):
    import subprocess
    subprocess.run(["git", "config", "core.quotePath", "true"], cwd=git_repo,
                   check=True)
    (git_repo / "数据处理.py").write_text("def clean():\n    return 1\n")
    diff = diff_since(git_repo, "HEAD")
    assert "数据处理.py" in diff
    assert "def clean" in diff


def test_diff_since_handles_untracked_path_with_newline(git_repo):
    name = "line\nbreak.py"
    _write_or_skip(git_repo / name, "def odd():\n    return 1\n")
    diff = diff_since(git_repo, "HEAD")
    assert "def odd" in diff


def test_diff_since_skips_large_untracked_text_with_notice(git_repo):
    big = "x" * (MAX_UNTRACKED_DIFF_BYTES + 1)
    (git_repo / "large_notes.txt").write_text(big)
    diff = diff_since(git_repo, "HEAD")
    assert "research-git: skipped untracked file 'large_notes.txt'" in diff
    assert "exceeds" in diff
    assert big[:1000] not in diff


def test_diff_since_skips_untracked_binary_with_notice(git_repo):
    (git_repo / "blob.bin").write_bytes(b"\x00\x01\x02binary")
    diff = diff_since(git_repo, "HEAD")
    assert "research-git: skipped untracked file 'blob.bin' (binary file)" in diff
    assert "Binary files" not in diff


def test_diff_since_skips_untracked_late_binary_with_notice(git_repo):
    (git_repo / "late.bin").write_bytes(b"a" * 9000 + b"\0SECRET_AFTER_SNIFF")
    diff = diff_since(git_repo, "HEAD")
    assert "research-git: skipped untracked file 'late.bin' (binary file)" in diff
    assert "SECRET_AFTER_SNIFF" not in diff


def test_diff_since_skips_untracked_non_utf8_with_notice(git_repo):
    (git_repo / "latin.txt").write_bytes(b"caf\xe9\n")
    diff = diff_since(git_repo, "HEAD")
    assert ("research-git: skipped untracked file 'latin.txt' "
            "(binary or non-UTF-8 file)") in diff


def test_diff_since_skips_untracked_non_regular_with_notice(git_repo):
    fifo = git_repo / "pipe.txt"
    if not hasattr(os, "mkfifo"):
        pytest.skip("mkfifo unavailable")
    try:
        os.mkfifo(fifo)
    except OSError as e:
        pytest.skip(f"mkfifo unavailable on this filesystem: {e}")
    diff = diff_since(git_repo, "HEAD")
    if "pipe.txt" not in diff:
        pytest.skip("git ls-files does not report fifos on this platform")
    assert "research-git: skipped untracked file 'pipe.txt' (not a regular file)" in diff


def test_diff_since_skips_untracked_symlink_outside_repo(git_repo):
    outside = git_repo.parent / "secret-target.txt"
    try:
        os.symlink(outside, git_repo / "leak.txt")
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation unavailable")
    outside.write_text("TOKEN=outside\n")
    diff = diff_since(git_repo, "HEAD")
    assert "research-git: skipped untracked file 'leak.txt'" in diff
    assert "secret-target" not in diff
    assert "TOKEN=outside" not in diff


def test_diff_since_skips_tracked_symlink_outside_repo(git_repo):
    outside = git_repo.parent / "secret-target.py"
    outside.write_text("def TRACKED_SECRET_SYMBOL_TOKEN():\n    return 1\n")
    try:
        (git_repo / "model.py").unlink()
        os.symlink(outside, git_repo / "model.py")
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation unavailable")
    _skip_unless_git_sees_symlink(git_repo, "model.py")
    diff = diff_since(git_repo, "HEAD")
    assert ("research-git: skipped tracked file 'model.py' "
            "(symlink points outside the repo)") in diff
    assert "secret-target" not in diff
    assert "TRACKED_SECRET_SYMBOL_TOKEN" not in diff


def test_diff_since_skips_deleted_tracked_external_symlink(git_repo):
    outside = git_repo.parent / "deleted-secret-target.py"
    outside.write_text("def DELETED_SECRET_SYMBOL_TOKEN():\n    return 1\n")
    try:
        (git_repo / "model.py").unlink()
        os.symlink(outside, git_repo / "model.py")
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation unavailable")
    _commit_tracked_symlink_or_skip(git_repo, "model.py")

    (git_repo / "model.py").unlink()
    diff = diff_since(git_repo, "HEAD")
    assert ("research-git: skipped tracked file 'model.py' "
            "(symlink points outside the repo)") in diff
    assert "deleted-secret-target" not in diff
    assert "DELETED_SECRET_SYMBOL_TOKEN" not in diff


def test_diff_since_captures_replaced_external_symlink_without_leaking(git_repo):
    # Replacing a tracked external symlink with a real file must capture the new
    # content (add-only) while never leaking the old link's outside target.
    outside = git_repo.parent / "replaced-secret-target.py"
    outside.write_text("def REPLACED_SECRET_SYMBOL_TOKEN():\n    return 1\n")
    try:
        (git_repo / "model.py").unlink()
        os.symlink(outside, git_repo / "model.py")
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation unavailable")
    _commit_tracked_symlink_or_skip(git_repo, "model.py")

    (git_repo / "model.py").unlink()
    (git_repo / "model.py").write_text("def replacement():\n    return 2\n")
    diff = diff_since(git_repo, "HEAD")
    assert "def replacement" in diff and "model.py" in diff
    assert "replaced-secret-target" not in diff
    assert "REPLACED_SECRET_SYMBOL_TOKEN" not in diff


def test_binary_skip_reason_reports_unreadable_path():
    # A path that cannot be opened (here, a directory) must yield a skip reason,
    # not None -- returning None would let diff_since silently omit the file.
    from rgit.gitutil import _binary_skip_reason
    import pathlib
    assert _binary_skip_reason(pathlib.Path(".")) == "could not read file"


def test_diff_since_skips_unreadable_untracked_with_notice(git_repo):
    secret = git_repo / "unreadable.py"
    secret.write_text("def hidden_secret():\n    return 1\n")
    try:
        secret.chmod(0o000)
    except OSError:
        pytest.skip("chmod unavailable")
    if os.access(secret, os.R_OK):  # root, or a filesystem that ignores perms
        secret.chmod(0o644)
        pytest.skip("cannot make a file unreadable in this environment")
    try:
        diff = diff_since(git_repo, "HEAD")
    finally:
        secret.chmod(0o644)
    assert ("research-git: skipped untracked file 'unreadable.py' "
            "(could not read file)") in diff
    assert "hidden_secret" not in diff


def test_batch_pathspecs_covers_all_paths_within_budget():
    from rgit.gitutil import _batch_pathspecs
    paths = [f"file_{i}.py" for i in range(10)]  # each 9 bytes
    batches = list(_batch_pathspecs(paths, budget=20))
    assert [p for b in batches for p in b] == paths  # every path, in order
    assert len(batches) > 1  # the tiny budget forces multiple batches
    for b in batches:
        cost = sum(len(p.encode()) + 1 for p in b)
        assert cost <= 20 or len(b) == 1  # a lone oversize path is its own batch


def test_diff_since_batches_many_tracked_files(git_repo, monkeypatch):
    import rgit.gitutil as gitutil
    names = [f"mod_{i}.py" for i in range(8)]
    for n in names:
        (git_repo / n).write_text(f"# {n}\n")
    subprocess.run(["git", "add", "."], cwd=git_repo, check=True,
                   capture_output=True)
    subprocess.run(["git", "commit", "-q", "-m", "many"], cwd=git_repo,
                   check=True, capture_output=True)
    for n in names:
        (git_repo / n).write_text(f"# {n}\nchanged = True\n")
    # Force many tiny git-diff batches so the argv-splitting path is exercised
    # end to end; every changed file must still appear exactly once.
    monkeypatch.setattr(gitutil, "_MAX_DIFF_PATHSPEC_BYTES", 12)
    diff = diff_since(git_repo, "HEAD")
    for n in names:
        assert n in diff
    assert diff.count("changed = True") == len(names)


def test_parse_git_diff_path_handles_timestamp_and_c_quoted_path():
    assert parse_git_diff_path("+++ b/dir/file with spaces.py\t2026-01-01", "+++") == (
        "dir/file with spaces.py")
    assert parse_git_diff_path('+++ "b/line\\nbreak.py"', "+++") == "line\nbreak.py"
    assert parse_git_diff_path('+++ "b/\\346\\225\\260\\346\\215\\256.py"', "+++") == "数据.py"
    assert parse_git_diff_path("+++ /dev/null", "+++") is None
    assert parse_git_diff_path("+++ /dev/null\t2026-01-01", "+++") is None
    assert parse_git_diff_path('+++ "b/a\\ab\\bf\\fv\\v.py"', "+++") == (
        "a\ab\bf\fv\v.py")


def test_freeze_is_deterministic_and_materializes(git_repo, tmp_path):
    objs = ObjectStore(tmp_path / "objects")
    (git_repo / "model.py").write_text("CHANGED\n")
    h1 = freeze_worktree(git_repo, objs)
    h2 = freeze_worktree(git_repo, objs)
    assert h1 == h2                                  # byte-identical snapshot
    dest = tmp_path / "restored"
    materialize(objs, h1, dest)
    assert (dest / "model.py").read_text() == "CHANGED\n"


def _tar_hash(objs: ObjectStore, build) -> str:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        build(tar)
    return objs.put(buf.getvalue())


def test_materialize_rejects_path_traversal(tmp_path):
    objs = ObjectStore(tmp_path / "objects")

    def build(tar):
        data = b"pwned\n"
        info = tarfile.TarInfo("../escape.py"); info.size = len(data)
        tar.addfile(info, io.BytesIO(data))

    h = _tar_hash(objs, build)
    dest = tmp_path / "restored"
    with pytest.raises(ValueError):
        materialize(objs, h, dest)
    assert not (tmp_path / "escape.py").exists()      # nothing written outside dest


def test_materialize_rejects_symlink_member(tmp_path):
    objs = ObjectStore(tmp_path / "objects")

    def build(tar):
        info = tarfile.TarInfo("link"); info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        tar.addfile(info)

    h = _tar_hash(objs, build)
    with pytest.raises(ValueError):
        materialize(objs, h, tmp_path / "restored")


def test_materialize_allows_ordinary_colon_name(tmp_path):
    # a valid POSIX filename containing ':' must NOT be rejected (PR #1 over-strict regression)
    objs = ObjectStore(tmp_path / "objects")

    def build(tar):
        data = b"ok\n"
        info = tarfile.TarInfo("notes:draft.py"); info.size = len(data)
        tar.addfile(info, io.BytesIO(data))

    h = _tar_hash(objs, build)
    dest = tmp_path / "restored"
    if os.name == "nt":
        with pytest.raises(ValueError):
            materialize(objs, h, dest)
        return
    materialize(objs, h, dest)
    assert (dest / "notes:draft.py").read_text() == "ok\n"


def test_freeze_deterministic_when_store_inside_repo(git_repo):
    # object store INSIDE the repo (production layout) must not pollute its own
    # snapshot -> the exclude_root guard keeps the freeze byte-identical.
    objs = ObjectStore(git_repo / "objstore")
    (git_repo / "model.py").write_text("X\n")
    h1 = freeze_worktree(git_repo, objs)
    h2 = freeze_worktree(git_repo, objs)
    assert h1 == h2


def test_freeze_does_not_follow_symlink_outside_repo(git_repo, tmp_path):
    outside = git_repo.parent / "secret-outside-repo.txt"
    try:
        os.symlink(outside, git_repo / "leak.txt")
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation unavailable")
    outside.write_text("TOKEN=outside\n")
    objs = ObjectStore(tmp_path / "objects")

    h = freeze_worktree(git_repo, objs)
    with tarfile.open(fileobj=io.BytesIO(objs.get(h))) as tar:
        names = tar.getnames()
        payload = b"".join(
            tar.extractfile(m).read()
            for m in tar.getmembers()
            if m.isfile() and tar.extractfile(m) is not None)

    assert "leak.txt" not in names
    assert b"TOKEN=outside" not in payload


def test_materialize_allows_safe_relative_symlink(tmp_path):
    try:
        os.symlink("target.txt", tmp_path / "probe-link")
        (tmp_path / "probe-link").unlink()
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation unavailable")
    objs = ObjectStore(tmp_path / "objects")

    def build(tar):
        data = b"ok\n"
        info = tarfile.TarInfo("target.txt"); info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
        link = tarfile.TarInfo("link.txt"); link.type = tarfile.SYMTYPE
        link.linkname = "target.txt"
        tar.addfile(link)

    h = _tar_hash(objs, build)
    dest = tmp_path / "restored"
    materialize(objs, h, dest)
    assert (dest / "link.txt").is_symlink()
    assert (dest / "link.txt").read_text() == "ok\n"


def test_materialize_rejects_before_writing_partial_files(tmp_path):
    objs = ObjectStore(tmp_path / "objects")

    def build(tar):
        data = b"partial\n"
        info = tarfile.TarInfo("partial.txt"); info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
        bad = tarfile.TarInfo("../escape.txt"); bad.size = len(data)
        tar.addfile(bad, io.BytesIO(data))

    h = _tar_hash(objs, build)
    dest = tmp_path / "restored"
    with pytest.raises(ValueError):
        materialize(objs, h, dest)
    assert not (dest / "partial.txt").exists()
