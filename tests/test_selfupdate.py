import subprocess
import sys
import types

from rgit import selfupdate


def test_detect_installer_uv_tool(monkeypatch):
    monkeypatch.setattr(selfupdate.sys, "prefix",
                        "/home/u/.local/share/uv/tools/research-git")
    assert selfupdate.detect_installer() == "uv-tool"


def test_detect_installer_uv_tool_windows(monkeypatch):
    monkeypatch.setattr(selfupdate.sys, "prefix",
                        r"C:\Users\u\AppData\Roaming\uv\tools\research-git")
    assert selfupdate.detect_installer() == "uv-tool"


def test_detect_installer_pipx(monkeypatch):
    monkeypatch.setattr(selfupdate.sys, "prefix",
                        "/home/u/.local/pipx/venvs/research-git")
    assert selfupdate.detect_installer() == "pipx"


def test_detect_installer_pip_fallback(monkeypatch):
    monkeypatch.setattr(selfupdate.sys, "prefix", "/repo/.venv")
    assert selfupdate.detect_installer() == "pip"


def test_upgrade_command_mapping():
    assert selfupdate.upgrade_command("uv-tool") == \
        ["uv", "tool", "upgrade", "research-git"]
    assert selfupdate.upgrade_command("pipx") == \
        ["pipx", "upgrade", "research-git"]
    assert selfupdate.upgrade_command("pip") == \
        [sys.executable, "-m", "pip", "install", "--upgrade", "research-git"]


def _completed(rc=0, out="", err=""):
    return subprocess.CompletedProcess(args=[], returncode=rc,
                                       stdout=out, stderr=err)


def test_run_update_success_refreshes_platforms(monkeypatch, capsys):
    ran = []

    def fake_run(cmd, **kw):
        ran.append(cmd)
        return _completed(0, out="ok")

    monkeypatch.setattr(selfupdate.subprocess, "run", fake_run)
    monkeypatch.setattr(selfupdate.shutil, "which",
                        lambda name: "/usr/bin/rgit" if name == "rgit" else None)
    import rgit.installer as installer
    monkeypatch.setattr(installer, "detect_platforms",
                        lambda: ["claude-code", "codex"])
    assert selfupdate.run_update() == 0
    # first call: the upgrade; then one refresh subprocess per platform
    assert ran[0][-1] == "research-git" or "research-git" in ran[0]
    assert ["/usr/bin/rgit", "install", "claude-code", "--from-update"] in ran
    assert ["/usr/bin/rgit", "install", "codex", "--from-update"] in ran


def test_run_update_failure_skips_refresh(monkeypatch, capsys):
    ran = []

    def fake_run(cmd, **kw):
        ran.append(cmd)
        return _completed(1, err="boom")

    monkeypatch.setattr(selfupdate.subprocess, "run", fake_run)
    assert selfupdate.run_update() == 1
    assert len(ran) == 1                      # no refresh after failed upgrade


def test_run_update_pep668_hint(monkeypatch, capsys):
    monkeypatch.setattr(
        selfupdate.subprocess, "run",
        lambda cmd, **kw: _completed(1, err="error: externally-managed-environment"))
    assert selfupdate.run_update() == 1
    err = capsys.readouterr().err
    assert "uv tool install research-git" in err


def test_run_update_windows_lock_hint(monkeypatch, capsys):
    monkeypatch.setattr(
        selfupdate.subprocess, "run",
        lambda cmd, **kw: _completed(1, err="[WinError 5] Access is denied: rgit.exe"))
    assert selfupdate.run_update() == 1
    err = capsys.readouterr().err
    assert "python -m pip install -U research-git" in err


def test_run_update_missing_tool_binary(monkeypatch, capsys):
    def fake_run(cmd, **kw):
        raise FileNotFoundError(cmd[0])

    monkeypatch.setattr(selfupdate.subprocess, "run", fake_run)
    monkeypatch.setattr(selfupdate, "detect_installer", lambda: "uv-tool")
    assert selfupdate.run_update() == 1
    assert "manually" in capsys.readouterr().err


def test_python_dash_m_rgit_entrypoint():
    p = subprocess.run([sys.executable, "-m", "rgit", "install", "--list"],
                       capture_output=True, text=True)
    assert p.returncode == 0
    assert "platforms:" in p.stdout
