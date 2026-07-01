from __future__ import annotations
import io
import os
import subprocess
import tarfile
from pathlib import Path
from typing import Optional

from .store.objects import ObjectStore

MAX_UNTRACKED_DIFF_BYTES = 1_000_000


def _git(repo: Path, *args: str) -> str:
    out = subprocess.run(["git", *args], cwd=repo, check=True,
                         capture_output=True, text=True, encoding="utf-8",
                         errors="replace")
    return out.stdout


def _git_ls_files_z(repo: Path, *args: str) -> list[str]:
    """Git path output as NUL-delimited filesystem paths, never C-quoted."""
    out = subprocess.run(["git", "ls-files", "-z", *args], cwd=repo, check=True,
                         capture_output=True)
    return sorted(os.fsdecode(p) for p in out.stdout.split(b"\0") if p)


def _git_diff_entries_z(repo: Path, base: str) -> list[dict[str, str]]:
    out = subprocess.run(["git", "diff", "--raw", "-z", "--no-renames",
                          "--abbrev=40", base, "--"],
                         cwd=repo, check=True, capture_output=True)
    fields = [p for p in out.stdout.split(b"\0") if p]
    entries: list[dict[str, str]] = []
    i = 0
    while i + 1 < len(fields):
        meta = os.fsdecode(fields[i]).split()
        path = os.fsdecode(fields[i + 1])
        i += 2
        if len(meta) < 5 or not meta[0].startswith(":"):
            continue
        entries.append({
            "old_mode": meta[0][1:],
            "new_mode": meta[1],
            "old_sha": meta[2],
            "new_sha": meta[3],
            "status": meta[4],
            "path": path,
        })
    return sorted(entries, key=lambda e: e["path"])


