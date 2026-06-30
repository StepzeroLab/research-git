from __future__ import annotations
import io
import os
import subprocess
import tarfile
from pathlib import Path

from .store.objects import ObjectStore


def _git(repo: Path, *args: str) -> str:
    out = subprocess.run(["git", *args], cwd=repo, check=True,
                         capture_output=True, text=True, encoding="utf-8",
                         errors="replace")
    return out.stdout


def current_commit(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD").strip()


def _untracked_files(repo: Path) -> list[str]:
    """New, non-ignored files git doesn't track yet (excluding .rgit/)."""
    out = _git(repo, "ls-files", "--others", "--exclude-standard")
    return sorted(p for p in out.splitlines() if p and not p.startswith(".rgit/"))


def diff_since(repo: Path, base: str = "HEAD") -> str:
    """Unified diff of the working tree vs `base`, INCLUDING untracked files.

    Tracked changes come from `git diff`; brand-new files (a common shape for a
    research feature living in its own module) are appended as add-only hunks via
    `git diff --no-index`, without mutating the user's index. Both sources use the
    standard `+++ b/<path>` / `@@` headers the segmenter and astmap rely on.
    """
    parts: list[str] = []
    tracked = _git(repo, "diff", base, "--")
    if tracked:
        parts.append(tracked)
    for f in _untracked_files(repo):
        path = repo / f
        if path.is_symlink():
            linkname = os.readlink(path)
            target = Path(linkname)
            resolved = target if target.is_absolute() else path.parent / target
            if not _within(repo, resolved):
                continue
        # --no-index exits 1 when the files differ, so do not check the return code.
        res = subprocess.run(["git", "diff", "--no-index", "--", "/dev/null", f],
                             cwd=repo, capture_output=True, text=True,
                             encoding="utf-8", errors="replace")
        if res.stdout:
            parts.append(res.stdout)
    return "\n".join(parts)


def _snapshot_paths(repo: Path, exclude_root: Path | None = None) -> list[str]:
    """Tracked + untracked files, excluding ignored, .git and .rgit.

    `exclude_root` (e.g. the ObjectStore directory) is dropped when it lives
    inside the repo so the artifact store never pollutes its own snapshot --
    that would break the byte-identical reproducibility contract.
    """
    out = _git(repo, "ls-files", "-co", "--exclude-standard")
    prefix = None
    if exclude_root is not None:
        try:
            prefix = exclude_root.resolve().relative_to(repo.resolve()).as_posix()
        except ValueError:
            prefix = None  # store lives outside the repo; nothing to exclude
    paths = []
    for p in out.splitlines():
        if not p or p.startswith(".rgit/"):
            continue
        if prefix and (p == prefix or p.startswith(prefix + "/")):
            continue
        paths.append(p)
    return sorted(paths)


def freeze_worktree(repo: Path, objects: ObjectStore) -> str:
    """Deterministic tar of the working tree -> content-addressed hash."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for rel in _snapshot_paths(repo, objects.root):
            path = repo / rel
            info = tarfile.TarInfo(name=rel)
            info.mtime = 0          # normalize for byte-identical snapshots
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            if path.is_symlink():
                linkname = os.readlink(path)
                target = Path(linkname)
                resolved = target if target.is_absolute() else path.parent / target
                # Never follow or archive links that point outside the repo: a
                # worktree snapshot must not smuggle home-directory secrets.
                if not _within(repo, resolved):
                    continue
                info.type = tarfile.SYMTYPE
                info.linkname = linkname
                tar.addfile(info)
                continue
            if not path.is_file():
                continue
            data = path.read_bytes()
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return objects.put(buf.getvalue())


def _within(base: Path, target: Path) -> bool:
    """True if `target` resolves to `base` itself or somewhere underneath it."""
    try:
        target.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False


def materialize(objects: ObjectStore, artifact_hash: str, dest: Path) -> None:
    """Extract a frozen artifact into `dest`, guarding against tar path traversal.

    `freeze_worktree` only ever writes regular files, so we extract member-by-member
    and refuse anything that would escape `dest` (`..`, absolute paths) or that
    isn't a plain file or directory (symlinks/hardlinks/devices — the vectors for
    a malicious archive to write outside the destination). Ordinary names with
    characters like ``:`` stay allowed; only genuinely unsafe entries are rejected.
    """
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(objects.get(artifact_hash))) as tar:
        members = tar.getmembers()
        for member in members:
            _validate_member(dest, member)
        for member in members:
            out = dest / member.name
            if member.isdir():
                out.mkdir(parents=True, exist_ok=True)
            elif member.issym():
                out.parent.mkdir(parents=True, exist_ok=True)
                if out.exists() or out.is_symlink():
                    out.unlink()
                os.symlink(member.linkname, out)
            else:
                f = tar.extractfile(member)
                if f is None:
                    continue
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(f.read())


def _validate_member(dest: Path, member: tarfile.TarInfo) -> None:
    if not _within(dest, dest / member.name):
        raise ValueError(f"refusing unsafe path in artifact: {member.name!r}")
    if member.issym():
        target = Path(member.linkname)
        if target.is_absolute():
            raise ValueError(f"refusing absolute symlink in artifact: {member.name!r}")
        resolved = (dest / member.name).parent / target
        if not _within(dest, resolved):
            raise ValueError(f"refusing escaping symlink in artifact: {member.name!r}")
        return
    if not (member.isfile() or member.isdir()):
        raise ValueError(f"refusing non-regular tar entry: {member.name!r}")
