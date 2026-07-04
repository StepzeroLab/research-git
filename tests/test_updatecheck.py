import json

from rgit import updatecheck


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