def current_commit(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD").strip()


def _untracked_files(repo: Path) -> list[str]:
    """New, non-ignored files git doesn't track yet (excluding .rgit/)."""
    return [p for p in _git_ls_files_z(repo, "--others", "--exclude-standard")
            if not p.startswith(".rgit/")]


def _notice(path: str, reason: str, kind: str = "untracked file") -> str:
    return f"research-git: skipped {kind} {path!r} ({reason})"


def _binary_skip_reason(path: Path) -> Optional[str]:
    try:
        with path.open("rb") as f:
            chunk = f.read(MAX_UNTRACKED_DIFF_BYTES + 1)
    except OSError:
        return None
    if len(chunk) > MAX_UNTRACKED_DIFF_BYTES:
        return f"exceeds {MAX_UNTRACKED_DIFF_BYTES} byte diff cap"
    if b"\0" in chunk:
        return "binary file"
    try:
        chunk.decode("utf-8")
    except UnicodeDecodeError:
        return "binary or non-UTF-8 file"
    return None


def _decode_c_quoted_path(text: str) -> str:
    data = bytearray()
    i = 0
    while i < len(text):
        ch = text[i]
        if ch != "\\":
            data.extend(ch.encode("utf-8"))
            i += 1
            continue
        i += 1
        if i >= len(text):
            data.append(ord("\\"))
            break
        esc = text[i]
        if esc in "01234567":
            octal = esc
            i += 1
            for _ in range(2):
                if i < len(text) and text[i] in "01234567":
                    octal += text[i]
                    i += 1
                else:
                    break
            data.append(int(octal, 8))
            continue
        mapping = {"a": b"\a", "b": b"\b", "f": b"\f", "n": b"\n",
                   "r": b"\r", "t": b"\t", "v": b"\v", "\\": b"\\",
                   '"': b'"'}
        data.extend(mapping.get(esc, esc.encode("utf-8")))
        i += 1
    return os.fsdecode(bytes(data))


def _split_c_quoted(text: str) -> tuple[str, str]:
    escaped = False
    out = []
    for i, ch in enumerate(text):
        if escaped:
            out.append("\\" + ch)
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            continue
        if ch == '"':
            return "".join(out), text[i + 1:]
        out.append(ch)
    return "".join(out), ""


def parse_git_diff_header(line: str, marker: str) -> tuple[bool, Optional[str]]:
    """(matched, path) from a `---` / `+++` diff header.

    Git may append timestamps after a tab, and may C-quote paths when they carry
    Unicode, tabs, newlines, or other special bytes. path is a repo-relative path
    without the `a/` or `b/` prefix, or None for /dev/null.
    """
    prefix = marker + " "
    if not line.startswith(prefix):
        return False, None
    raw = line[len(prefix):]
    if raw.startswith('"'):
        quoted, _ = _split_c_quoted(raw[1:])
        path = _decode_c_quoted_path(quoted)
    else:
        path = raw.split("\t", 1)[0]
    if path == "/dev/null":
        return True, None
    if path.startswith(("a/", "b/")):
        path = path[2:]
    return True, path


def _git_blob(repo: Path, sha: str) -> bytes:
    if set(sha) == {"0"}:
        return b""
    out = subprocess.run(["git", "cat-file", "-p", sha], cwd=repo, check=True,
                         capture_output=True)
    return out.stdout


def _symlink_target_within(repo: Path, file: str, linkname: str) -> bool:
    path = repo / file
    target = Path(linkname)
    resolved = target if target.is_absolute() else path.parent / target
    return _within(repo, resolved)


def _external_tracked_symlink_reason(repo: Path, entry: dict[str, str]) -> Optional[str]:
    file = entry["path"]
    if entry["old_mode"] == "120000":
        linkname = os.fsdecode(_git_blob(repo, entry["old_sha"]))
        if not _symlink_target_within(repo, file, linkname):
            return "symlink points outside the repo"
    if entry["new_mode"] == "120000":
        path = repo / file
        try:
            linkname = os.readlink(path)
        except OSError:
            if set(entry["new_sha"]) == {"0"}:
                return None
            linkname = os.fsdecode(_git_blob(repo, entry["new_sha"]))
        if not _symlink_target_within(repo, file, linkname):
            return "symlink points outside the repo"
    return None


def parse_git_diff_path(line: str, marker: str) -> Optional[str]:
    """Path from a diff header, or None for non-headers and /dev/null."""
    _, path = parse_git_diff_header(line, marker)
    return path


def diff_since(repo: Path, base: str = "HEAD") -> str:
    """Unified diff of the working tree vs `base`, INCLUDING untracked files.

    Tracked changes come from `git diff`; brand-new files (a common shape for a
    research feature living in its own module) are appended as add-only hunks via
    `git diff --no-index`, without mutating the user's index. Both sources use the
    standard `+++ b/<path>` / `@@` headers the segmenter and astmap rely on.
    """
    parts: list[str] = []
    included_tracked: list[str] = []
    for entry in _git_diff_entries_z(repo, base):
        reason = _external_tracked_symlink_reason(repo, entry)
        if reason:
            parts.append(_notice(entry["path"], reason, kind="tracked file"))
            continue
        included_tracked.append(entry["path"])
    if included_tracked:
        tracked = _git(repo, "-c", "core.quotePath=false", "diff", base, "--",
                       *included_tracked)
        if tracked:
            parts.append(tracked)
    for f in _untracked_files(repo):
        path = repo / f
        if path.is_symlink():
            linkname = os.readlink(path)
            target = Path(linkname)
            resolved = target if target.is_absolute() else path.parent / target
            if not _within(repo, resolved):
                parts.append(_notice(f, "symlink points outside the repo"))
                continue
        if not path.is_file():
            parts.append(_notice(f, "not a regular file"))
            continue
        try:
            size = path.stat().st_size
        except OSError:
            parts.append(_notice(f, "could not stat file"))
            continue
        if size > MAX_UNTRACKED_DIFF_BYTES:
            parts.append(_notice(
                f, f"{size} bytes exceeds {MAX_UNTRACKED_DIFF_BYTES} byte diff cap"))
            continue
        reason = _binary_skip_reason(path)
        if reason:
            parts.append(_notice(f, reason))
            continue
        # --no-index exits 1 when the files differ, so do not check the return code.
        res = subprocess.run(["git", "-c", "core.quotePath=false", "diff",
                              "--no-index", "--", "/dev/null", f],
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
    prefix = None
    if exclude_root is not None:
        try:
            prefix = exclude_root.resolve().relative_to(repo.resolve()).as_posix()
        except ValueError:
            prefix = None  # store lives outside the repo; nothing to exclude
    paths = []
    for p in _git_ls_files_z(repo, "-co", "--exclude-standard"):
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
    except (ValueError, OSError, RuntimeError):
        return False


def materialize(objects: ObjectStore, artifact_hash: str, dest: Path) -> None:
    """Extract a frozen artifact into `dest`, guarding against tar path traversal.

    `freeze_worktree` only ever writes regular files, so we extract member-by-member
    and refuse anything that would escape `dest` (`..`, absolute paths) or that
    isn't a plain file or directory (symlinks/hardlinks/devices — the vectors for
    a malicious archive to write outside the destination). POSIX-only characters
    like ``:`` stay allowed except on Windows, where they are not ordinary
    filesystem names.
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
    if os.name == "nt" and _unsafe_windows_member_name(member.name):
        raise ValueError(f"refusing Windows-unsafe path in artifact: {member.name!r}")
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


_WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
_WINDOWS_FORBIDDEN_CHARS = set('<>:"|?*')


def _unsafe_windows_member_name(name: str) -> bool:
    """True for names Windows cannot materialize as ordinary files."""
    parts = name.replace("\\", "/").split("/")
    for part in parts:
        if not part:
            return True
        if part.endswith((" ", ".")):
            return True
        if any(ord(ch) < 32 or ch in _WINDOWS_FORBIDDEN_CHARS for ch in part):
            return True
        stem = part.split(".", 1)[0].upper()
        if stem in _WINDOWS_RESERVED_NAMES:
            return True
    return False
