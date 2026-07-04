from __future__ import annotations
import stat
from pathlib import Path

MARKER = "# installed by research-git"
# --commit HEAD: right after `git commit` the worktree matches HEAD, so a bare
# worktree capture is empty exactly when the just-committed work is the thing
# to capture (issue #20).
CAPTURE_LINE = "rgit capture --trigger commit --commit HEAD"
_POST_COMMIT = f"#!/bin/sh\n{MARKER}\n{CAPTURE_LINE} || true\n"
_POST_COMMIT_BYTES = _POST_COMMIT.encode("utf-8")
# Exact bytes of every template we ever shipped: a hook matching one of these
# is ours to upgrade or remove. Marker-substring matching would be too loose —
# a user comment mentioning the marker must never make us clobber their hook.
_LEGACY_CAPTURE_LINES = ("rgit capture --trigger commit",)
_MANAGED_HOOK_BYTES = frozenset(
    {_POST_COMMIT_BYTES,
     *(f"#!/bin/sh\n{MARKER}\n{line} || true\n".encode("utf-8")
       for line in _LEGACY_CAPTURE_LINES)})


def _hook_path(repo: Path) -> Path:
    return Path(repo) / ".git" / "hooks" / "post-commit"


def _classify(hook: Path) -> str:
    """absent | ours | foreign — based on exact managed-hook bytes.

    Reads bytes, not text: a foreign hook can be a binary/non-UTF-8 file, and
    decoding it would raise — defeating the whole 'never clobber, never
    traceback' guarantee. A hook that merely contains our marker is still
    foreign; otherwise a user comment could make us overwrite their hook.
    """
    if not hook.exists():
        return "absent"
    return "ours" if hook.read_bytes() in _MANAGED_HOOK_BYTES else "foreign"


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
    # Force LF and UTF-8: /bin/sh (incl. Git for Windows' bundled sh) chokes on
    # CRLF, and write_text would otherwise translate "\n" to the platform newline.
    hook.write_text(_POST_COMMIT, encoding="utf-8", newline="\n")
    hook.chmod(hook.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return {"action": "reinstalled" if kind == "ours" else "installed",
            "path": str(hook), "line": CAPTURE_LINE}


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
