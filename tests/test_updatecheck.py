import json

from rgit import updatecheck

import io


def _use_tmp_state(monkeypatch, tmp_path):
    monkeypatch.setattr(updatecheck, "state_path",
                        lambda: tmp_path / "update-check.json")


def test_load_state_missing_file_is_empty(monkeypatch, tmp_path):
    _use_tmp_state(monkeypatch, tmp_path)
    assert updatecheck.load_state() == {}


def test_load_state_corrupted_json_self_heals(monkeypatch, tmp_path):
    _use_tmp_state(monkeypatch, tmp_path)
    (tmp_path / "update-check.json").write_text("{not json", encoding="utf-8")
    assert updatecheck.load_state() == {}
    updatecheck.save_state({"disabled": True})          # rewrite works
    assert updatecheck.load_state() == {"disabled": True}


def test_save_creates_parent_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(updatecheck, "state_path",
                        lambda: tmp_path / "deep" / "update-check.json")
    updatecheck.save_state({"last_check": 5})
    assert updatecheck.load_state() == {"last_check": 5}


def test_disabled_via_env(monkeypatch, tmp_path):
    _use_tmp_state(monkeypatch, tmp_path)
    monkeypatch.setenv(updatecheck.ENV_FLAG, "0")
    assert updatecheck.disabled() is True


def test_disabled_via_state_flag(monkeypatch, tmp_path):
    _use_tmp_state(monkeypatch, tmp_path)
    monkeypatch.delenv(updatecheck.ENV_FLAG, raising=False)
    updatecheck.set_disabled(True)
    assert updatecheck.disabled() is True
    updatecheck.set_disabled(False)
    assert updatecheck.disabled() is False


def test_should_check_ttl(monkeypatch, tmp_path):
    _use_tmp_state(monkeypatch, tmp_path)
    monkeypatch.delenv(updatecheck.ENV_FLAG, raising=False)
    assert updatecheck.should_check(now=1000.0) is True          # never checked
    updatecheck.save_state({"last_check": 1000.0})
    assert updatecheck.should_check(now=1000.0 + 3600) is False  # inside TTL
    assert updatecheck.should_check(
        now=1000.0 + updatecheck.TTL_SECONDS + 1) is True        # expired


def test_should_check_false_when_disabled(monkeypatch, tmp_path):
    _use_tmp_state(monkeypatch, tmp_path)
    monkeypatch.setenv(updatecheck.ENV_FLAG, "0")
    assert updatecheck.should_check(now=1e12) is False


def test_should_check_tolerates_garbage_last_check(monkeypatch, tmp_path):
    _use_tmp_state(monkeypatch, tmp_path)
    monkeypatch.delenv(updatecheck.ENV_FLAG, raising=False)
    updatecheck.save_state({"last_check": "yesterday"})
    assert updatecheck.should_check(now=0.0) is True


def test_newer_basic():
    assert updatecheck._newer("0.0.5", "0.0.4") is True
    assert updatecheck._newer("0.0.4", "0.0.4") is False
    assert updatecheck._newer("0.0.3", "0.0.4") is False
    assert updatecheck._newer("0.1.0", "0.0.9") is True


def test_newer_incomparable_is_false():
    assert updatecheck._newer("weird", "0.0.4") is False


def test_render_notice_from_cache(monkeypatch, tmp_path):
    _use_tmp_state(monkeypatch, tmp_path)
    updatecheck.save_state({"latest_version": "0.0.9"})
    notice = updatecheck.render_notice("0.0.4")
    assert notice == ("research-git 0.0.9 available (you have 0.0.4) "
                      "— run `rgit update`")


def test_render_notice_none_when_current(monkeypatch, tmp_path):
    _use_tmp_state(monkeypatch, tmp_path)
    updatecheck.save_state({"latest_version": "0.0.4"})
    assert updatecheck.render_notice("0.0.4") is None


def test_render_notice_none_when_no_cache(monkeypatch, tmp_path):
    _use_tmp_state(monkeypatch, tmp_path)
    assert updatecheck.render_notice("0.0.4") is None


def test_render_notice_none_when_permanently_disabled(monkeypatch, tmp_path):
    # A cached newer version must stay silent once the user opted out for good.
    _use_tmp_state(monkeypatch, tmp_path)
    monkeypatch.delenv(updatecheck.ENV_FLAG, raising=False)
    updatecheck.save_state({"latest_version": "99.0.0"})
    updatecheck.set_disabled(True)
    assert updatecheck.render_notice("0.0.4") is None


def test_render_notice_none_when_disabled_via_env(monkeypatch, tmp_path):
    _use_tmp_state(monkeypatch, tmp_path)
    updatecheck.save_state({"latest_version": "99.0.0"})
    monkeypatch.setenv(updatecheck.ENV_FLAG, "0")
    assert updatecheck.render_notice("0.0.4") is None


class _FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_fetch_once_success(monkeypatch, tmp_path):
    _use_tmp_state(monkeypatch, tmp_path)
    body = b'{"info": {"version": "0.0.9"}}'
    monkeypatch.setattr(updatecheck.urllib.request, "urlopen",
                        lambda url, timeout: _FakeResp(body))
    updatecheck._fetch_once(now=42.0)
    st = updatecheck.load_state()
    assert st["latest_version"] == "0.0.9"
    assert st["last_check"] == 42.0


def test_fetch_once_failure_is_silent(monkeypatch, tmp_path):
    _use_tmp_state(monkeypatch, tmp_path)

    def boom(url, timeout):
        raise OSError("no network")

    monkeypatch.setattr(updatecheck.urllib.request, "urlopen", boom)
    updatecheck._fetch_once(now=42.0)          # must not raise
    assert "latest_version" not in updatecheck.load_state()


def test_maybe_start_stamps_and_spawns(monkeypatch, tmp_path):
    _use_tmp_state(monkeypatch, tmp_path)
    monkeypatch.delenv(updatecheck.ENV_FLAG, raising=False)
    calls = []
    monkeypatch.setattr(updatecheck, "_fetch_once",
                        lambda now: calls.append(now))

    class InlineThread:
        def __init__(self, target=None, args=(), daemon=None):
            self._target, self._args = target, args

        def start(self):
            self._target(*self._args)

    monkeypatch.setattr(updatecheck.threading, "Thread", InlineThread)
    updatecheck.maybe_start_background_check(now=1000.0)
    assert calls == [1000.0]
    # stamped immediately: a second call inside the TTL does nothing
    updatecheck.maybe_start_background_check(now=1001.0)
    assert calls == [1000.0]


def test_hint_ledger(monkeypatch, tmp_path):
    _use_tmp_state(monkeypatch, tmp_path)
    assert updatecheck.hint_pending("/x/AGENTS.md") is True
    updatecheck.mark_hint_shown("/x/AGENTS.md")
    assert updatecheck.hint_pending("/x/AGENTS.md") is False
    assert updatecheck.hint_pending("/y/CLAUDE.md") is True


def test_hint_ledger_tolerates_corrupt_guidance_hints(monkeypatch, tmp_path):
    _use_tmp_state(monkeypatch, tmp_path)
    updatecheck.save_state({"guidance_hints": "corrupt"})
    assert updatecheck.hint_pending("/x/AGENTS.md") is True
    updatecheck.mark_hint_shown("/x/AGENTS.md")          # must not raise
    assert updatecheck.hint_pending("/x/AGENTS.md") is False
    assert updatecheck.load_state()["guidance_hints"] == ["/x/AGENTS.md"]
