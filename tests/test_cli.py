import json
import sys
from pathlib import Path

import pytest
import rgit.cli as cli
from rgit.cli import main
from rgit.gitutil import MAX_UNTRACKED_DIFF_BYTES
from rgit.segmenter import MockSegmenter
from rgit.store.store import Store
from rgit.store.models import Capsule, CodeSlice, Run


def test_init_creates_store_but_no_hook(git_repo, monkeypatch):
    monkeypatch.chdir(git_repo)
    assert cli.main(["init"]) == 0
    assert (git_repo / ".rgit" / "graph.db").exists()
    assert not (git_repo / ".git" / "hooks" / "post-commit").exists()


def test_mcp_subcommand_launches_server(monkeypatch):
    # `rgit mcp` should boot the MCP server via mcp_server.run (no separate binary)
    import rgit.mcp_server as srv
    ran = {}
    monkeypatch.setattr(srv, "run", lambda: ran.setdefault("ok", True))
    assert cli.main(["mcp"]) == 0
    assert ran.get("ok")


def test_install_list_and_dry_run(capsys):
    assert cli.main(["install", "--list"]) == 0
    out = capsys.readouterr().out
    assert "claude-code" in out and "generic" in out
    assert cli.main(["install", "claude-code", "--dry-run",
                     "--guidance", "default"]) == 0
    out2 = capsys.readouterr().out
    assert "marketplace" in out2 and "research-git@research-git" in out2


def test_run_from_links_variant_and_refreshes_guide(git_repo, monkeypatch, tmp_path):
    from rgit.store.models import Capsule, CodeSlice
    monkeypatch.chdir(git_repo)
    cli.main(["init"])
    store = Store.open(git_repo)
    src = store.add_feature(Capsule(
        id="", name="src", intent="i", status="approved", base_commit="b", knobs={},
        data_assumptions=None, resurrection_guide="OLD", result_summary=None,
        payload_hash=None, code_slices=[CodeSlice("model.py", "forward", "L1", "x", "wrap")]))
    (git_repo / "train.py").write_text("print('hi')\n")
    cli._SEGMENTER = MockSegmenter([])
    gfile = tmp_path / "guide.txt"
    gfile.write_text("REFRESHED GUIDE")
    assert cli.main(["run", "--from", src, "--refresh-guide-file", str(gfile),
                     "--", sys.executable, "train.py"]) == 0
    after = Store.open(git_repo)
    assert after.neighbors(src, "produced") == []                 # lineage is not a result edge
    assert after.get_feature(src).resurrection_guide == "REFRESHED GUIDE"


