import io
import json
import sys
from pathlib import Path

import pytest
import rgit.cli as cli
from conftest import commit_file, make_candidate
from rgit.cli import main
from rgit.gitutil import MAX_UNTRACKED_DIFF_BYTES
from rgit.segmenter import MockSegmenter, segment_diff
from rgit.store.store import Store
from rgit.store.models import Capsule, CodeSlice, Run

T0 = 1_700_000_000


class _FakeTTY(io.StringIO):
    def isatty(self):
        return True


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


def _skill_link_failure_result():
    return {
        "platform": "codex",
        "links": [{"link": "/home/.agents/skills/rgit-capture",
                   "target": "/pkg/rgit/_plugin/skills/rgit-capture"}],
        "skills_dir": "/home/.agents/skills",
        "errors": [{"link": "/home/.agents/skills/rgit-capture",
                    "error": "privilege not held",
                    "hint": "enable Developer Mode"}],
        "guidance": {"action": "skipped_error",
                     "path": "/home/.codex/AGENTS.md",
                     "error": "skill symlink failed"},
        "instructions": "Skills symlinked into /home/.agents/skills. MCP config: {}",
        "ran": True,
    }


def test_install_returns_nonzero_when_skill_links_fail(monkeypatch, capsys):
    from rgit import installer

    monkeypatch.setattr(installer, "install",
                        lambda *a, **k: _skill_link_failure_result())

    assert cli.main(["install", "codex", "--guidance", "default"]) == 1
    captured = capsys.readouterr()
    assert "privilege not held" in captured.out
    assert "skill symlink failed" in captured.out
    assert "Skills symlinked into" not in captured.out
    assert "restart your CLI/agent session" not in captured.out


def test_install_json_returns_nonzero_when_skill_links_fail(monkeypatch, capsys):
    from rgit import installer

    monkeypatch.setattr(installer, "install",
                        lambda *a, **k: _skill_link_failure_result())

    assert cli.main(["install", "codex", "--json", "--guidance", "default"]) == 1
    res = json.loads(capsys.readouterr().out)
    assert res["errors"][0]["error"] == "privilege not held"


