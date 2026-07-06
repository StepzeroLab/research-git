import json
import sqlite3
import subprocess

import pytest

import rgit.capture_history as history
import rgit.cli as cli
from rgit.capture_history import plan_capture_history
from rgit.gitutil import current_commit
from rgit.store.store import Store


def _commit_all(repo, msg):
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True,
                   capture_output=True)
    subprocess.run(["git", "commit", "-q", "-m", msg], cwd=repo, check=True,
                   capture_output=True)


def _two_commit_history(repo):
    base = current_commit(repo)
    (repo / "model.py").write_text("def forward(x):\n    return x * 2\n")
    _commit_all(repo, "double")
    first = current_commit(repo)
    (repo / "extra.py").write_text("def extra():\n    return 1\n")
    _commit_all(repo, "extra")
    second = current_commit(repo)
    return base, first, second


def test_plan_capture_history_dry_run_does_not_write_proposals(git_repo):
    store = Store.init(git_repo)
    base, first, second = _two_commit_history(git_repo)

    plan = plan_capture_history(store, f"{base}..HEAD")

    assert [item.commit for item in plan.items] == [first, second]
    assert [item.status for item in plan.items] == ["would_capture", "would_capture"]
    assert [item.subject for item in plan.items] == ["double", "extra"]
    assert store.list_proposals("open") == []
    assert plan.summary == {
        "would_capture": 2,
        "captured": 0,
        "existing": 0,
        "skipped": 0,
    }


def test_plan_capture_history_marks_large_diff_as_skipped(git_repo):
    store = Store.init(git_repo)
    base = current_commit(git_repo)
    (git_repo / "large.txt").write_text("x" * 200)
    _commit_all(git_repo, "large")

    plan = plan_capture_history(store, f"{base}..HEAD", max_diff_bytes=50)

    assert len(plan.items) == 1
    assert plan.items[0].status == "skipped"
    assert "exceeds" in plan.items[0].reason
    assert store.list_proposals("open") == []


def test_plan_capture_history_skips_large_diff_before_loading_patch(
        git_repo, monkeypatch):
    store = Store.init(git_repo)
    base = current_commit(git_repo)
    (git_repo / "large.txt").write_text("x" * 200)
    _commit_all(git_repo, "large")

    def fail_if_loaded(self, repo):
        raise AssertionError("oversized patch should not be loaded")

    monkeypatch.setattr(history.CommitDiffSource, "diff", fail_if_loaded)

    plan = plan_capture_history(store, f"{base}..HEAD", max_diff_bytes=50)

    assert plan.items[0].status == "skipped"
    assert "exceeds" in plan.items[0].reason


def test_plan_capture_history_marks_duplicate_diff_within_range(git_repo):
    store = Store.init(git_repo)
    base = current_commit(git_repo)
    (git_repo / "model.py").write_text("def forward(x):\n    return x * 2\n")
    _commit_all(git_repo, "double")
    (git_repo / "model.py").write_text("def forward(x):\n    return x\n")
    _commit_all(git_repo, "revert double")
    (git_repo / "model.py").write_text("def forward(x):\n    return x * 2\n")
    _commit_all(git_repo, "double again")

    plan = plan_capture_history(store, f"{base}..HEAD")

    assert [item.status for item in plan.items] == [
        "would_capture", "would_capture", "duplicate"]
    assert plan.items[2].proposal_id is None
    assert "same diff" in plan.items[2].reason


