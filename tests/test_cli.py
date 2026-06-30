import json
import sys
from pathlib import Path

import rgit.cli as cli
from rgit.cli import main
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
    assert cli.main(["install", "claude-code", "--dry-run"]) == 0
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
    assert after.neighbors(src, "produced")                       # lineage edge created
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
    payload.write_text(json.dumps([{"name": "refined", "intent": "better"}]))
    cli.main(["resegment", pid, "--from-json", str(payload)])
    assert store.get_proposal(pid).candidates == [{"name": "refined", "intent": "better"}]


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

    assert cli.main(["install", "codex", "--dry-run"]) == 0

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

    assert cli.main(["install", "generic", "--dry-run"]) == 0

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
    monkeypatch.setattr(cli, "_stdin_is_tty", lambda: True)  # must not prompt

    assert cli.main(["install", "codex", "--dry-run", "--guidance", "none"]) == 0

    res = json.loads(capsys.readouterr().out)
    assert res["guidance"] == {"action": "disabled"}


def test_cli_install_prompts_for_mode_on_tty_without_flag(
        tmp_path, monkeypatch, capsys):
    from rgit import agent_platforms, installer
    monkeypatch.setattr(agent_platforms, "home_dir", lambda: tmp_path)
    monkeypatch.setattr(installer, "_AGENTS_SKILLS_DIR",
                        tmp_path / ".agents" / "skills")
    monkeypatch.setattr(cli, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(cli, "_prompt_guidance_mode", lambda platform: "manual-only")

    assert cli.main(["install", "codex", "--dry-run"]) == 0

    res = json.loads(capsys.readouterr().out)
    assert "Current mode: manual-only" in res["guidance"]["block"]


def test_prompt_guidance_mode_maps_answers(monkeypatch):
    answers = iter(["2"])
    monkeypatch.setattr("builtins.input", lambda: next(answers))
    assert cli._prompt_guidance_mode("codex") == "manual-only"


def test_prompt_guidance_mode_empty_defaults_then_retries_on_garbage(monkeypatch):
    answers = iter([""])
    monkeypatch.setattr("builtins.input", lambda: next(answers))
    assert cli._prompt_guidance_mode("codex") == "default"

    answers = iter(["nope", "3"])
    monkeypatch.setattr("builtins.input", lambda: next(answers))
    assert cli._prompt_guidance_mode("codex") == "none"


def test_cli_install_does_not_prompt_when_not_a_tty(
        tmp_path, monkeypatch, capsys):
    from rgit import agent_platforms, installer
    monkeypatch.setattr(agent_platforms, "home_dir", lambda: tmp_path)
    monkeypatch.setattr(installer, "_AGENTS_SKILLS_DIR",
                        tmp_path / ".agents" / "skills")
    monkeypatch.setattr(cli, "_stdin_is_tty", lambda: False)

    def explode(platform):
        raise AssertionError("must not prompt when stdin is not a TTY")

    monkeypatch.setattr(cli, "_prompt_guidance_mode", explode)

    assert cli.main(["install", "codex", "--dry-run"]) == 0

    res = json.loads(capsys.readouterr().out)
    assert "Current mode: default" in res["guidance"]["block"]


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
