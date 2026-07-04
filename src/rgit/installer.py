"""Cross-platform installer: `rgit install <platform>` wires the bundled plugin (agents + skills) and the MCP server into each AI client's expected locations.

The plugin assets ship inside the wheel at `rgit/_plugin/`, so after `pip install research-git` the `rgit` binary is on PATH and the installer can point any client at `rgit mcp` (no absolute venv paths). Adapters are dry-run friendly: `dry_run=True` returns the exact commands/links/config it *would* apply without touching anything.
"""
from __future__ import annotations
import json
import shutil
import subprocess
from functools import partial
from importlib.resources import files
from pathlib import Path

from . import agent_guidance, agent_platforms


def plugin_dir() -> Path:
    """On-disk path to the bundled plugin (agents/, skills/, .claude-plugin/)."""
    return Path(str(files("rgit").joinpath("_plugin")))


def mcp_config() -> dict:
    """Standard MCP server config understood by any MCP-capable client."""
    return {"mcpServers": {"research-git": {"command": "rgit", "args": ["mcp"]}}}


def _run(plan: list[list[str]]) -> list[dict]:
    results = []
    for cmd in plan:
        # Decode as UTF-8 (the agent CLIs emit UTF-8 glyphs like ✓ and curly
        # quotes on every platform); without this, text=True uses the locale
        # codepage (GBK on Chinese Windows) and the reader thread dies on a
        # UnicodeDecodeError, leaving p.stdout None (issue #11). errors="replace"
        # plus the `or ""` guards keep a half-installed plan from crashing.
        p = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        results.append({"cmd": cmd, "rc": p.returncode,
                        "out": ((p.stdout or "") + (p.stderr or "")).strip()})
    return results


def _install_guidance(platform: str, dry_run: bool, mode: str | None = None,
                      conservative: bool = False) -> dict:
    if mode == "none":
        return {"action": "disabled"}
    target = agent_platforms.guidance_target(platform)
    if target is None:
        return agent_guidance.manual_status(mode or "default")
    if conservative:
        res = agent_guidance.refresh_managed_block(target["path"])
        if res.get("action") == "skipped_removed":
            from . import updatecheck
            if updatecheck.hint_pending(res["path"]):
                updatecheck.mark_hint_shown(res["path"])
            else:
                res.pop("hint", None)
        res["reload"] = target["reload"]
        return res
    try:
        res = agent_guidance.upsert_managed_block(target["path"], mode=mode,
                                                  dry_run=dry_run)
    except OSError as e:
        return _guidance_error(target["path"], e, mode)
    res["reload"] = target["reload"]
    return res


def _uninstall_guidance(platform: str, dry_run: bool, mode: str | None = None) -> dict:
    if mode == "none":
        return {"action": "disabled"}
    target = agent_platforms.guidance_target(platform)
    if target is None:
        return agent_guidance.manual_uninstall_status()
    try:
        res = agent_guidance.remove_managed_block(target["path"], dry_run=dry_run)
    except OSError as e:
        return {"action": "skipped_error", "path": str(target["path"]),
                "error": str(e)}
    res["reload"] = target["reload"]
    return res


def _guidance_error(path: Path, error: Exception | str,
                    mode: str | None = None) -> dict:
    return {
        "action": "skipped_error",
        "path": str(path),
        "error": str(error),
        "block": agent_guidance.render_global_block(mode or "default"),
        "instructions": "Add this managed block manually if you want global research-git guidance.",
    }


def _all_ok(results: list[dict]) -> bool:
    return all(r["rc"] == 0 for r in results)


# ---- claude-code -----------------------------------------------------------

def _plan_claude_code(scope: str) -> list[list[str]]:
    pdir = str(plugin_dir())
    return [
        ["claude", "plugin", "marketplace", "add", pdir],
        ["claude", "plugin", "install", "research-git@research-git"],
        ["claude", "mcp", "add", "-s", scope, "research-git", "--", "rgit", "mcp"],
    ]


