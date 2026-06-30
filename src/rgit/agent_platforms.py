"""Platform facts for installing research-git agent guidance."""
from __future__ import annotations

import os
from pathlib import Path


def home_dir() -> Path:
    return Path.home()


def _env_dir(var: str) -> Path | None:
    """Return $var as a Path if it is set and non-empty, else None."""
    val = os.environ.get(var)
    return Path(val) if val else None


def _config_home() -> Path:
    """XDG base dir for per-user config, honoring $XDG_CONFIG_HOME."""
    return _env_dir("XDG_CONFIG_HOME") or home_dir() / ".config"


def guidance_target(platform: str) -> dict | None:
    home = home_dir()
    if platform == "codex":
        root = _env_dir("CODEX_HOME") or home / ".codex"
        return {
            "path": root / "AGENTS.md",
            "reload": "Start a new Codex session after install.",
        }
    if platform == "claude-code":
        root = _env_dir("CLAUDE_CONFIG_DIR") or home / ".claude"
        return {
            "path": root / "CLAUDE.md",
            "reload": "Restart Claude Code or run /reload-plugins after install.",
        }
    if platform == "gemini":
        return {
            "path": home / ".gemini" / "GEMINI.md",
            "reload": "Start a new Gemini CLI session after install.",
        }
    if platform == "opencode":
        root = _config_home() / "opencode"
        if root.exists():
            return {
                "path": root / "AGENTS.md",
                "reload": "Start a new opencode session after install.",
            }
    return None
