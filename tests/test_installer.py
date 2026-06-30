from pathlib import Path

import pytest
from rgit import agent_guidance, agent_platforms, installer


def _path_endswith(path, *parts):
    """Cross-platform tail match: str(Path) uses '\\' on Windows, so compare parts."""
    return Path(path).parts[-len(parts):] == parts


def _path_contains(path, *parts):
    p = Path(path).parts
    return any(p[i:i + len(parts)] == parts for i in range(len(p) - len(parts) + 1))


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_platforms, "home_dir", lambda: tmp_path)
    monkeypatch.setattr(installer, "_AGENTS_SKILLS_DIR",
                        tmp_path / ".agents" / "skills")
    # Keep guidance paths anchored to the fake home regardless of the host's
    # real config-dir env vars.
    for var in ("XDG_CONFIG_HOME", "CODEX_HOME", "CLAUDE_CONFIG_DIR"):
        monkeypatch.delenv(var, raising=False)
    return tmp_path


def guidance_path(res):
    return Path(res["guidance"]["path"])


def test_plugin_dir_ships_agents_skills_and_manifest():
    pdir = installer.plugin_dir()
    assert (pdir / "agents" / "capsule-segmenter.md").exists()
    assert (pdir / "agents" / "capsule-regenerator.md").exists()
    assert (pdir / "skills" / "rgit-capture" / "SKILL.md").exists()
    assert (pdir / "skills" / "rgit-recall" / "SKILL.md").exists()
    assert (pdir / ".claude-plugin" / "plugin.json").exists()
    assert (pdir / ".claude-plugin" / "marketplace.json").exists()


def test_mcp_config_points_at_rgit_mcp():
    cfg = installer.mcp_config()
    server = cfg["mcpServers"]["research-git"]
    assert server["command"] == "rgit"
    assert server["args"] == ["mcp"]


def test_install_claude_code_dry_run_plans_official_cli(fake_home):
    res = installer.install("claude-code", scope="user", dry_run=True)
    assert res["ran"] is False
    cmds = [" ".join(c) for c in res["planned"]]
    assert any("plugin marketplace add" in c for c in cmds)
    assert any("plugin install research-git@research-git" in c for c in cmds)
    assert any("mcp add -s user research-git -- rgit mcp" in c for c in cmds)
    assert guidance_path(res) == fake_home / ".claude" / "CLAUDE.md"
    assert res["guidance"]["action"] == "would_create"


def test_install_codex_dry_run_symlinks_into_agents_skills(fake_home):
    res = installer.install("codex", dry_run=True)
    assert res["ran"] is False
    assert _path_endswith(res["skills_dir"], ".agents", "skills")
    linked = {Path(l["link"]).name for l in res["links"]}
    assert {"rgit-capture", "rgit-recall"} <= linked
    # each link points back into the bundled plugin so agents/ stays reachable
    assert all(_path_contains(l["target"], "_plugin", "skills") for l in res["links"])
    assert res["mcp_config"]["mcpServers"]["research-git"]["command"] == "rgit"
    assert res["instructions"]
    assert guidance_path(res) == fake_home / ".codex" / "AGENTS.md"
    assert res["guidance"]["action"] == "would_create"
    assert "Current mode: default" in res["guidance"]["block"]


def test_install_codex_surfaces_symlink_permission_hint(fake_home, monkeypatch):
    # On Windows symlink creation needs Developer Mode / admin; a failure must
    # carry an actionable hint, not just an opaque OSError string.
    def fail_symlink(self, target, target_is_directory=False):
        raise OSError("privilege not held")

    monkeypatch.setattr(Path, "symlink_to", fail_symlink)
    res = installer.install("codex")
    assert res["errors"]
    assert all("Developer Mode" in e["hint"] for e in res["errors"])


def test_generic_is_an_alias_for_the_agents_skills_install(fake_home):
    res = installer.install("generic", dry_run=True)
    assert _path_endswith(res["skills_dir"], ".agents", "skills")
    assert res["guidance"]["action"] == "manual"
    assert "path" not in res["guidance"]
    assert agent_guidance.START in res["guidance"]["block"]


