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
    if os.name != "nt":                                # POSIX exec bit only
        assert os.stat(hook).st_mode & stat.S_IXUSR    # executable


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


def test_marker_substring_does_not_make_foreign_hook_ours(git_repo):
    hook = _hook(git_repo)
    hook.parent.mkdir(parents=True, exist_ok=True)
    text = f"#!/bin/sh\n# user note mentioning {MARKER}\necho mine\n"
    hook.write_text(text)
    res = install_hooks(git_repo)
    assert res["action"] == "skipped_foreign"
    assert hook.read_text() == text
    assert uninstall_hooks(git_repo)["action"] == "skipped_foreign"
    assert hook.read_text() == text


def test_dry_run_writes_nothing(git_repo):
    res = install_hooks(git_repo, dry_run=True)
    assert res["action"] == "would_install"
    assert not _hook(git_repo).exists()


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


def test_binary_foreign_hook_is_skipped_not_decoded(git_repo):
    # a non-UTF-8 foreign hook must classify as foreign, never raise / clobber
    hook = _hook(git_repo)
    hook.parent.mkdir(parents=True, exist_ok=True)
    blob = b"\x89PNG\xff\xfe\x00\x80 not text\n"
    hook.write_bytes(blob)
    assert install_hooks(git_repo)["action"] == "skipped_foreign"
    assert uninstall_hooks(git_repo)["action"] == "skipped_foreign"
    assert hook.read_bytes() == blob                      # left byte-identical