def test_run_then_review_then_features(git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    cli.main(["init"])
    (git_repo / "train.py").write_text("print('RGIT_METRIC acc=0.95')\n")
    (git_repo / "model.py").write_text("def forward(x):\n    return x * 3\n")
    candidate = {"name": "triple", "intent": "scale by 3",
                 "code_slices": [{"file": "model.py", "symbol": "forward",
                                  "anchor": "L1", "code": "x*3", "kind": "wrap"}],
                 "knobs": {}, "data_assumptions": None,
                 "resurrection_guide": "x3", "confidence": 0.9}
    cli._SEGMENTER = MockSegmenter([candidate])           # inject, no network

    assert cli.main(["run", "--", sys.executable, "train.py"]) == 0
    out = capsys.readouterr().out
    assert "proposal" in out.lower()

    # one open proposal exists; approve it by index 0
    store = Store.open(git_repo)
    pid = store.list_proposals("open")[0].id
    assert cli.main(["review", "--approve", pid, "--name", "triple"]) == 0

    assert cli.main(["features"]) == 0
    assert "triple" in capsys.readouterr().out


def test_run_failed_command_is_visible_and_nonzero(git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    cli.main(["init"])
    rc = cli.main(["run", "--", sys.executable, "-c",
                   "import sys; print('before fail'); "
                   "print('bad stderr', file=sys.stderr); sys.exit(7)"])
    out = capsys.readouterr().out
    assert rc == 7
    assert "run " in out and "recorded" in out
    assert "no code changes to capture" in out
    assert "command exited with status 7" in out
    assert "before fail" in out
    assert "bad stderr" in out


def test_run_missing_command_is_friendly(git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    cli.main(["init"])
    rc = cli.main(["run", "--", "definitely-not-a-real-rgit-command-xyz"])
    out = capsys.readouterr().out
    assert rc == 127
    assert "command exited with status 127" in out
    assert "command not found" in out


def test_capture_empty_diff_is_friendly(git_repo, monkeypatch, capsys):
    # Explicit worktree target with a clean tree stays a friendly no-op.
    # (A bare `rgit capture` on a clean tree now auto-captures HEAD instead —
    # covered by test_capture_auto_clean_tree_captures_head_with_note.)
    monkeypatch.chdir(git_repo)
    Store.init(git_repo)
    assert cli.main(["capture", "--worktree"]) == 0
    out = capsys.readouterr().out
    assert "nothing to capture" in out
    assert Store.open(git_repo).list_proposals("open") == []


def test_capture_skip_notice_warns_and_json_stays_clean(git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    monkeypatch.setattr(cli, "_SEGMENTER", None)
    Store.init(git_repo)
    (git_repo / "large_notes.txt").write_text(
        "x" * (MAX_UNTRACKED_DIFF_BYTES + 1))
    assert cli.main(["capture", "--trigger", "manual"]) == 0
    out = capsys.readouterr().out
    assert "warning: skipped 1 file(s)" in out
    assert "proposal has 0 candidates" in out

    assert cli.main(["pending"]) == 0
    pending = capsys.readouterr().out
    assert "warning: skipped 1 file(s)" in pending

    assert cli.main(["pending", "--json"]) == 0
    raw = capsys.readouterr().out
    items = json.loads(raw)
    assert "warning:" not in raw
    assert "research-git: skipped untracked file 'large_notes.txt'" in items[0]["diff"]


def test_review_empty_candidates_is_friendly(git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    store = Store.init(git_repo)
    from rgit.store.models import Proposal
    diff_ref = store.objects.put(b"diff")
    pid = store.add_proposal(Proposal(id="", trigger="manual", diff_ref=diff_ref,
                                      candidates=[]))
    assert cli.main(["review"]) == 0
    assert "0 candidate(s)" in capsys.readouterr().out
    rc = cli.main(["review", "--approve", pid, "--name", "x"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "has no candidates" in out
    assert "resegment" in out


def test_review_unknown_proposal_is_friendly(git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    Store.init(git_repo)
    rc = cli.main(["review", "--approve", "prop_nope", "--name", "x"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "prop_nope" in out
    assert "pending --json" in out


def test_pending_and_resegment_roundtrip(git_repo, monkeypatch, capsys, tmp_path):
    import json
    from rgit.store.store import Store
    from rgit.store.models import Proposal
    monkeypatch.chdir(git_repo)
    store = Store.init(git_repo)
    diff_ref = store.objects.put(b"some diff text")
    pid = store.add_proposal(Proposal(id="", trigger="run", diff_ref=diff_ref,
                                      candidates=[{"name": "rough"}]))
    cli.main(["pending", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert out[0]["proposal_id"] == pid
    assert out[0]["diff"] == "some diff text"

    payload = tmp_path / "caps.json"
    refined = {"name": "refined", "intent": "better", "code_slices": [
        {"file": "model.py", "symbol": "forward", "anchor": None,
         "code": "x", "kind": "wrap"}]}
    payload.write_text(json.dumps([refined]))
    cli.main(["resegment", pid, "--from-json", str(payload)])
    assert store.get_proposal(pid).candidates == [refined]


def test_edges_apply_writes_overlaps_and_emits_pairs(git_repo, monkeypatch, capsys):
    import json
    from rgit.store.store import Store
    from rgit.store.models import Capsule, CodeSlice
    monkeypatch.chdir(git_repo)
    store = Store.init(git_repo)

    def cap(name, slices):
        return Capsule(id="", name=name, intent=f"{name}", status="approved",
                       base_commit="abc", knobs={}, data_assumptions=None,
                       resurrection_guide="...", result_summary=None, payload_hash=None,
                       code_slices=slices)
    a = store.add_feature(cap("a", [CodeSlice("m.py", "loss", None, "x", "wrap")]))
    b = store.add_feature(cap("b", [CodeSlice("m.py", "loss", None, "y", "wrap")]))
    cli.main(["edges", "--apply"])
    res = json.loads(capsys.readouterr().out)
    assert res["overlaps_written"] == 1
    assert b in store.neighbors(a, "overlaps")
    # the agent's worklist: the overlap pair is emitted for edge-judge to classify
    assert any({p["a"], p["b"]} == {a, b} for p in res["overlap_pairs"])


def test_edges_add_writes_depends_on(git_repo, monkeypatch, capsys):
    from rgit.store.store import Store
    monkeypatch.chdir(git_repo)
    store = Store.init(git_repo)
    cli.main(["edges", "--add", "depends_on", "feat_x", "feat_y"])
    assert "feat_y" in store.neighbors("feat_x", "depends_on")


def test_watch_once_stages_proposal(git_repo, monkeypatch, capsys):
    from rgit.store.store import Store
    monkeypatch.chdir(git_repo)
    Store.init(git_repo)
    (git_repo / "model.py").write_text("def forward(x):\n    return x + 1\n")
    cli.main(["watch", "--once"])
    assert "staged proposal" in capsys.readouterr().out


def test_watch_once_skip_notice_warns(git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    monkeypatch.setattr(cli, "_SEGMENTER", None)
    Store.init(git_repo)
    (git_repo / "large_notes.txt").write_text(
        "x" * (MAX_UNTRACKED_DIFF_BYTES + 1))
    assert cli.main(["watch", "--once"]) == 0
    out = capsys.readouterr().out
    assert "staged proposal" in out
    assert "warning: skipped 1 file(s)" in out
    assert "proposal has 0 candidates" in out


def test_pending_and_review_empty_state_message(git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    Store.init(git_repo)
    assert cli.main(["pending"]) == 0
    assert "no pending proposals" in capsys.readouterr().out
    assert cli.main(["review"]) == 0
    assert "no pending proposals" in capsys.readouterr().out


def _cap(store, name):
    return store.add_feature(Capsule(
        id="", name=name, intent="x", status="approved", base_commit="abc",
        knobs={}, data_assumptions=None, resurrection_guide=None,
        result_summary=None, payload_hash=None,
        code_slices=[CodeSlice("loss.py", "Loss", None, "c", "wrap")]))


def test_cli_metric_dir_set_and_list(git_repo, capsys, monkeypatch):
    monkeypatch.chdir(git_repo)
    Store.init(git_repo)
    assert main(["metric-dir", "set", "eval_loss", "lower"]) == 0
    capsys.readouterr()
    assert main(["metric-dir", "list"]) == 0
    assert "eval_loss" in capsys.readouterr().out


def test_cli_compare_prints_table(git_repo, capsys, monkeypatch):
    monkeypatch.chdir(git_repo)
    store = Store.init(git_repo)
    store.set_metric_direction("eval_loss", "lower")
    a = _cap(store, "temperature")
    rid = store.add_run(Run(id="", cmd="t", artifact_hash="h",
                            metrics={"eval_loss": 1.1}, base_commit="abc",
                            env=None, created_at="2026-01-01T00:00:00"))
    store.add_edge(a, rid, "produced")
    assert main(["compare", "temperature"]) == 0
    assert "temperature" in capsys.readouterr().out


def test_cli_compare_unknown_target_returns_nonzero(git_repo, capsys, monkeypatch):
    monkeypatch.chdir(git_repo)
    Store.init(git_repo)
    rc = main(["compare", "nope"])
    assert rc == 1
    assert "no capsule" in capsys.readouterr().out.lower()


def test_cli_provenance_prints_summary(git_repo, capsys, monkeypatch):
    import io, tarfile
    monkeypatch.chdir(git_repo)
    store = Store.init(git_repo)
    a = _cap(store, "loss")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        data = b"c"
        info = tarfile.TarInfo("loss.py"); info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    h = store.objects.put(buf.getvalue())
    rid = store.add_run(Run(id="", cmd="t", artifact_hash=h, metrics=None,
                            base_commit="abc", env=None, created_at="2026-01-01T00:00:00"))
    store.add_edge(a, rid, "produced")
    assert main(["provenance", rid]) == 0


def test_cli_ablation_unknown_capsule_returns_nonzero(git_repo, capsys, monkeypatch):
    monkeypatch.chdir(git_repo)
    Store.init(git_repo)
    rc = main(["ablation", "feat_nope"])
    assert rc == 1
    assert "no capsule" in capsys.readouterr().out.lower()


def test_cli_provenance_missing_artifact_returns_nonzero(git_repo, capsys, monkeypatch):
    monkeypatch.chdir(git_repo)
    store = Store.init(git_repo)
    rid = store.add_run(Run(id="", cmd="t", artifact_hash="deadbeef" * 8,
                            metrics=None, base_commit="abc", env=None,
                            created_at="2026-01-01T00:00:00"))
    rc = main(["provenance", rid])
    assert rc == 1
    assert "artifact unavailable" in capsys.readouterr().out.lower()


def test_install_hooks_subcommand(git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    assert cli.main(["install-hooks"]) == 0
    res = json.loads(capsys.readouterr().out)
    assert res["action"] == "installed"
    assert (git_repo / ".git" / "hooks" / "post-commit").exists()


def test_install_hooks_dry_run_writes_nothing(git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    assert cli.main(["install-hooks", "--dry-run"]) == 0
    res = json.loads(capsys.readouterr().out)
    assert res["action"] == "would_install"
    assert not (git_repo / ".git" / "hooks" / "post-commit").exists()


def test_install_hooks_uninstall(git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    cli.main(["install-hooks"])
    capsys.readouterr()
    assert cli.main(["install-hooks", "--uninstall"]) == 0
    res = json.loads(capsys.readouterr().out)
    assert res["action"] == "uninstalled"
    assert not (git_repo / ".git" / "hooks" / "post-commit").exists()


def test_cli_install_codex_dry_run_emits_guidance_json(tmp_path, monkeypatch, capsys):
    from rgit import agent_platforms, installer
    monkeypatch.setattr(agent_platforms, "home_dir", lambda: tmp_path)
    monkeypatch.setattr(installer, "_AGENTS_SKILLS_DIR",
                        tmp_path / ".agents" / "skills")

    assert cli.main(["install", "codex", "--dry-run",
                     "--guidance", "default"]) == 0

    res = json.loads(capsys.readouterr().out)
    assert res["platform"] == "codex"
    assert res["ran"] is False
    assert Path(res["guidance"]["path"]) == tmp_path / ".codex" / "AGENTS.md"
    assert "Current mode: default" in res["guidance"]["block"]


def test_cli_install_generic_dry_run_guidance_is_instruction_only(
        tmp_path, monkeypatch, capsys):
    from rgit import agent_platforms, installer
    monkeypatch.setattr(agent_platforms, "home_dir", lambda: tmp_path)
    monkeypatch.setattr(installer, "_AGENTS_SKILLS_DIR",
                        tmp_path / ".agents" / "skills")

    assert cli.main(["install", "generic", "--dry-run",
                     "--guidance", "default"]) == 0

    res = json.loads(capsys.readouterr().out)
    assert res["platform"] == "generic"
    assert res["guidance"]["action"] == "manual"
    assert "path" not in res["guidance"]
    assert "research-git is installed" in res["guidance"]["block"]


def test_cli_install_codex_guidance_none_flag_disables_guidance(
        tmp_path, monkeypatch, capsys):
    from rgit import agent_platforms, installer
    monkeypatch.setattr(agent_platforms, "home_dir", lambda: tmp_path)
    monkeypatch.setattr(installer, "_AGENTS_SKILLS_DIR",
                        tmp_path / ".agents" / "skills")

    assert cli.main(["install", "codex", "--dry-run", "--guidance", "none"]) == 0

    res = json.loads(capsys.readouterr().out)
    assert res["guidance"] == {"action": "disabled"}


def test_cli_install_prompts_for_mode_on_tty_without_flag(
        tmp_path, monkeypatch, capsys):
    from rgit import agent_platforms, installer
    monkeypatch.setattr(agent_platforms, "home_dir", lambda: tmp_path)
    monkeypatch.setattr(installer, "_AGENTS_SKILLS_DIR",
                        tmp_path / ".agents" / "skills")
    monkeypatch.setattr(cli, "_prompt_guidance_mode", lambda platform: "manual-only")

    assert cli.main(["install", "codex", "--dry-run"]) == 0

    res = json.loads(capsys.readouterr().out)
    assert "Current mode: manual-only" in res["guidance"]["block"]


class _TTYBuffer:
    def __init__(self):
        self.parts = []

    def write(self, text):
        self.parts.append(text)

    def flush(self):
        pass

    def isatty(self):
        return True

    def getvalue(self):
        return "".join(self.parts)


class _NotTTYBuffer:
    def write(self, text):
        pass

    def flush(self):
        pass

    def isatty(self):
        return False


def _allow_selector_ansi(monkeypatch):
    monkeypatch.setattr(cli, "_selector_ansi_supported", lambda stderr: True)


def test_guidance_numbered_prompt_rejects_blank_then_accepts_choice(monkeypatch):
    answers = iter(["", "2"])
    monkeypatch.setattr("builtins.input", lambda: next(answers))

    assert cli._prompt_guidance_mode_numbered("codex") == "manual-only"


def test_guidance_numbered_prompt_accepts_numbers_and_names(monkeypatch):
    cases = [
        ("1", "default"),
        ("2", "manual-only"),
        ("3", "none"),
        ("default", "default"),
        ("manual-only", "manual-only"),
        ("none", "none"),
    ]

    for answer, expected in cases:
        answers = iter([answer])
        monkeypatch.setattr("builtins.input", lambda: next(answers))
        assert cli._prompt_guidance_mode_numbered("codex") == expected


def test_guidance_numbered_prompt_retries_and_eof_cancels(monkeypatch):
    answers = iter(["nope", "3"])
    monkeypatch.setattr("builtins.input", lambda: next(answers))
    assert cli._prompt_guidance_mode_numbered("codex") == "none"

    monkeypatch.setattr("builtins.input", lambda: (_ for _ in ()).throw(EOFError))
    with pytest.raises(cli._GuidancePromptCancelled):
        cli._prompt_guidance_mode_numbered("codex")


def test_guidance_selector_defaults_to_default_on_enter(monkeypatch):
    err = _TTYBuffer()
    keys = iter(["enter"])
    _allow_selector_ansi(monkeypatch)
    monkeypatch.setattr(cli, "_read_prompt_key", lambda: next(keys))

    assert cli._prompt_guidance_mode_interactive("codex", stderr=err) == "default"
    assert "> default" in err.getvalue()
    assert "\x1b[7m> default" in err.getvalue()


def test_guidance_selector_moves_down_up_and_selects(monkeypatch):
    err = _TTYBuffer()
    keys = iter(["down", "up", "down", "enter"])
    _allow_selector_ansi(monkeypatch)
    monkeypatch.setattr(cli, "_read_prompt_key", lambda: next(keys))

    assert cli._prompt_guidance_mode_interactive("codex", stderr=err) == "manual-only"


def test_guidance_selector_accepts_numeric_shortcut(monkeypatch):
    err = _TTYBuffer()
    keys = iter(["3"])
    _allow_selector_ansi(monkeypatch)
    monkeypatch.setattr(cli, "_read_prompt_key", lambda: next(keys))

    assert cli._prompt_guidance_mode_interactive("codex", stderr=err) == "none"


def test_guidance_selector_ignores_unknown_keys_without_rerender(monkeypatch):
    err = _TTYBuffer()
    keys = iter(["other", "down", "enter"])
    _allow_selector_ansi(monkeypatch)
    monkeypatch.setattr(cli, "_read_prompt_key", lambda: next(keys))

    assert cli._prompt_guidance_mode_interactive("codex", stderr=err) == "manual-only"
    assert err.getvalue().count("research-git guidance for codex") == 2


def test_guidance_selector_redraw_does_not_add_leading_blank_lines(monkeypatch):
    err = _TTYBuffer()
    keys = iter(["down", "enter"])
    _allow_selector_ansi(monkeypatch)
    monkeypatch.setattr(cli, "_read_prompt_key", lambda: next(keys))

    assert cli._prompt_guidance_mode_interactive("codex", stderr=err) == "manual-only"
    assert "\x1b[7F\x1b[Jresearch-git guidance for codex" in err.getvalue()
    assert "\x1b[7F\x1b[J\nresearch-git guidance for codex" not in err.getvalue()


def test_guidance_selector_ctrl_c_exits(monkeypatch):
    err = _TTYBuffer()
    _allow_selector_ansi(monkeypatch)
    monkeypatch.setattr(cli, "_read_prompt_key", lambda: "ctrl-c")

    with pytest.raises(KeyboardInterrupt):
        cli._prompt_guidance_mode_interactive("codex", stderr=err)


def test_guidance_selector_unavailable_for_bad_terminal(monkeypatch):
    with pytest.raises(cli._InteractivePromptUnavailable):
        cli._prompt_guidance_mode_interactive("codex", stderr=_NotTTYBuffer())

    monkeypatch.setenv("TERM", "dumb")
    with pytest.raises(cli._InteractivePromptUnavailable):
        cli._prompt_guidance_mode_interactive("codex", stderr=_TTYBuffer())


def test_guidance_selector_falls_back_when_windows_ansi_unavailable(monkeypatch):
    calls = []
    monkeypatch.setattr(cli.os, "name", "nt", raising=False)
    monkeypatch.setattr(cli, "_enable_windows_virtual_terminal",
                        lambda stderr: False)
    monkeypatch.setattr(cli, "_prompt_guidance_mode_numbered",
                        lambda platform: calls.append(platform) or "default")

    assert cli._prompt_guidance_mode("codex") == "default"
    assert calls == ["codex"]


def test_guidance_selector_runs_when_windows_ansi_enabled(monkeypatch):
    err = _TTYBuffer()
    keys = iter(["2"])
    monkeypatch.setattr(cli.os, "name", "nt", raising=False)
    monkeypatch.setattr(cli, "_enable_windows_virtual_terminal",
                        lambda stderr: True)
    monkeypatch.setattr(cli, "_read_prompt_key", lambda: next(keys))

    assert cli._prompt_guidance_mode_interactive("codex", stderr=err) == "manual-only"
    assert "\x1b[7m> default" in err.getvalue()


def test_prompt_guidance_mode_falls_back_to_numbered(monkeypatch):
    calls = []

    def unavailable(platform):
        raise cli._InteractivePromptUnavailable

    monkeypatch.setattr(cli, "_prompt_guidance_mode_interactive", unavailable)
    monkeypatch.setattr(cli, "_prompt_guidance_mode_numbered",
                        lambda platform: calls.append(platform) or "manual-only")

    assert cli._prompt_guidance_mode("codex") == "manual-only"
    assert calls == ["codex"]


def test_decode_prompt_key_sequences():
    assert cli._decode_prompt_key("\r") == "enter"
    assert cli._decode_prompt_key("\n") == "enter"
    assert cli._decode_prompt_key("\x03") == "ctrl-c"
    assert cli._decode_prompt_key("1") == "1"
    assert cli._decode_prompt_key("2") == "2"
    assert cli._decode_prompt_key("3") == "3"
    assert cli._decode_prompt_key("\x1b[A") == "up"
    assert cli._decode_prompt_key("\x1b[B") == "down"
    assert cli._decode_prompt_key("x") == "other"


def test_read_prompt_key_dispatches_backend(monkeypatch):
    monkeypatch.setattr(cli.os, "name", "posix", raising=False)
    monkeypatch.setattr(cli, "_read_prompt_key_posix", lambda: "down")
    assert cli._read_prompt_key() == "down"

    monkeypatch.setattr(cli.os, "name", "nt", raising=False)
    monkeypatch.setattr(cli, "_read_prompt_key_windows", lambda: "up")
    assert cli._read_prompt_key() == "up"


def test_install_explicit_guidance_bypasses_prompt(monkeypatch, capsys):
    prompted = {"called": False}
    monkeypatch.setattr(cli, "_prompt_guidance_mode",
                        lambda platform: prompted.__setitem__("called", True) or "default")

    import rgit.installer as installer
    monkeypatch.setattr(installer, "install",
                        lambda platform, scope="user", dry_run=False, mode=None:
                        {"platform": platform, "mode": mode})

    assert cli.main(["install", "codex", "--guidance", "manual-only"]) == 0
    out = capsys.readouterr().out
    assert '"mode": "manual-only"' in out
    assert prompted["called"] is False


def test_install_stdout_remains_json_when_prompting(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_prompt_guidance_mode", lambda platform: "default")

    import rgit.installer as installer
    monkeypatch.setattr(installer, "install",
                        lambda platform, scope="user", dry_run=False, mode=None:
                        {"platform": platform, "mode": mode})

    assert cli.main(["install", "codex"]) == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data["mode"] == "default"
    assert captured.err == ""


def test_install_prompt_ctrl_c_exits_without_traceback(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_prompt_guidance_mode",
                        lambda platform: (_ for _ in ()).throw(KeyboardInterrupt))

    import rgit.installer as installer
    monkeypatch.setattr(installer, "install",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("install must not run after Ctrl+C")))

    assert cli.main(["install", "codex"]) == 130
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "install cancelled" in captured.err
    assert "Traceback" not in captured.err


def test_install_prompt_eof_cancels_without_running_installer(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_prompt_guidance_mode",
                        lambda platform: (_ for _ in ()).throw(
                            cli._GuidancePromptCancelled))

    import rgit.installer as installer
    monkeypatch.setattr(installer, "install",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("install must not run after prompt cancellation")))

    assert cli.main(["install", "codex"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "install cancelled: no guidance mode selected" in captured.err
    assert "run one of:" in captured.err
    assert "rgit install codex --guidance default" in captured.err
    assert "rgit install codex --guidance manual-only" in captured.err
    assert "rgit install codex --guidance none" in captured.err


def test_prompt_guidance_mode_maps_answers(monkeypatch):
    answers = iter(["2"])
    monkeypatch.setattr("builtins.input", lambda: next(answers))
    assert cli._prompt_guidance_mode("codex") == "manual-only"


def test_prompt_guidance_mode_blank_retries_and_garbage_retries(monkeypatch):
    answers = iter(["", "1"])
    monkeypatch.setattr("builtins.input", lambda: next(answers))
    assert cli._prompt_guidance_mode("codex") == "default"

    answers = iter(["nope", "3"])
    monkeypatch.setattr("builtins.input", lambda: next(answers))
    assert cli._prompt_guidance_mode("codex") == "none"


def test_cli_install_prompts_when_not_a_tty(
        tmp_path, monkeypatch, capsys):
    from rgit import agent_platforms, installer
    monkeypatch.setattr(agent_platforms, "home_dir", lambda: tmp_path)
    monkeypatch.setattr(installer, "_AGENTS_SKILLS_DIR",
                        tmp_path / ".agents" / "skills")
    monkeypatch.setattr(cli, "_prompt_guidance_mode", lambda platform: "manual-only")

    assert cli.main(["install", "codex", "--dry-run"]) == 0

    res = json.loads(capsys.readouterr().out)
    assert "Current mode: manual-only" in res["guidance"]["block"]


def test_cli_install_non_tty_numbered_input_selects_mode(
        tmp_path, monkeypatch, capsys):
    from rgit import agent_platforms, installer
    monkeypatch.setattr(agent_platforms, "home_dir", lambda: tmp_path)
    monkeypatch.setattr(installer, "_AGENTS_SKILLS_DIR",
                        tmp_path / ".agents" / "skills")
    monkeypatch.setattr(cli, "_prompt_guidance_mode_interactive",
                        lambda platform: (_ for _ in ()).throw(
                            cli._InteractivePromptUnavailable))
    answers = iter(["2"])
    monkeypatch.setattr("builtins.input", lambda: next(answers))

    assert cli.main(["install", "codex", "--dry-run"]) == 0

    captured = capsys.readouterr()
    res = json.loads(captured.out)
    assert "Current mode: manual-only" in res["guidance"]["block"]
    assert "Select [1-3]" in captured.err


def test_readonly_command_without_store_is_clean_error(git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)                 # git repo, but no `rgit init`
    assert cli.main(["features"]) == 1
    assert "no .rgit/" in capsys.readouterr().out


def test_run_without_store_suggests_init_flag(git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    assert cli.main(["run", "--", "true"]) == 1
    out = capsys.readouterr().out
    assert "no .rgit/" in out and "--init" in out


def test_run_with_init_flag_bootstraps_store(git_repo, monkeypatch):
    monkeypatch.chdir(git_repo)
    cli._SEGMENTER = MockSegmenter([])
    assert cli.main(["run", "--init", "--", "true"]) == 0
    assert (git_repo / ".rgit" / "graph.db").exists()
    assert not (git_repo / ".git" / "hooks" / "post-commit").exists()   # --init never installs hooks


def _seed_variant_pair(git_repo):
    store = Store.open(git_repo)
    from rgit.store.models import Capsule, CodeSlice
    def cap(name):
        return store.add_feature(Capsule(
            id="", name=name, intent="i", status="approved", base_commit="abc",
            knobs={}, data_assumptions=None, resurrection_guide=None,
            result_summary=None, payload_hash=None,
            code_slices=[CodeSlice("m.py", "f", None, "x", "wrap")]))
    a = cap("temp-0.7"); b = cap("temp-1.0")
    store.add_edge(b, a, "variant_of")


def test_graph_default_is_mermaid(git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    cli.main(["init"])
    _seed_variant_pair(git_repo)
    capsys.readouterr()                           # drop init/seed output
    assert cli.main(["graph"]) == 0               # no flag -> mermaid
    cap = capsys.readouterr()
    assert cap.out.startswith("graph LR") and "-->|variant_of|" in cap.out
    assert "mermaid.live" in cap.err             # tip on stderr, stdout stays pure


def test_graph_text_flag(git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    cli.main(["init"])
    _seed_variant_pair(git_repo)
    assert cli.main(["graph", "--text"]) == 0
    out = capsys.readouterr().out
    assert "temp-0.7" in out and "└─ temp-1.0" in out


def test_graph_dot_flag(git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    cli.main(["init"])
    assert cli.main(["graph", "--dot"]) == 0
    assert "digraph rgit {" in capsys.readouterr().out


def test_graph_without_store_is_clean_error(git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)                   # git repo, no `rgit init`
    assert cli.main(["graph"]) == 1
    assert "no .rgit/" in capsys.readouterr().out


def test_graph_mermaid_flag(git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    cli.main(["init"])
    assert cli.main(["graph", "--mermaid"]) == 0
    assert "graph LR" in capsys.readouterr().out


def test_graph_dot_and_mermaid_mutually_exclusive(git_repo, monkeypatch):
    import pytest
    monkeypatch.chdir(git_repo)
    cli.main(["init"])
    with pytest.raises(SystemExit):
        cli.main(["graph", "--dot", "--mermaid"])


def test_resegment_unknown_proposal_id_errors(git_repo, monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(git_repo)
    cli.main(["init"])
    jf = tmp_path / "cands.json"
    jf.write_text("[]", encoding="utf-8")
    rc = cli.main(["resegment", "prop_does_not_exist", "--from-json", str(jf)])
    assert rc == 1
    assert "prop_does_not_exist" in capsys.readouterr().out


def test_resegment_rejects_malformed_candidate(git_repo, monkeypatch, tmp_path, capsys):
    from rgit.segmenter import segment_diff
    monkeypatch.chdir(git_repo)
    cli.main(["init"])
    store = Store.open(git_repo)
    (git_repo / "model.py").write_text("def forward(x):\n    return x*2\n")
    good = {"name": "keep", "intent": "i", "code_slices": [
        {"file": "model.py", "symbol": "forward", "anchor": None,
         "code": "x", "kind": "wrap"}]}
    pid = segment_diff(store, "manual", MockSegmenter([good]), None)
    jf = tmp_path / "bad.json"
    jf.write_text('[{"intent": "i", "code_slices": []}]', encoding="utf-8")  # missing name
    rc = cli.main(["resegment", pid, "--from-json", str(jf)])
    assert rc == 1
    assert "name" in capsys.readouterr().out.lower()
    assert Store.open(git_repo).get_proposal(pid).candidates[0]["name"] == "keep"  # untouched


# ---- committed-diff capture (issue #20) ------------------------------------

def _commit_all(repo, msg):
    import subprocess
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True,
                   capture_output=True)
    subprocess.run(["git", "commit", "-q", "-m", msg], cwd=repo, check=True,
                   capture_output=True)


def test_capture_trigger_commit_defaults_to_head_commit(git_repo, monkeypatch, capsys):
    # what an already-installed post-commit hook runs: after the commit the
    # worktree is clean, yet the commit's own diff must be captured (issue #20)
    from rgit.gitutil import current_commit
    monkeypatch.chdir(git_repo)
    monkeypatch.setattr(cli, "_SEGMENTER", None)
    Store.init(git_repo)
    (git_repo / "model.py").write_text("def forward(x):\n    return x * 2\n")
    _commit_all(git_repo, "double")
    assert cli.main(["capture", "--trigger", "commit"]) == 0
    assert "proposal" in capsys.readouterr().out
    props = Store.open(git_repo).list_proposals("open")
    assert len(props) == 1
    store = Store.open(git_repo)
    assert "x * 2" in store.objects.get(props[0].diff_ref).decode()
    assert props[0].source_commit == current_commit(git_repo)


def test_capture_explicit_commit_flag_with_clean_worktree(git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    monkeypatch.setattr(cli, "_SEGMENTER", None)
    Store.init(git_repo)
    (git_repo / "model.py").write_text("def forward(x):\n    return x * 3\n")
    _commit_all(git_repo, "triple")
    assert cli.main(["capture", "--commit", "HEAD"]) == 0
    props = Store.open(git_repo).list_proposals("open")
    assert len(props) == 1 and props[0].trigger == "manual"


def test_capture_bare_commit_flag_means_head(git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    monkeypatch.setattr(cli, "_SEGMENTER", None)
    Store.init(git_repo)
    (git_repo / "model.py").write_text("def forward(x):\n    return x * 5\n")
    _commit_all(git_repo, "quint")
    assert cli.main(["capture", "--commit"]) == 0
    assert len(Store.open(git_repo).list_proposals("open")) == 1


def test_capture_commit_flag_ignores_dirty_worktree(git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    monkeypatch.setattr(cli, "_SEGMENTER", None)
    Store.init(git_repo)
    (git_repo / "model.py").write_text("def forward(x):\n    return x * 2\n")
    _commit_all(git_repo, "double")
    (git_repo / "scratch.py").write_text("SCRATCH = 1\n")   # uncommitted noise
    assert cli.main(["capture", "--commit", "HEAD"]) == 0
    store = Store.open(git_repo)
    diff = store.objects.get(store.list_proposals("open")[0].diff_ref).decode()
    assert "x * 2" in diff and "SCRATCH" not in diff


def test_capture_default_worktree_behavior_unchanged(git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    monkeypatch.setattr(cli, "_SEGMENTER", None)
    Store.init(git_repo)
    (git_repo / "model.py").write_text("def forward(x):\n    return x + 7\n")
    assert cli.main(["capture", "--trigger", "manual"]) == 0
    store = Store.open(git_repo)
    props = store.list_proposals("open")
    assert len(props) == 1 and props[0].source_commit is None
    assert "x + 7" in store.objects.get(props[0].diff_ref).decode()


def test_capture_bad_commit_ref_fails_cleanly(git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    Store.init(git_repo)
    assert cli.main(["capture", "--commit", "no-such-ref"]) == 1
    out = capsys.readouterr().out
    assert "no-such-ref" in out and "Traceback" not in out
    assert Store.open(git_repo).list_proposals("open") == []


def test_capture_range_spans_commits(git_repo, monkeypatch, capsys):
    from rgit.gitutil import current_commit
    monkeypatch.chdir(git_repo)
    monkeypatch.setattr(cli, "_SEGMENTER", None)
    Store.init(git_repo)
    base = current_commit(git_repo)
    (git_repo / "model.py").write_text("def forward(x):\n    return x * 2\n")
    _commit_all(git_repo, "double")
    (git_repo / "extra.py").write_text("def extra():\n    return 1\n")
    _commit_all(git_repo, "extra")
    assert cli.main(["capture", "--range", f"{base}..HEAD"]) == 0
    store = Store.open(git_repo)
    diff = store.objects.get(store.list_proposals("open")[0].diff_ref).decode()
    assert "x * 2" in diff and "extra.py" in diff


def test_capture_range_requires_dots(git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    Store.init(git_repo)
    assert cli.main(["capture", "--range", "main"]) == 1
    out = capsys.readouterr().out
    assert "A..B" in out and "Traceback" not in out


def test_capture_commit_and_range_are_mutually_exclusive(git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    Store.init(git_repo)
    with pytest.raises(SystemExit):
        cli.main(["capture", "--commit", "HEAD", "--range", "a..b"])


def test_capture_commit_no_diff_names_the_commit(git_repo, monkeypatch, capsys):
    # merge commits produce no diff-tree patch: say so instead of the
    # misleading "working tree has no diff"
    import subprocess
    monkeypatch.chdir(git_repo)
    Store.init(git_repo)
    subprocess.run(["git", "checkout", "-q", "-b", "side"], cwd=git_repo,
                   check=True, capture_output=True)
    (git_repo / "side.py").write_text("SIDE = 1\n")
    _commit_all(git_repo, "side")
    subprocess.run(["git", "checkout", "-q", "-"], cwd=git_repo, check=True,
                   capture_output=True)
    (git_repo / "main.py").write_text("MAIN = 1\n")
    _commit_all(git_repo, "main")
    subprocess.run(["git", "merge", "-q", "--no-ff", "-m", "merge", "side"],
                   cwd=git_repo, check=True, capture_output=True)
    assert cli.main(["capture", "--commit", "HEAD"]) == 0
    out = capsys.readouterr().out
    assert "nothing to capture" in out and "commit" in out
    assert "working tree" not in out


def test_pending_json_includes_source_commit(git_repo, monkeypatch, capsys):
    from rgit.gitutil import current_commit
    monkeypatch.chdir(git_repo)
    monkeypatch.setattr(cli, "_SEGMENTER", None)
    Store.init(git_repo)
    (git_repo / "model.py").write_text("def forward(x):\n    return x * 2\n")
    _commit_all(git_repo, "double")
    assert cli.main(["capture", "--trigger", "commit"]) == 0
    capsys.readouterr()
    assert cli.main(["pending", "--json"]) == 0
    items = json.loads(capsys.readouterr().out)
    assert items[0]["source_commit"] == current_commit(git_repo)


def test_capture_same_diff_twice_reports_existing(git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    monkeypatch.setattr(cli, "_SEGMENTER", None)
    Store.init(git_repo)
    (git_repo / "model.py").write_text("def forward(x):\n    return x * 2\n")
    _commit_all(git_repo, "double")
    assert cli.main(["capture", "--commit", "HEAD"]) == 0
    capsys.readouterr()
    assert cli.main(["capture", "--commit", "HEAD"]) == 0
    out = capsys.readouterr().out
    assert "already exists" in out
    assert len(Store.open(git_repo).list_proposals("open")) == 1


def test_capture_worktree_flag_overrides_commit_trigger_default(git_repo, monkeypatch, capsys):
    # explicit --worktree must beat the `--trigger commit` commit-diff default
    monkeypatch.chdir(git_repo)
    monkeypatch.setattr(cli, "_SEGMENTER", None)
    Store.init(git_repo)
    (git_repo / "model.py").write_text("def forward(x):\n    return x * 2\n")
    _commit_all(git_repo, "double")
    (git_repo / "scratch.py").write_text("SCRATCH = 1\n")
    assert cli.main(["capture", "--trigger", "commit", "--worktree"]) == 0
    store = Store.open(git_repo)
    props = store.list_proposals("open")
    assert len(props) == 1 and props[0].source_commit is None
    diff = store.objects.get(props[0].diff_ref).decode()
    assert "SCRATCH" in diff and "x * 2" not in diff


# ---- zero-choice capture (auto source + positional) -------------------------

def test_capture_auto_dirty_tree_captures_worktree(git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    monkeypatch.setattr(cli, "_SEGMENTER", None)
    Store.init(git_repo)
    (git_repo / "model.py").write_text("def forward(x):\n    return x + 1\n")
    assert cli.main(["capture"]) == 0
    props = Store.open(git_repo).list_proposals("open")
    assert len(props) == 1 and props[0].source_commit is None


def test_capture_auto_clean_tree_captures_head_with_note(git_repo, monkeypatch, capsys):
    from rgit.gitutil import current_commit
    monkeypatch.chdir(git_repo)
    monkeypatch.setattr(cli, "_SEGMENTER", None)
    Store.init(git_repo)
    (git_repo / "model.py").write_text("def forward(x):\n    return x * 2\n")
    _commit_all(git_repo, "double it")
    assert cli.main(["capture"]) == 0
    out = capsys.readouterr().out
    assert "capturing last commit" in out and "double it" in out
    props = Store.open(git_repo).list_proposals("open")
    assert len(props) == 1
    assert props[0].source_commit == current_commit(git_repo)


def test_capture_auto_repeat_reports_existing(git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    monkeypatch.setattr(cli, "_SEGMENTER", None)
    Store.init(git_repo)
    (git_repo / "model.py").write_text("def forward(x):\n    return x * 2\n")
    _commit_all(git_repo, "double")
    assert cli.main(["capture"]) == 0
    capsys.readouterr()
    assert cli.main(["capture"]) == 0
    assert "already exists" in capsys.readouterr().out
    assert len(Store.open(git_repo).list_proposals("open")) == 1


def test_capture_positional_commit_and_range(git_repo, monkeypatch, capsys):
    from rgit.gitutil import current_commit
    monkeypatch.chdir(git_repo)
    monkeypatch.setattr(cli, "_SEGMENTER", None)
    Store.init(git_repo)
    base = current_commit(git_repo)
    (git_repo / "model.py").write_text("def forward(x):\n    return x * 2\n")
    _commit_all(git_repo, "double")
    assert cli.main(["capture", "HEAD"]) == 0
    assert cli.main(["capture", f"{base}..HEAD"]) == 0
    props = Store.open(git_repo).list_proposals("open")
    assert len(props) >= 1


def test_capture_positional_conflicts_with_legacy_flags(git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    Store.init(git_repo)
    assert cli.main(["capture", "HEAD", "--worktree"]) == 1
    assert "not both" in capsys.readouterr().out


def test_capture_help_hides_legacy_source_flags(capsys):
    with pytest.raises(SystemExit):
        cli.main(["capture", "--help"])
    out = capsys.readouterr().out
    assert "--commit" not in out and "--range" not in out and "--worktree" not in out
    assert "REV|A..B" in out


# ---- zero-choice review (id-free actions) -----------------------------------

def _stage_one_proposal(git_repo, text="def forward(x):\n    return x + 5\n"):
    (git_repo / "model.py").write_text(text)
    assert cli.main(["capture", "--worktree"]) == 0


def test_review_approve_without_id_takes_sole_open_proposal(git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    monkeypatch.setattr(cli, "_SEGMENTER", None)
    Store.init(git_repo)
    _stage_one_proposal(git_repo)
    capsys.readouterr()
    assert cli.main(["review", "--approve"]) == 0
    assert "approved -> feature" in capsys.readouterr().out
    assert Store.open(git_repo).list_proposals("open") == []


def test_review_approve_without_id_no_proposals(git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    Store.init(git_repo)
    assert cli.main(["review", "--approve"]) == 1
    assert "no pending proposals" in capsys.readouterr().out


def test_review_approve_without_id_ambiguous_lists_candidates(git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    monkeypatch.setattr(cli, "_SEGMENTER", None)
    Store.init(git_repo)
    _stage_one_proposal(git_repo)
    (git_repo / "other.py").write_text("def other():\n    return 1\n")
    assert cli.main(["capture", "--worktree"]) == 0
    capsys.readouterr()
    assert cli.main(["review", "--approve"]) == 1
    out = capsys.readouterr().out
    assert "several proposals are open" in out and out.count("prop_") >= 2


def test_review_dismiss_without_id_takes_sole_open_proposal(git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    monkeypatch.setattr(cli, "_SEGMENTER", None)
    Store.init(git_repo)
    _stage_one_proposal(git_repo)
    capsys.readouterr()
    assert cli.main(["review", "--dismiss"]) == 0
    assert "dismissed" in capsys.readouterr().out
    assert Store.open(git_repo).list_proposals("open") == []