def test_uninstall_generic_guidance_is_instruction_only(fake_home):
    res = installer.uninstall("generic", dry_run=True)
    assert res["guidance"]["action"] == "manual"
    assert "remove" in res["guidance"]["instructions"]
    assert "block" not in res["guidance"]


def test_install_gemini_dry_run_reports_guidance_write(fake_home):
    res = installer.install("gemini", dry_run=True)
    assert guidance_path(res) == fake_home / ".gemini" / "GEMINI.md"
    assert res["guidance"]["action"] == "would_create"


def test_install_opencode_dry_run_reports_guidance_fallback(fake_home):
    res = installer.install("opencode", dry_run=True)
    assert res["guidance"]["action"] == "manual"
    assert "path" not in res["guidance"]
    assert agent_guidance.START in res["guidance"]["block"]


def test_guidance_target_honors_config_dir_env_vars(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_platforms, "home_dir", lambda: tmp_path / "home")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-cfg"))

    assert (agent_platforms.guidance_target("codex")["path"]
            == tmp_path / "codex-home" / "AGENTS.md")
    assert (agent_platforms.guidance_target("claude-code")["path"]
            == tmp_path / "claude-cfg" / "CLAUDE.md")


def test_guidance_target_opencode_honors_xdg_config_home(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_platforms, "home_dir", lambda: tmp_path / "home")
    xdg = tmp_path / "xdg"
    (xdg / "opencode").mkdir(parents=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))

    assert (agent_platforms.guidance_target("opencode")["path"]
            == xdg / "opencode" / "AGENTS.md")


def test_install_codex_mode_none_skips_guidance_write(fake_home):
    res = installer.install("codex", mode="none")
    assert res["ran"] is True
    assert (fake_home / ".agents" / "skills" / "rgit-capture").is_symlink()
    assert res["guidance"] == {"action": "disabled"}
    assert not (fake_home / ".codex" / "AGENTS.md").exists()


def test_uninstall_codex_mode_none_leaves_block_untouched(fake_home):
    guidance = fake_home / ".codex" / "AGENTS.md"
    guidance.parent.mkdir(parents=True)
    guidance.write_text(agent_guidance.render_global_block(), encoding="utf-8")

    res = installer.uninstall("codex", mode="none")

    assert res["guidance"] == {"action": "disabled"}
    assert agent_guidance.START in guidance.read_text(encoding="utf-8")


def test_install_claude_code_mode_none_dry_run(fake_home):
    res = installer.install("claude-code", dry_run=True, mode="none")
    assert res["guidance"] == {"action": "disabled"}


def test_install_codex_mode_manual_only_pins_mode(fake_home):
    res = installer.install("codex", mode="manual-only")
    assert res["guidance"]["action"] == "created"
    text = (fake_home / ".codex" / "AGENTS.md").read_text(encoding="utf-8")
    assert "Current mode: manual-only" in text


def test_explicit_mode_overrides_previously_pinned_mode(fake_home):
    installer.install("codex", mode="manual-only")
    installer.install("codex", mode="default")
    text = (fake_home / ".codex" / "AGENTS.md").read_text(encoding="utf-8")
    assert "Current mode: default" in text
    assert "Current mode: manual-only" not in text


def test_agent_cli_platforms_are_registered():
    for pid in ("codex", "gemini", "opencode", "generic"):
        assert pid in installer.PLATFORMS


def test_uninstall_codex_dry_run_lists_links(fake_home):
    res = installer.uninstall("codex", dry_run=True)
    assert res["ran"] is False
    assert any(_path_endswith(l, ".agents", "skills", "rgit-capture")
               for l in res["would_remove"])
    assert guidance_path(res) == fake_home / ".codex" / "AGENTS.md"
    assert res["guidance"]["action"] == "absent"


def test_uninstall_claude_code_dry_run_plans_removal(fake_home):
    res = installer.uninstall("claude-code", dry_run=True)
    cmds = [" ".join(c) for c in res["planned"]]
    assert any("mcp remove" in c for c in cmds)
    assert any("plugin uninstall research-git@research-git" in c for c in cmds)
    assert guidance_path(res) == fake_home / ".claude" / "CLAUDE.md"