def test_cli_capture_history_json_dry_run(git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    Store.init(git_repo)
    base, first, second = _two_commit_history(git_repo)

    assert cli.main(["capture-history", f"{base}..HEAD", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["range"] == f"{base}..HEAD"
    assert payload["write"] is False
    assert payload["truncated"] is False
    assert [item["commit"] for item in payload["items"]] == [first, second]
    assert payload["summary"]["would_capture"] == 2
    assert Store.open(git_repo).list_proposals("open") == []


def test_cli_capture_history_respects_max_commits(git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    Store.init(git_repo)
    base, _first, second = _two_commit_history(git_repo)

    assert cli.main(["capture-history", f"{base}..HEAD",
                     "--max-commits", "1", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert [item["commit"] for item in payload["items"]] == [second]
    assert payload["max_commits"] == 1
    assert payload["truncated"] is True
    assert payload["summary"]["would_capture"] == 1


def test_capture_history_max_commits_is_git_limited(git_repo, monkeypatch):
    store = Store.init(git_repo)
    base, _first, second = _two_commit_history(git_repo)
    commands = []
    real_run = subprocess.run

    def spy_run(cmd, *args, **kwargs):
        if cmd[:2] == ["git", "rev-list"]:
            commands.append(cmd)
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(history.subprocess, "run", spy_run)

    plan = plan_capture_history(store, f"{base}..HEAD", max_commits=1)

    assert commands[0] == [
        "git", "rev-list", "--max-count", "2", f"{base}..HEAD"]
    assert [item.commit for item in plan.items] == [second]
    assert plan.truncated is True


def test_cli_capture_history_plain_output_reports_truncation(
        git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    Store.init(git_repo)
    base, _first, _second = _two_commit_history(git_repo)

    assert cli.main(["capture-history", f"{base}..HEAD",
                     "--max-commits", "1"]) == 0

    assert "truncated by --max-commits" in capsys.readouterr().out


def test_cli_capture_history_dry_run_does_not_migrate_store(
        git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    store = Store.init(git_repo)
    store.conn.execute(
        "UPDATE schema_metadata SET value=? WHERE key=?",
        ("stale", "schema_version"))
    store.conn.commit()
    base, _first, _second = _two_commit_history(git_repo)

    assert cli.main(["capture-history", f"{base}..HEAD", "--json"]) == 0

    capsys.readouterr()
    row = store.conn.execute(
        "SELECT value FROM schema_metadata WHERE key='schema_version'"
    ).fetchone()
    assert row["value"] == "stale"


def test_cli_capture_history_dry_run_reads_old_proposal_schema(
        git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    store = Store.init(git_repo)
    base = current_commit(git_repo)
    (git_repo / "model.py").write_text("def forward(x):\n    return x * 2\n")
    _commit_all(git_repo, "double")
    first = current_commit(git_repo)
    diff = history.CommitDiffSource(first).diff(git_repo)
    diff_ref = store.objects.put(diff.encode("utf-8", errors="replace"))
    store.conn.execute("DROP TABLE proposals")
    store.conn.execute(
        "CREATE TABLE proposals (id TEXT PRIMARY KEY, trigger TEXT NOT NULL, "
        "diff_ref TEXT NOT NULL, candidates TEXT NOT NULL, "
        "status TEXT NOT NULL DEFAULT 'open', run_id TEXT)"
    )
    store.conn.execute(
        "INSERT INTO proposals VALUES (?,?,?,?,?,?)",
        ("prop_old", "manual", diff_ref, json.dumps([{"name": "old"}]),
         "open", None),
    )
    store.conn.commit()
    store.conn.close()

    assert cli.main(["capture-history", f"{base}..HEAD", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["items"][0]["status"] == "existing"
    assert payload["items"][0]["proposal_id"] == "prop_old"
    assert payload["items"][0]["candidate_count"] == 1
    conn = sqlite3.connect(git_repo / ".rgit" / "graph.db")
    cols = {row[1] for row in conn.execute("PRAGMA table_info(proposals)")}
    conn.close()
    assert "source_commit" not in cols


def test_cli_capture_history_with_init_flag_bootstraps_store(
        git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    base, _first, _second = _two_commit_history(git_repo)

    assert cli.main(["capture-history", f"{base}..HEAD", "--init", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["summary"]["would_capture"] == 2
    assert (git_repo / ".rgit" / "graph.db").exists()
    assert Store.open(git_repo).list_proposals("open") == []


def test_cli_capture_history_write_creates_one_proposal_per_commit(
        git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    monkeypatch.setattr(cli, "_SEGMENTER", None)
    Store.init(git_repo)
    base, first, second = _two_commit_history(git_repo)

    assert cli.main(["capture-history", f"{base}..HEAD", "--write"]) == 0

    out = capsys.readouterr().out
    assert "captured" in out and first[:12] in out and second[:12] in out
    props = Store.open(git_repo).list_proposals("open")
    by_commit = {p.source_commit: p for p in props}
    assert sorted(by_commit) == sorted([first, second])
    assert {p.trigger for p in props} == {"history"}
    assert len(props) == 2


def test_cli_capture_history_write_skips_oversized_diff(
        git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    Store.init(git_repo)
    base = current_commit(git_repo)
    (git_repo / "large.txt").write_text("x" * 200)
    _commit_all(git_repo, "large")

    assert cli.main(["capture-history", f"{base}..HEAD", "--write",
                     "--max-diff-bytes", "50", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["items"][0]["status"] == "skipped"
    assert payload["items"][0]["diff_bytes"] is None
    assert payload["items"][0]["diff_bytes_at_least"] == 51
    assert payload["items"][0]["diff_bytes_truncated"] is True
    assert payload["summary"]["skipped"] == 1
    assert Store.open(git_repo).list_proposals("open") == []


def test_cli_capture_history_write_reuses_existing_open_proposal(
        git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    monkeypatch.setattr(cli, "_SEGMENTER", None)
    Store.init(git_repo)
    base, first, second = _two_commit_history(git_repo)

    assert cli.main(["capture", "--commit", first]) == 0
    capsys.readouterr()

    assert cli.main(["capture-history", f"{base}..HEAD", "--write", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert [item["status"] for item in payload["items"]] == ["existing", "captured"]
    props = Store.open(git_repo).list_proposals("open")
    assert len(props) == 2
    assert sorted(p.source_commit for p in props) == sorted([first, second])


def test_cli_capture_history_dry_run_prints_duplicate_without_none(
        git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    Store.init(git_repo)
    base = current_commit(git_repo)
    (git_repo / "model.py").write_text("def forward(x):\n    return x * 2\n")
    _commit_all(git_repo, "double")
    (git_repo / "model.py").write_text("def forward(x):\n    return x\n")
    _commit_all(git_repo, "revert double")
    (git_repo / "model.py").write_text("def forward(x):\n    return x * 2\n")
    _commit_all(git_repo, "double again")

    assert cli.main(["capture-history", f"{base}..HEAD"]) == 0

    out = capsys.readouterr().out
    assert "duplicate" in out
    assert "same diff" in out
    assert "-> None" not in out


def test_cli_capture_history_write_preserves_duplicate_existing_candidate_count(
        git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    monkeypatch.setattr(cli, "_SEGMENTER", None)
    Store.init(git_repo)
    base = current_commit(git_repo)
    (git_repo / "model.py").write_text("def forward(x):\n    return x * 2\n")
    _commit_all(git_repo, "double")
    first = current_commit(git_repo)
    (git_repo / "model.py").write_text("def forward(x):\n    return x\n")
    _commit_all(git_repo, "revert double")
    (git_repo / "model.py").write_text("def forward(x):\n    return x * 2\n")
    _commit_all(git_repo, "double again")

    assert cli.main(["capture", "--commit", first]) == 0
    capsys.readouterr()

    assert cli.main(["capture-history", f"{base}..HEAD", "--write", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert [item["status"] for item in payload["items"]] == [
        "existing", "captured", "existing"]
    assert payload["items"][2]["proposal_id"] == payload["items"][0]["proposal_id"]
    assert payload["items"][2]["candidate_count"] == payload["items"][0]["candidate_count"]
    assert payload["items"][2]["candidate_count"] is not None


def test_cli_capture_history_write_reuses_duplicate_diff_from_same_range(
        git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    monkeypatch.setattr(cli, "_SEGMENTER", None)
    Store.init(git_repo)
    base = current_commit(git_repo)
    (git_repo / "model.py").write_text("def forward(x):\n    return x * 2\n")
    _commit_all(git_repo, "double")
    (git_repo / "model.py").write_text("def forward(x):\n    return x\n")
    _commit_all(git_repo, "revert double")
    (git_repo / "model.py").write_text("def forward(x):\n    return x * 2\n")
    _commit_all(git_repo, "double again")

    assert cli.main(["capture-history", f"{base}..HEAD", "--write", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert [item["status"] for item in payload["items"]] == [
        "captured", "captured", "existing"]
    assert payload["items"][2]["proposal_id"] == payload["items"][0]["proposal_id"]
    assert len(Store.open(git_repo).list_proposals("open")) == 2


def test_capture_history_rejects_nondotted_range(git_repo):
    store = Store.init(git_repo)
    with pytest.raises(ValueError, match="A..B"):
        plan_capture_history(store, "HEAD")


def test_capture_history_rejects_three_dot_range(git_repo):
    store = Store.init(git_repo)
    with pytest.raises(ValueError, match="A\\.\\.B"):
        plan_capture_history(store, "main...HEAD")


def test_capture_history_rejects_nonpositive_max_diff_bytes(git_repo):
    store = Store.init(git_repo)
    with pytest.raises(ValueError, match="max-diff-bytes"):
        plan_capture_history(store, "HEAD..HEAD", max_diff_bytes=0)


def test_cli_capture_history_invalid_range_is_clean(
        git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    Store.init(git_repo)

    assert cli.main(["capture-history", "HEAD"]) == 1

    out = capsys.readouterr().out
    assert "A..B" in out and "Traceback" not in out
    assert "git log" in out
    assert Store.open(git_repo).list_proposals("open") == []


def test_cli_capture_history_invalid_limits_are_clean(
        git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    Store.init(git_repo)

    assert cli.main(["capture-history", "HEAD..HEAD", "--max-diff-bytes", "0"]) == 1
    out = capsys.readouterr().out
    assert "--max-diff-bytes" in out and "git log" not in out

    assert cli.main(["capture-history", "HEAD..HEAD", "--max-commits", "0"]) == 1
    out = capsys.readouterr().out
    assert "--max-commits" in out and "git log" not in out
