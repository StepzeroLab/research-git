import io
import os
import tarfile

import pytest

from rgit.gitutil import current_commit, diff_since, freeze_worktree, materialize
from rgit.store.objects import ObjectStore


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


def test_diff_since_skips_untracked_symlink_outside_repo(git_repo):
    outside = git_repo.parent / "secret-target.txt"
    try:
        os.symlink(outside, git_repo / "leak.txt")
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation unavailable")
    outside.write_text("TOKEN=outside\n")
    diff = diff_since(git_repo, "HEAD")
    assert "leak.txt" not in diff
    assert "secret-target" not in diff
    assert "TOKEN=outside" not in diff


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
