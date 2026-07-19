from rgit import agent_guidance


def test_render_global_block_contains_markers_and_default_mode():
    block = agent_guidance.render_global_block()
    assert agent_guidance._START_RE.match(block)
    assert agent_guidance.END in block
    assert "Current mode: default" in block
    # missing-store guidance is conditional: autonomous bootstraps, interactive asks
    assert "rgit capture --init" in block                       # autonomous path
    assert "rgit init" in block                                 # interactive path
    assert "autonomously" in block and "interactive" in block
    assert "rgit-recall" in block


def test_upsert_creates_parent_and_file(tmp_path):
    path = tmp_path / ".codex" / "AGENTS.md"

    res = agent_guidance.upsert_managed_block(path)

    assert res["action"] == "created"
    assert res["path"] == str(path)
    text = path.read_text(encoding="utf-8")
    assert len(agent_guidance._START_RE.findall(text)) == 1
    assert text.count(agent_guidance.END) == 1


def test_upsert_appends_without_changing_existing_text(tmp_path):
    path = tmp_path / "AGENTS.md"
    original = "# Project notes\n\nKeep tests focused.\n"
    path.write_text(original, encoding="utf-8")

    res = agent_guidance.upsert_managed_block(path)

    assert res["action"] == "appended"
    text = path.read_text(encoding="utf-8")
    assert text.startswith(original)
    assert len(agent_guidance._START_RE.findall(text)) == 1
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
    assert len(agent_guidance._START_RE.findall(text)) == 1
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
    assert len(agent_guidance._START_RE.findall(text)) == 1
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
    assert len(agent_guidance._START_RE.findall(text)) == 1


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


def test_render_global_block_teaches_zero_choice_capture():
    # One command to learn: bare `rgit capture` does the right thing before
    # or after committing; only a span needs an argument. The old flag forms
    # must no longer be taught (they stay as hidden aliases).
    from rgit.agent_guidance import render_global_block
    block = render_global_block()
    assert "run `rgit capture`" in block
    assert "rgit capture main..HEAD" in block
    assert "--trigger manual" not in block
    assert "--commit HEAD" not in block


def test_render_global_block_tells_agents_to_skip_mechanical_changes():
    from rgit.agent_guidance import render_global_block
    assert "Skip mechanical" in render_global_block()


def test_render_global_block_requires_pending_review_details_in_user_language():
    block = agent_guidance.render_global_block()
    assert "open proposals awaiting review" in block
    assert "every candidate's stored name and one-line intent" in block
    assert "key knobs only when they affect the choice" in block
    assert "A candidate count alone is not enough" in block
    assert "language the user is currently using" in block
    assert "Keep proposal ids, capsule names, code symbols, configuration keys, " \
           "and file paths unchanged" in block


def test_render_emits_fingerprinted_start_marker():
    block = agent_guidance.render_global_block()
    m = agent_guidance._START_RE.match(block)
    assert m and m.group(1), "START marker must carry h=<12 hex>"
    assert m.group(1) == agent_guidance.canonical_hash(block)


def test_canonical_hash_ignores_mode_and_start_marker():
    default = agent_guidance.render_global_block("default")
    manual = agent_guidance.render_global_block("manual-only")
    assert agent_guidance.canonical_hash(default) == \
        agent_guidance.canonical_hash(manual)
    legacy = agent_guidance.START + "\n" + default.split("\n", 1)[1]
    assert agent_guidance.canonical_hash(legacy) == \
        agent_guidance.canonical_hash(default)


def test_classify_pristine_and_customized(tmp_path):
    fresh = agent_guidance.render_global_block()
    assert agent_guidance.classify_block("intro\n" + fresh) == "pristine"
    tampered = fresh.replace("Skip mechanical formatting",
                             "Always capture everything")
    assert agent_guidance.classify_block(tampered) == "customized"