def _install_claude_code(scope: str, dry_run: bool, mode: str | None = None,
                         conservative: bool = False) -> dict:
    plan = _plan_claude_code(scope)
    if dry_run:
        return {"platform": "claude-code", "planned": plan,
                "guidance": _install_guidance("claude-code", dry_run, mode,
                                              conservative),
                "ran": False}
    results = _run(plan)
    if mode != "none" and not _all_ok(results):
        g = _guidance_error(agent_platforms.guidance_target("claude-code")["path"],
                            "install commands failed", mode)
    else:
        g = _install_guidance("claude-code", dry_run, mode, conservative)
    return {"platform": "claude-code", "results": results,
            "guidance": g, "ran": True}


def _uninstall_claude_code(scope: str, dry_run: bool, mode: str | None = None) -> dict:
    plan = [
        ["claude", "mcp", "remove", "-s", scope, "research-git"],
        ["claude", "plugin", "uninstall", "research-git@research-git"],
    ]
    if dry_run:
        return {"platform": "claude-code", "planned": plan,
                "guidance": _uninstall_guidance("claude-code", dry_run, mode),
                "ran": False}
    results = _run(plan)
    if mode != "none" and not _all_ok(results):
        path = agent_platforms.guidance_target("claude-code")["path"]
        g = {"action": "skipped_error", "path": str(path),
             "error": "uninstall commands failed"}
    else:
        g = _uninstall_guidance("claude-code", dry_run, mode)
    return {"platform": "claude-code", "results": results,
            "guidance": g, "ran": True}


# ---- agent-CLI family (Codex / Gemini / opencode: ~/.agents/skills) ---------
# These CLIs discover skills under ~/.agents/skills/. We symlink each bundled
# skill there (one link per skill), pointing back into the plugin tree, so that
# at runtime a skill can resolve its own real path and reach the *sibling*
# agents/ directory (see the "Locating the agent definitions" note in each
# SKILL.md). Symlinks — not copies — are what make that resolution work, and
# they also keep the skills updated in place when the package is upgraded.

_AGENTS_SKILLS_DIR = Path.home() / ".agents" / "skills"

# Symlinks (not copies) are what let a skill resolve back to its sibling agents/.
# On Windows, creating one needs Developer Mode or an elevated shell; surface that
# instead of an opaque OSError so the user knows the one concrete thing to do.
_SYMLINK_HINT = (
    "research-git needs symlinks so skills can resolve their bundled agents. "
    "On Windows, enable Developer Mode (Settings -> Privacy & security -> For "
    "developers) or run the install from an Administrator terminal, then rerun "
    "`rgit install`.")


def _skill_links() -> list[tuple[Path, Path]]:
    """[(link in ~/.agents/skills, real skill dir in the plugin)] per bundled skill."""
    skills_root = plugin_dir() / "skills"
    return [(_AGENTS_SKILLS_DIR / s.name, s)
            for s in sorted(p for p in skills_root.iterdir() if p.is_dir())]


def _install_agents_cli(platform: str, scope: str, dry_run: bool,
                        mode: str | None = None,
                        conservative: bool = False) -> dict:
    links = _skill_links()
    planned = [{"link": str(dst), "target": str(src)} for dst, src in links]
    mcp = mcp_config()
    server = json.dumps(mcp["mcpServers"]["research-git"])
    instructions = (
        f"Skills symlinked into {_AGENTS_SKILLS_DIR} (each resolves back into the "
        f"plugin so its agents/ are reachable). For MCP, add this server to your "
        f"client config (e.g. ~/.codex/config.toml [mcp_servers], or the client's "
        f"mcp.json): {server}")
    out = {"platform": platform, "links": planned,
           "skills_dir": str(_AGENTS_SKILLS_DIR), "mcp_config": mcp,
           "instructions": instructions, "ran": not dry_run}
    if dry_run:
        out["guidance"] = _install_guidance(platform, dry_run, mode, conservative)
        return out
    _AGENTS_SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    errors = []
    for dst, src in links:
        try:
            if dst.is_symlink() or dst.exists():
                dst.unlink()
            dst.symlink_to(src, target_is_directory=True)
        except OSError as e:
            errors.append({"link": str(dst), "error": str(e), "hint": _SYMLINK_HINT})
    if errors:
        out["errors"] = errors
    # Mirror the claude-code adapter: if the primary install step failed, don't
    # claim to have wired guidance — report a structured guidance error instead.
    if errors and mode != "none":
        target = agent_platforms.guidance_target(platform)
        out["guidance"] = (
            _guidance_error(target["path"], "skill symlink failed", mode)
            if target is not None
            else {"action": "skipped", "reason": "skill symlink failed"})
    else:
        out["guidance"] = _install_guidance(platform, dry_run, mode, conservative)
    return out