def test_install_claude_code_skips_guidance_when_cli_command_fails(
        fake_home, monkeypatch):
    def fail(plan):
        return [{"cmd": plan[0], "rc": 127, "out": "missing claude"}]

    monkeypatch.setattr(installer, "_run", fail)

    res = installer.install("claude-code")

    assert res["guidance"]["action"] == "skipped_error"
    assert "install commands failed" in res["guidance"]["error"]
    assert not (fake_home / ".claude" / "CLAUDE.md").exists()


def test_uninstall_claude_code_keeps_guidance_when_cli_command_fails(
        fake_home, monkeypatch):
    guidance = fake_home / ".claude" / "CLAUDE.md"
    guidance.parent.mkdir(parents=True)
    guidance.write_text(agent_guidance.render_global_block())

    def fail(plan):
        return [{"cmd": plan[0], "rc": 1, "out": "remove failed"}]

    monkeypatch.setattr(installer, "_run", fail)

    res = installer.uninstall("claude-code")

    assert res["guidance"]["action"] == "skipped_error"
    assert "uninstall commands failed" in res["guidance"]["error"]
    assert agent_guidance.START in guidance.read_text()


def test_install_codex_writes_guidance_and_symlinks_under_fake_home(fake_home):
    res = installer.install("codex")

    assert res["ran"] is True
    assert (fake_home / ".agents" / "skills" / "rgit-capture").is_symlink()
    guidance = fake_home / ".codex" / "AGENTS.md"
    assert guidance.exists()
    assert guidance.read_text().count(agent_guidance.START) == 1
    assert res["guidance"]["action"] == "created"


def test_install_codex_is_idempotent_for_guidance(fake_home):
    installer.install("codex")
    res = installer.install("codex")

    guidance = fake_home / ".codex" / "AGENTS.md"
    assert guidance.read_text().count(agent_guidance.START) == 1
    assert res["guidance"]["action"] == "unchanged"


def test_uninstall_codex_removes_only_managed_guidance(fake_home):
    guidance = fake_home / ".codex" / "AGENTS.md"
    block = agent_guidance.render_global_block()
    guidance.parent.mkdir(parents=True)
    guidance.write_text("before\n" + block + "\nafter\n")

    res = installer.uninstall("codex")

    assert res["guidance"]["action"] == "removed"
    text = guidance.read_text()
    assert "before\n" in text
    assert "after\n" in text
    assert agent_guidance.START not in text


def test_install_codex_symlink_failure_is_structured_and_skips_guidance(
        fake_home, monkeypatch):
    def boom(self, *a, **k):
        raise OSError("symlink denied")

    monkeypatch.setattr(Path, "symlink_to", boom)

    res = installer.install("codex", mode="default")

    assert res["errors"]
    assert "symlink denied" in res["errors"][0]["error"]
    assert res["guidance"]["action"] == "skipped_error"
    assert not (fake_home / ".codex" / "AGENTS.md").exists()


def test_guidance_write_error_is_structured_not_fatal(fake_home, monkeypatch):
    def boom(path, *, mode=None, dry_run=False):
        raise OSError("nope")

    monkeypatch.setattr(agent_guidance, "upsert_managed_block", boom)

    res = installer.install("codex")

    assert res["ran"] is True
    assert res["guidance"]["action"] == "skipped_error"
    assert "nope" in res["guidance"]["error"]


def test_guidance_paths_compare_as_paths(fake_home):
    res = installer.install("codex", dry_run=True)
    assert guidance_path(res) == fake_home / ".codex" / "AGENTS.md"


def test_unknown_platform_raises():
    with pytest.raises(ValueError):
        installer.install("nope", dry_run=True)


def test_edge_judge_agent_is_packaged():
    from rgit import installer
    agents = installer.plugin_dir() / "agents"
    assert (agents / "edge-judge.md").exists()


def test_capture_skill_uses_cli_not_mcp_write_tools():
    from rgit import installer
    skill = (installer.plugin_dir() / "skills" / "rgit-capture" / "SKILL.md").read_text()
    assert "rgit pending" in skill
    assert "rgit resegment" in skill
    assert "pending_captures" not in skill     # MCP write tools are gone
    assert "resegment(" not in skill