def test_classify_absent_and_broken():
    assert agent_guidance.classify_block("# my notes\n") == "absent"
    only_end = f"# notes\n{agent_guidance.END}\n"
    assert agent_guidance.classify_block(only_end) == "broken"
    only_start = f"{agent_guidance.START}\nstuff\n"
    assert agent_guidance.classify_block(only_start) == "broken"


def test_classify_legacy_block_via_historical_hash(monkeypatch):
    # a legacy block: bare START, no fingerprint, body unknown to current render
    body = "## research-git\n\nold official text\n"
    legacy = f"{agent_guidance.START}\n{body}{agent_guidance.END}\n"
    h = agent_guidance.canonical_hash(legacy)
    assert agent_guidance.classify_block(legacy) == "customized"
    monkeypatch.setattr(agent_guidance, "HISTORICAL_HASHES", frozenset({h}))
    assert agent_guidance.classify_block(legacy) == "pristine"


def test_refresh_replaces_pristine_and_carries_mode(tmp_path):
    path = tmp_path / "AGENTS.md"
    old = agent_guidance.render_global_block("manual-only")
    path.write_text("# mine\n\n" + old, encoding="utf-8")
    res = agent_guidance.refresh_managed_block(path)
    assert res["action"] in ("updated", "unchanged")
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# mine\n")
    assert "Current mode: manual-only" in text


def test_refresh_dry_run_reports_would_update_without_writing(tmp_path):
    # A pristine but outdated block under --dry-run must be classified, not
    # rewritten: report would_update and leave the file byte-for-byte unchanged.
    path = tmp_path / "AGENTS.md"
    # A pre-fingerprint (bare-START) block with the current body: pristine, yet
    # refresh would rewrite the marker to the fingerprinted form — a real change.
    fresh = agent_guidance.render_global_block()
    legacy = fresh.replace(agent_guidance._START_RE.match(fresh).group(0),
                          agent_guidance.START)
    path.write_text("# mine\n\n" + legacy, encoding="utf-8")
    assert agent_guidance.classify_block(path.read_text(encoding="utf-8")) \
        == "pristine"
    before = path.read_bytes()

    res = agent_guidance.refresh_managed_block(path, dry_run=True)

    assert res["action"] == "would_update"
    assert res["path"] == str(path)
    assert path.read_bytes() == before


def test_refresh_skips_customized(tmp_path):
    path = tmp_path / "AGENTS.md"
    block = agent_guidance.render_global_block().replace(
        "Skip mechanical formatting", "my own rule")
    path.write_text(block, encoding="utf-8")
    before = path.read_text(encoding="utf-8")
    res = agent_guidance.refresh_managed_block(path)
    assert res["action"] == "skipped_customized"
    assert "rgit install" in res["hint"]
    assert path.read_text(encoding="utf-8") == before


def test_refresh_never_appends_when_removed(tmp_path):
    path = tmp_path / "AGENTS.md"
    path.write_text("# no block here\n", encoding="utf-8")
    res = agent_guidance.refresh_managed_block(path)
    assert res["action"] == "skipped_removed"
    assert "rgit install" in res["hint"]
    assert path.read_text(encoding="utf-8") == "# no block here\n"


def test_refresh_warns_on_broken_markers(tmp_path):
    path = tmp_path / "AGENTS.md"
    path.write_text(f"notes\n{agent_guidance.END}\n", encoding="utf-8")
    res = agent_guidance.refresh_managed_block(path)
    assert res["action"] == "skipped_broken"
    assert str(path) in res["hint"]


def test_refresh_missing_file(tmp_path):
    res = agent_guidance.refresh_managed_block(tmp_path / "nope.md")
    assert res["action"] == "absent_file"


def test_upsert_still_replaces_fingerprinted_block(tmp_path):
    path = tmp_path / "AGENTS.md"
    path.write_text(agent_guidance.render_global_block(), encoding="utf-8")
    res = agent_guidance.upsert_managed_block(path)
    assert res["action"] in ("updated", "unchanged")
    text = path.read_text(encoding="utf-8")
    assert len(agent_guidance._START_RE.findall(text)) == 1