def test_install_returns_nonzero_when_guidance_write_fails(monkeypatch, capsys):
    from rgit import installer

    monkeypatch.setattr(
        installer, "install",
        lambda *a, **k: {
            "platform": "codex",
            "links": [{"link": "/home/.agents/skills/rgit-capture",
                       "target": "/pkg/rgit/_plugin/skills/rgit-capture"}],
            "skills_dir": "/home/.agents/skills",
            "guidance": {"action": "skipped_error",
                         "path": "/home/.codex/AGENTS.md",
                         "error": "permission denied"},
            "ran": True,
        },
    )

    assert cli.main(["install", "codex", "--guidance", "default"]) == 1
    captured = capsys.readouterr()
    assert "permission denied" in captured.out
    assert "restart your CLI/agent session" not in captured.out


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

    assert cli.main(["install", "codex", "--dry-run", "--json",
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

    assert cli.main(["install", "generic", "--dry-run", "--json",
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

    assert cli.main(["install", "codex", "--dry-run", "--json", "--guidance", "none"]) == 0

    res = json.loads(capsys.readouterr().out)
    assert res["guidance"] == {"action": "disabled"}


def test_cli_install_prompts_for_mode_on_tty_without_flag(
        tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", _FakeTTYStdin())
    from rgit import agent_platforms, installer
    monkeypatch.setattr(agent_platforms, "home_dir", lambda: tmp_path)
    monkeypatch.setattr(installer, "_AGENTS_SKILLS_DIR",
                        tmp_path / ".agents" / "skills")
    monkeypatch.setattr(cli, "_prompt_guidance_mode", lambda platform: "manual-only")

    assert cli.main(["install", "codex", "--dry-run", "--json"]) == 0

    res = json.loads(capsys.readouterr().out)
    assert "Current mode: manual-only" in res["guidance"]["block"]


class _FakeTTYStdin:
    def isatty(self):
        return True


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
                        lambda platform, scope="user", dry_run=False, mode=None, conservative=False:
                        {"platform": platform, "mode": mode})

    assert cli.main(["install", "codex", "--json", "--guidance", "manual-only"]) == 0
    out = capsys.readouterr().out
    assert '"mode": "manual-only"' in out
    assert prompted["called"] is False


def test_install_stdout_remains_json_when_prompting(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", _FakeTTYStdin())
    monkeypatch.setattr(cli, "_prompt_guidance_mode", lambda platform: "default")

    import rgit.installer as installer
    monkeypatch.setattr(installer, "install",
                        lambda platform, scope="user", dry_run=False, mode=None, conservative=False:
                        {"platform": platform, "mode": mode})

    assert cli.main(["install", "codex", "--json"]) == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data["mode"] == "default"
    assert captured.err == ""


def test_install_prompt_ctrl_c_exits_without_traceback(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", _FakeTTYStdin())
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
    monkeypatch.setattr(sys, "stdin", _FakeTTYStdin())
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


def test_cli_install_non_tty_defaults_guidance_with_notice(
        tmp_path, monkeypatch, capsys):
    # Automation must succeed on the first try: no prompt, no homework —
    # keep/pin default guidance and say so on stderr (reverses part of #19).
    from rgit import agent_platforms, installer
    monkeypatch.setattr(agent_platforms, "home_dir", lambda: tmp_path)
    monkeypatch.setattr(installer, "_AGENTS_SKILLS_DIR",
                        tmp_path / ".agents" / "skills")

    assert cli.main(["install", "codex", "--dry-run", "--json"]) == 0

    captured = capsys.readouterr()
    res = json.loads(captured.out)
    assert "Current mode: default" in res["guidance"]["block"]
    assert "guidance mode: default" in captured.err
    assert "Select [1-3]" not in captured.err


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
    assert cli.main(["run", "--init", "--", sys.executable, "-c", "pass"]) == 0
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


# ---- zero-choice install (auto-detect platforms) -----------------------------

def test_bare_install_without_detection_non_tty_lists_platforms(monkeypatch, capsys):
    import io
    from rgit import installer
    monkeypatch.setattr(installer, "detect_platforms", lambda: [])
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    assert cli.main(["install"]) == 1
    out = capsys.readouterr().out
    assert "no agent client detected" in out and "claude-code" in out


def test_bare_install_fans_out_to_detected_platforms(monkeypatch, capsys):
    from rgit import installer
    calls = []
    monkeypatch.setattr(installer, "detect_platforms", lambda: ["codex", "gemini"])
    monkeypatch.setattr(
        installer, "install",
        lambda p, *, scope, dry_run, mode, conservative=False: (
            calls.append((p, mode)) or {"platform": p, "ran": True}))
    assert cli.main(["install", "--guidance", "default"]) == 0
    assert calls == [("codex", "default"), ("gemini", "default")]
    err = capsys.readouterr().err
    assert "detected: codex, gemini" in err


def test_bare_uninstall_fans_out_to_detected_platforms(monkeypatch, capsys):
    from rgit import installer
    calls = []
    monkeypatch.setattr(installer, "detect_platforms", lambda: ["codex"])
    monkeypatch.setattr(
        installer, "uninstall",
        lambda p, *, scope, dry_run, mode: (calls.append(p)
                                            or {"platform": p, "ran": True}))
    assert cli.main(["install", "--uninstall"]) == 0
    assert calls == ["codex"]


def _canned_agents_result(platform="codex"):
    return {"platform": platform,
            "links": [{"link": f"/tmp/skills/rgit-capture", "target": "/plug"}],
            "skills_dir": "/tmp/skills",
            "mcp_config": {"mcpServers": {"research-git": {"command": "rgit"}}},
            "instructions": "add this server to ~/.codex/config.toml",
            "guidance": {"action": "updated", "path": "/home/AGENTS.md",
                         "reload": "restart"},
            "ran": True}


def test_install_prints_human_lines_by_default(monkeypatch, capsys):
    from rgit import installer
    monkeypatch.setattr(installer, "install",
                        lambda p, *, scope, dry_run, mode, conservative=False: _canned_agents_result(p))
    assert cli.main(["install", "codex", "--guidance", "none"]) == 0
    out = capsys.readouterr().out
    assert "✓" in out
    assert "skills linked" in out and "/tmp/skills" in out
    assert "guidance updated" in out and "/home/AGENTS.md" in out
    assert "restart" in out
    assert "install-hooks" in out
    assert not out.lstrip().startswith("{")


def test_install_json_flag_prints_todays_document(monkeypatch, capsys):
    from rgit import installer
    canned = _canned_agents_result()
    monkeypatch.setattr(installer, "install",
                        lambda p, *, scope, dry_run, mode, conservative=False: canned)
    assert cli.main(["install", "codex", "--json", "--guidance", "none"]) == 0
    out = capsys.readouterr().out
    assert json.loads(out) == canned


def test_install_help_hides_plumbing_flags(capsys):
    with pytest.raises(SystemExit):
        cli.main(["install", "--help"])
    out = capsys.readouterr().out
    for hidden in ("--guidance", "--scope", "--dry-run", "--json"):
        assert hidden not in out
    assert "--uninstall" in out and "--list" in out


# ---- git-style misuse hints --------------------------------------------------

def test_unknown_subcommand_suggests_closest(capsys):
    with pytest.raises(SystemExit) as ei:
        cli.main(["captur"])
    assert ei.value.code == 2
    err = capsys.readouterr().err
    assert "did you mean" in err and "capture" in err


def test_unknown_subcommand_without_close_match_has_no_hint(capsys):
    with pytest.raises(SystemExit) as ei:
        cli.main(["frobnicate"])
    assert ei.value.code == 2
    assert "did you mean" not in capsys.readouterr().err


def test_install_unknown_platform_suggests_closest(capsys):
    assert cli.main(["install", "codx", "--guidance", "none"]) == 1
    out = capsys.readouterr().out
    assert "unknown platform" in out
    assert "did you mean 'codex'" in out
    assert "Traceback" not in out


def test_capture_bad_ref_prints_hint(git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    Store.init(git_repo)
    assert cli.main(["capture", "no-such-ref"]) == 1
    out = capsys.readouterr().out
    assert "cannot resolve" in out
    assert "hint:" in out and "git log --oneline" in out


def test_review_dismiss_unknown_id_prints_hint(git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    Store.init(git_repo)
    assert cli.main(["review", "--dismiss", "prop_nope"]) == 1
    out = capsys.readouterr().out
    assert "prop_nope" in out
    assert "hint:" in out and "rgit review" in out


def _seed_three_candidates(git_repo):
    store = Store.init(git_repo)
    (git_repo / "model.py").write_text("def forward(x):\n    return x*2\n")
    # segment_diff returns a CaptureResult, a str subclass that IS the proposal id
    pid = segment_diff(store, "manual",
                       MockSegmenter([make_candidate("rerank"),
                                      make_candidate("cache"),
                                      make_candidate("logging")]), None)
    return store, pid


def test_review_decide_keeps_and_drops(git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    store, pid = _seed_three_candidates(git_repo)
    assert cli.main(["review", "--decide", pid, "--keep", "rerank,cache"]) == 0
    out = capsys.readouterr().out
    assert out.count("approved -> ") == 2
    assert "rerank" in out and "cache" in out
    assert "dropped" in out and "logging" in out
    assert f"proposal {pid} resolved" in out
    assert {c.name for c in store.list_features()} == {"rerank", "cache"}


def test_review_decide_keep_accumulates_across_repeats(git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    store, pid = _seed_three_candidates(git_repo)
    # repeated --keep must accumulate, not last-win
    assert cli.main(["review", "--decide", pid,
                     "--keep", "rerank", "--keep", "cache"]) == 0
    assert {c.name for c in store.list_features()} == {"rerank", "cache"}


def test_review_decide_defaults_to_sole_open_proposal(git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    store, pid = _seed_three_candidates(git_repo)
    assert cli.main(["review", "--decide", "--keep", "rerank"]) == 0
    out = capsys.readouterr().out
    assert f"proposal {pid} resolved" in out
    assert {c.name for c in store.list_features()} == {"rerank"}


def test_review_decide_requires_keep(git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    _seed_three_candidates(git_repo)
    assert cli.main(["review", "--decide"]) == 1
    out = capsys.readouterr().out
    assert "--keep" in out and "--dismiss" in out


def test_review_decide_explicit_empty_id_is_rejected(git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    store, pid = _seed_three_candidates(git_repo)
    # an explicit empty id must not silently fall back to the sole proposal
    assert cli.main(["review", "--decide", "", "--keep", "x"]) == 1
    out = capsys.readouterr().out
    assert "empty PROPOSAL_ID" in out
    assert store.get_proposal(pid).status == "open"   # untouched


def test_review_decide_unknown_id_prints_review_hint(git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    Store.init(git_repo)
    assert cli.main(["review", "--decide", "prop_nope", "--keep", "x"]) == 1
    out = capsys.readouterr().out
    assert "prop_nope" in out
    assert "rgit review" in out
    assert "resegment" not in out                 # id error, not a name error


def test_review_flag_abbreviation_is_rejected(git_repo, monkeypatch):
    monkeypatch.chdir(git_repo)
    Store.init(git_repo)
    # --d is ambiguous between --dismiss and --decide; abbreviations are disabled
    with pytest.raises(SystemExit):
        cli.main(["review", "--d", "x"])


def test_review_full_flag_names_still_parse(git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    Store.init(git_repo)
    # the full names remain valid (here they resolve no proposal → exit 1, not 2)
    assert cli.main(["review", "--dismiss", "prop_nope"]) == 1
    capsys.readouterr()
    assert cli.main(["review", "--decide", "prop_nope", "--keep", "x"]) == 1


def test_review_modes_are_mutually_exclusive(git_repo, monkeypatch):
    monkeypatch.chdir(git_repo)
    Store.init(git_repo)
    with pytest.raises(SystemExit):
        cli.main(["review", "--approve", "X", "--decide", "Y"])


def test_review_keep_without_decide_is_an_error(git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    Store.init(git_repo)
    assert cli.main(["review", "--keep", "a"]) == 1
    assert "--decide" in capsys.readouterr().out


def test_review_decide_unknown_name_fails_with_hint(git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    store, pid = _seed_three_candidates(git_repo)
    assert cli.main(["review", "--decide", pid, "--keep", "nope"]) == 1
    out = capsys.readouterr().out
    assert "nope" in out and "rgit pending --json" in out
    assert store.list_features() == []
    assert store.get_proposal(pid).status == "open"


def test_edges_apply_scope_and_limit(git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    cli.main(["init"])
    capsys.readouterr()  # drain init's informational stdout so it stays out of the JSON parse
    store = Store.open(git_repo)
    a = store.add_feature(Capsule(
        id="", name="edge-a", intent="i", status="approved", base_commit="c",
        knobs={}, data_assumptions=None, resurrection_guide=None,
        result_summary=None, payload_hash=None,
        code_slices=[CodeSlice("m.py", "s", None, "x", "wrap")]))
    store.add_feature(Capsule(
        id="", name="edge-b", intent="i", status="approved", base_commit="c",
        knobs={}, data_assumptions=None, resurrection_guide=None,
        result_summary=None, payload_hash=None,
        code_slices=[CodeSlice("m.py", "s", None, "y", "wrap")]))
    assert cli.main(["edges", "--apply", "--scope", "edge-a", "--limit", "5"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["overlaps_written"] == 1
    assert cli.main(["edges", "--apply", "--scope", "no-such-capsule"]) == 1


def test_digest_scan_status_next_accept_roundtrip(git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    commit_file(git_repo, "feat.py", "def f():\n    return 1\n", "a feature",
                when=T0)
    assert cli.main(["init"]) == 0
    capsys.readouterr()

    assert cli.main(["digest", "scan", "--json"]) == 0
    scanned = json.loads(capsys.readouterr().out)
    assert scanned["units_total"] >= 1 and scanned["mode"] == "layered"

    assert cli.main(["digest", "status", "--json"]) == 0
    st = json.loads(capsys.readouterr().out)
    assert st["pending_in_mode"] >= 1

    assert cli.main(["digest", "next", "--batch", "1", "--json"]) == 0
    items = json.loads(capsys.readouterr().out)
    assert items and items[0]["proposal_id"].startswith("prop_")

    pid = items[0]["proposal_id"]
    payload = json.dumps([make_candidate("backfilled-feature")])
    monkeypatch.setattr(sys, "stdin", __import__("io").StringIO(payload))
    assert cli.main(["resegment", pid, "--from-json", "-"]) == 0
    capsys.readouterr()

    assert cli.main(["digest", "accept", pid]) == 0
    out = capsys.readouterr().out
    assert "approved ->" in out and "[backfill]" in out
    store = Store.open(git_repo)
    caps = [c for c in store.list_features() if c.origin == "backfill"]
    assert len(caps) == 1 and caps[0].name == "backfilled-feature"

    assert cli.main(["digest", "accept", pid]) == 1        # already resolved
    capsys.readouterr()
    assert cli.main(["digest", "clear"]) == 0
    assert "removed" in capsys.readouterr().out


def test_digest_scan_unknown_mode_fails(git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    cli.main(["init"])
    capsys.readouterr()
    import pytest as _pytest
    with _pytest.raises(SystemExit):                       # argparse choices
        cli.main(["digest", "scan", "--mode", "bogus"])


def test_backfill_proposals_hidden_from_live_surfaces(git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    commit_file(git_repo, "feat.py", "def f():\n    return 1\n", "a feature",
                when=T0)
    cli.main(["init"])
    cli.main(["digest", "scan"])
    cli.main(["digest", "next", "--batch", "1"])
    capsys.readouterr()
    assert cli.main(["pending"]) == 0
    assert "no pending proposals" in capsys.readouterr().out
    assert cli.main(["review"]) == 0
    assert "no pending proposals" in capsys.readouterr().out
    # bare --approve must not resolve to a backfill proposal either
    assert cli.main(["review", "--approve"]) == 1
    assert "no pending proposals" in capsys.readouterr().out


def test_features_tags_backfill(git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    cli.main(["init"])
    store = Store.open(git_repo)
    store.add_feature(Capsule(
        id="", name="bf", intent="i", status="approved", base_commit="c",
        knobs={}, data_assumptions=None, resurrection_guide=None,
        result_summary=None, payload_hash=None,
        code_slices=[CodeSlice("m.py", "f", None, "x", "wrap")],
        origin="backfill"))
    capsys.readouterr()
    assert cli.main(["features"]) == 0
    assert "[backfill]" in capsys.readouterr().out


# ---- init history-digestion offer (Task 8) ----------------------------------

def test_init_non_tty_prints_digest_hint_with_history(git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    commit_file(git_repo, "a.py", "x = 1\n", "second commit", when=T0)
    assert cli.main(["init"]) == 0
    out = capsys.readouterr().out
    assert "rgit digest scan" in out
    store = Store.open(git_repo)
    assert store.list_digest_units() == []                 # hint only, no scan


def test_init_single_commit_repo_stays_quiet(git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    assert cli.main(["init"]) == 0
    assert "digest" not in capsys.readouterr().out


def test_init_no_digest_flag_suppresses_offer(git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    commit_file(git_repo, "a.py", "x = 1\n", "second commit", when=T0)
    assert cli.main(["init", "--no-digest"]) == 0
    out = capsys.readouterr().out
    # offer fully suppressed — check the offer's own strings, not a bare
    # "digest" (the pytest tmp dir is named after this test, so the printed
    # `initialized .rgit/ in <path>` line legitimately contains "digest").
    assert "rgit digest scan" not in out    # non-TTY hint suppressed
    assert "digest plan" not in out         # no scan happened


def test_init_digest_flag_scans_non_interactively(git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    commit_file(git_repo, "a.py", "x = 1\n", "second commit", when=T0)
    assert cli.main(["init", "--digest", "dead"]) == 0
    out = capsys.readouterr().out
    assert "digest plan" in out
    store = Store.open(git_repo)
    assert store.get_digest_meta("mode") == "dead"
    assert store.list_digest_units()


def test_init_tty_prompt_scans_selected_mode(git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    commit_file(git_repo, "a.py", "x = 1\n", "second commit", when=T0)
    monkeypatch.setattr(sys, "stdin", _FakeTTY("1\n"))     # pick "layered"
    assert cli.main(["init"]) == 0
    assert "digest plan" in capsys.readouterr().out
    assert Store.open(git_repo).get_digest_meta("mode") == "layered"


def test_init_tty_prompt_skip_leaves_no_plan_but_a_breadcrumb(git_repo, monkeypatch,
                                                              capsys):
    monkeypatch.chdir(git_repo)
    commit_file(git_repo, "a.py", "x = 1\n", "second commit", when=T0)
    monkeypatch.setattr(sys, "stdin", _FakeTTY("5\n"))     # "skip"
    assert cli.main(["init"]) == 0
    assert Store.open(git_repo).list_digest_units() == []
    assert "digest history anytime" in capsys.readouterr().out


def test_init_survives_none_stdin(git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    commit_file(git_repo, "a.py", "x = 1\n", "second commit", when=T0)
    monkeypatch.setattr(sys, "stdin", None)                # pythonw/detached console
    assert cli.main(["init"]) == 0
    assert "rgit digest scan" in capsys.readouterr().out


def test_init_star_note_shown_on_tty(git_repo, monkeypatch):
    from rgit import updatecheck
    monkeypatch.setattr(updatecheck, "maybe_start_background_check", lambda now: None)
    monkeypatch.setattr(updatecheck, "render_notice", lambda version: None)
    monkeypatch.chdir(git_repo)
    out = _FakeTTY()                                       # stdout that claims a TTY
    monkeypatch.setattr(sys, "stdout", out)
    assert cli.main(["init"]) == 0
    text = out.getvalue()
    assert "https://github.com/StepzeroLab/research-git" in text
    assert "lin.yuxiang.contact@gmail.com" in text


def test_init_star_note_hidden_when_piped(git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    assert cli.main(["init"]) == 0
    out = capsys.readouterr().out
    assert "lin.yuxiang.contact@gmail.com" not in out
    assert "user/starred" not in out
