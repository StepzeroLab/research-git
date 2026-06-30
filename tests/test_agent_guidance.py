from rgit import agent_guidance


def test_render_global_block_contains_markers_and_default_mode():
    block = agent_guidance.render_global_block()
    assert agent_guidance.START in block
    assert agent_guidance.END in block
    assert "Current mode: default" in block
    # missing-store guidance is conditional: autonomous bootstraps, interactive asks
    assert "rgit capture --init --trigger manual" in block      # autonomous path
    assert "rgit init" in block                                 # interactive path
    assert "autonomously" in block and "interactive" in block
    assert "rgit-recall" in block


def test_upsert_creates_parent_and_file(tmp_path):
    path = tmp_path / ".codex" / "AGENTS.md"

    res = agent_guidance.upsert_managed_block(path)

    assert res["action"] == "created"
    assert res["path"] == str(path)
    text = path.read_text(encoding="utf-8")
    assert text.count(agent_guidance.START) == 1
    assert text.count(agent_guidance.END) == 1


def test_upsert_appends_without_changing_existing_text(tmp_path):
    path = tmp_path / "AGENTS.md"
    original = "# Project notes\n\nKeep tests focused.\n"
    path.write_text(original, encoding="utf-8")

    res = agent_guidance.upsert_managed_block(path)

    assert res["action"] == "appended"
    text = path.read_text(encoding="utf-8")
    assert text.startswith(original)
    assert text.count(agent_guidance.START) == 1
    assert "Current mode: default" in text


def test_upsert_replaces_existing_managed_block_without_duplication(tmp_path):
    path = tmp_path / "AGENTS.md"
    path.write_text(
        "before\n"
        f"{agent_guidance.START}\nold unique phrase\n{agent_guidance.END}\n"
        "after\n",
        encoding="utf-8",
    )

    res = agent_guidance.upsert_managed_block(path)

    assert res["action"] == "updated"
    text = path.read_text(encoding="utf-8")
    assert "before\n" in text
    assert "after\n" in text
    assert "old unique phrase" not in text
    assert text.count(agent_guidance.START) == 1
    assert text.count(agent_guidance.END) == 1


def test_upsert_preserves_user_pinned_mode_on_update(tmp_path):
    path = tmp_path / "AGENTS.md"
    block = agent_guidance.render_global_block().replace(
        "Current mode: default", "Current mode: manual-only")
    path.write_text(block, encoding="utf-8")

    res = agent_guidance.upsert_managed_block(path)

    text = path.read_text(encoding="utf-8")
    assert "Current mode: manual-only" in text
    assert "Current mode: default" not in text
    assert text.count(agent_guidance.START) == 1
    # Re-rendering with the pinned mode carried over is a no-op write.
    assert res["action"] == "unchanged"


def test_upsert_resets_unrecognized_mode_to_default(tmp_path):
    path = tmp_path / "AGENTS.md"
    block = agent_guidance.render_global_block().replace(
        "Current mode: default", "Current mode: bogus")
    path.write_text(block, encoding="utf-8")

    agent_guidance.upsert_managed_block(path)

    text = path.read_text(encoding="utf-8")
    assert "Current mode: default" in text
    assert "Current mode: bogus" not in text


def test_remove_managed_block_preserves_user_text(tmp_path):
    path = tmp_path / "AGENTS.md"
    path.write_text(
        "before\n"
        f"{agent_guidance.START}\nmanaged\n{agent_guidance.END}\n"
        "after\n",
        encoding="utf-8",
    )

    res = agent_guidance.remove_managed_block(path)

    assert res["action"] == "removed"
    text = path.read_text(encoding="utf-8")
    assert "before\n" in text
    assert "after\n" in text
    assert agent_guidance.START not in text
    assert agent_guidance.END not in text


def test_remove_reports_absent_for_missing_file_or_no_block(tmp_path):
    missing = tmp_path / "missing.md"
    assert agent_guidance.remove_managed_block(missing)["action"] == "absent"

    plain = tmp_path / "plain.md"
    plain.write_text("user text\n", encoding="utf-8")
    assert agent_guidance.remove_managed_block(plain)["action"] == "absent"
    assert plain.read_text(encoding="utf-8") == "user text\n"


def test_manual_uninstall_status_does_not_tell_user_to_add_block():
    res = agent_guidance.manual_uninstall_status()
    assert res["action"] == "manual"
    assert "remove" in res["instructions"]
    assert "block" not in res


def test_upsert_preserves_non_ascii_user_text_as_utf8(tmp_path):
    path = tmp_path / "CLAUDE.md"
    original = "# 项目笔记\n\n保持测试聚焦。café ☕\n"
    path.write_text(original, encoding="utf-8")

    res = agent_guidance.upsert_managed_block(path)

    assert res["action"] == "appended"
    text = path.read_bytes().decode("utf-8")
    assert text.startswith(original)
    assert "项目笔记" in text
    assert text.count(agent_guidance.START) == 1


def test_remove_preserves_non_ascii_user_text_as_utf8(tmp_path):
    path = tmp_path / "CLAUDE.md"
    path.write_text(
        "中文开头\n"
        f"{agent_guidance.START}\nmanaged\n{agent_guidance.END}\n"
        "café 结尾\n",
        encoding="utf-8",
    )

    res = agent_guidance.remove_managed_block(path)

    assert res["action"] == "removed"
    text = path.read_bytes().decode("utf-8")
    assert "中文开头" in text
    assert "café 结尾" in text
    assert agent_guidance.START not in text


def test_dry_run_reports_action_and_writes_nothing(tmp_path):
    path = tmp_path / "AGENTS.md"

    res = agent_guidance.upsert_managed_block(path, dry_run=True)

    assert res["action"] == "would_create"
    assert res["path"] == str(path)
    assert "block" in res
    assert not path.exists()
