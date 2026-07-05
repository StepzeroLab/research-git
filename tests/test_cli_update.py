import sys

from rgit import cli, selfupdate, updatecheck


def _use_tmp_state(monkeypatch, tmp_path):
    monkeypatch.setattr(updatecheck, "state_path",
                        lambda: tmp_path / "update-check.json")


def test_update_off_on(monkeypatch, tmp_path, capsys):
    _use_tmp_state(monkeypatch, tmp_path)
    monkeypatch.delenv(updatecheck.ENV_FLAG, raising=False)
    assert cli.main(["update", "--off"]) == 0
    assert updatecheck.disabled() is True
    assert "disabled" in capsys.readouterr().out
    assert cli.main(["update", "--on"]) == 0
    assert updatecheck.disabled() is False


def test_update_dispatches_to_selfupdate(monkeypatch, tmp_path):
    _use_tmp_state(monkeypatch, tmp_path)
    called = []
    monkeypatch.setattr(selfupdate, "run_update",
                        lambda: called.append(True) or 0)
    assert cli.main(["update"]) == 0
    assert called == [True]


def test_notice_printed_after_command_on_tty(monkeypatch, tmp_path, capsys):
    _use_tmp_state(monkeypatch, tmp_path)
    monkeypatch.delenv(updatecheck.ENV_FLAG, raising=False)
    updatecheck.save_state({"latest_version": "99.0.0",
                            "last_check": 9e12})
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    assert cli.main(["install", "--list"]) == 0
    err = capsys.readouterr().err
    assert "99.0.0 available" in err
    assert "rgit update" in err


def test_notice_suppressed_when_not_tty(monkeypatch, tmp_path, capsys):
    _use_tmp_state(monkeypatch, tmp_path)
    monkeypatch.delenv(updatecheck.ENV_FLAG, raising=False)
    updatecheck.save_state({"latest_version": "99.0.0",
                            "last_check": 9e12})
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
    assert cli.main(["install", "--list"]) == 0
    assert "99.0.0" not in capsys.readouterr().err


def test_notice_suppressed_for_update_cmd(monkeypatch, tmp_path, capsys):
    _use_tmp_state(monkeypatch, tmp_path)
    monkeypatch.delenv(updatecheck.ENV_FLAG, raising=False)
    updatecheck.save_state({"latest_version": "99.0.0",
                            "last_check": 9e12})
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(selfupdate, "run_update", lambda: 0)
    cli.main(["update"])
    assert "available" not in capsys.readouterr().err


def test_install_from_update_uses_conservative_guidance(monkeypatch, tmp_path):
    import rgit.installer as installer
    seen = {}

    def fake_install(platform, *, scope, dry_run, mode, conservative=False):
        seen["conservative"] = conservative
        return {"platform": platform, "ran": True, "results": [],
                "guidance": {"action": "skipped_customized",
                             "path": "/x", "hint": "left untouched"}}

    monkeypatch.setattr(installer, "install", fake_install)
    assert cli.main(["install", "codex", "--from-update"]) == 0
    assert seen["conservative"] is True