def _uninstall_agents_cli(platform: str, scope: str, dry_run: bool,
                          mode: str | None = None) -> dict:
    links = _skill_links()
    if dry_run:
        return {"platform": platform, "would_remove": [str(d) for d, _ in links],
                "guidance": _uninstall_guidance(platform, dry_run, mode),
                "ran": False}
    removed = []
    for dst, src in links:
        if dst.is_symlink():
            try:
                same = dst.resolve() == src.resolve()
            except OSError:
                same = False
            if same:
                dst.unlink()
                removed.append(str(dst))
    return {"platform": platform, "removed": removed,
            "guidance": _uninstall_guidance(platform, dry_run, mode),
            "ran": True}


# ---- registry --------------------------------------------------------------

# Codex/Gemini/opencode share the ~/.agents/skills convention; `generic` is kept
# as a friendly alias for "any agent CLI that reads ~/.agents/skills".
_AGENT_CLI_IDS = ("codex", "gemini", "opencode", "generic")

_INSTALL = {"claude-code": _install_claude_code}
_UNINSTALL = {"claude-code": _uninstall_claude_code}
for _pid in _AGENT_CLI_IDS:
    _INSTALL[_pid] = partial(_install_agents_cli, _pid)
    _UNINSTALL[_pid] = partial(_uninstall_agents_cli, _pid)

PLATFORMS = tuple(_INSTALL)


def detect_platforms() -> list[str]:
    """Agent clients present on this machine, in PLATFORMS order.

    `generic` is deliberately never detected — it is an alias for "any
    ~/.agents/skills client", not an installation signal. Bare `rgit install`
    uses this so the common case needs no platform choice at all.
    """
    home = Path.home()
    found = []
    if shutil.which("claude"):
        found.append("claude-code")
    if (home / ".codex").is_dir():
        found.append("codex")
    if (home / ".gemini").is_dir():
        found.append("gemini")
    if shutil.which("opencode") or (home / ".config" / "opencode").is_dir():
        found.append("opencode")
    return found


def install(platform: str, *, scope: str = "user", dry_run: bool = False,
            mode: str | None = None, conservative: bool = False) -> dict:
    """Install for `platform`.

    `mode` selects the guidance written: None (default behavior, preserving any
    mode the user pinned earlier), "default"/"manual-only" (write that mode), or
    "none" (skills + MCP only, no guidance write).

    `conservative` (used by `rgit update`) refreshes an existing managed
    guidance block in place instead of prompting/rewriting: customized or
    user-removed blocks are left untouched.
    """
    if platform not in _INSTALL:
        raise ValueError(f"unknown platform '{platform}'. Known: {', '.join(PLATFORMS)}")
    return _INSTALL[platform](scope, dry_run, mode, conservative)


def uninstall(platform: str, *, scope: str = "user", dry_run: bool = False,
              mode: str | None = None) -> dict:
    if platform not in _UNINSTALL:
        raise ValueError(f"unknown platform '{platform}'. Known: {', '.join(PLATFORMS)}")
    return _UNINSTALL[platform](scope, dry_run, mode)
