"""`rgit update`: delegate the package upgrade to whichever installer owns
this environment, then refresh installed platforms so the Claude Code plugin
copy and guidance blocks track the new version.

We never touch site-packages ourselves — uv/pipx/pip own their environments.
The platform refresh runs in a *fresh subprocess* because this process still
has the pre-upgrade code and plugin assets in memory.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

_PEP668_MARKER = "externally-managed-environment"
_WIN_LOCK_MARKERS = ("WinError 5", "Access is denied")


def detect_installer() -> str:
    # Normalize backslashes ourselves: on a non-Windows host Path is PosixPath,
    # so .as_posix() leaves a Windows-style prefix's backslashes intact and the
    # "/uv/tools/" marker would never match. Detection must be host-independent.
    prefix = Path(sys.prefix).as_posix().replace("\\", "/").lower()
    if "/uv/tools/" in prefix:
        return "uv-tool"
    if "/pipx/venvs/" in prefix:
        return "pipx"
    return "pip"


def upgrade_command(installer: str) -> list[str]:
    return {
        "uv-tool": ["uv", "tool", "upgrade", "research-git"],
        "pipx": ["pipx", "upgrade", "research-git"],
        "pip": [sys.executable, "-m", "pip", "install", "--upgrade",
                "research-git"],
    }[installer]


def _rgit_cmd() -> list[str]:
    exe = shutil.which("rgit")
    return [exe] if exe else [sys.executable, "-m", "rgit"]


def _refresh_platforms() -> None:
    from . import installer as inst
    platforms = inst.detect_platforms()
    if not platforms:
        print("no agent platforms detected; nothing to refresh")
        return
    for pf in platforms:
        cmd = _rgit_cmd() + ["install", pf, "--from-update"]
        p = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        print(f"{pf}: " + ("refreshed" if p.returncode == 0
                           else "refresh FAILED"))
        tail = ((p.stdout or "") + (p.stderr or "")).strip()
        if tail:
            print("  " + "\n  ".join(tail.splitlines()))


def run_update(refresh: bool = True) -> int:
    """Upgrade the package; on success refresh installed platforms.

    Exit code reflects the upgrade step only (spec: everything else degrades
    to hints).
    """
    cmd = upgrade_command(detect_installer())
    print("upgrading research-git via: " + " ".join(cmd))
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
    except FileNotFoundError:
        print(f"`{cmd[0]}` not found — upgrade manually: "
              f"{sys.executable} -m pip install -U research-git",
              file=sys.stderr)
        return 1
    if p.stdout:
        sys.stdout.write(p.stdout)
    if p.stderr:
        sys.stderr.write(p.stderr)
    if p.returncode != 0:
        out = (p.stdout or "") + (p.stderr or "")
        if _PEP668_MARKER in out:
            print("this Python forbids pip installs (PEP 668) — try: "
                  "uv tool install research-git", file=sys.stderr)
        elif any(m in out for m in _WIN_LOCK_MARKERS):
            print("Windows locked rgit.exe while it was running — run "
                  "manually: python -m pip install -U research-git",
                  file=sys.stderr)
        return p.returncode
    if refresh:
        _refresh_platforms()
    return 0
