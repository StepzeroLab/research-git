import json
import sqlite3

from rgit.store.models import Capsule, CodeSlice, Proposal, Run
from rgit.store.store import Store


def _cap(name="feature"):
    return Capsule(
        id="",
        name=name,
        intent="intent",
        status="approved",
        base_commit="abc",
        knobs={},
        data_assumptions=None,
        resurrection_guide=None,
        result_summary=None,
        payload_hash=None,
        code_slices=[CodeSlice("model.py", "forward", None, "x", "wrap")],
    )


def _run(store, artifact=None):
    artifact_hash = artifact or store.objects.put(b"artifact")
    return store.add_run(Run(
        id="",
        cmd="python train.py",
        artifact_hash=artifact_hash,
        metrics=None,
        base_commit="abc",
        env=None,
        created_at="2026-01-01T00:00:00",
    ))


def _proposal(store, diff=None, candidates=None):
    diff_ref = diff or store.objects.put(b"diff")
    return store.add_proposal(Proposal(
        id="",
        trigger="manual",
        diff_ref=diff_ref,
        candidates=candidates if candidates is not None else [{
            "name": "candidate",
            "intent": "intent",
            "code_slices": [{
                "file": "model.py",
                "symbol": "forward",
                "anchor": None,
                "code": "x",
                "kind": "wrap",
            }],
        }],
    ))


def _object_path(store, digest):
    return store.objects._path(digest)


def _codes(report, level=None):
    return {
        f["code"]
        for f in report["findings"]
        if level is None or f["level"] == level
    }


def test_doctor_reports_healthy_store(git_repo):
    from rgit.doctor import run_doctor

    store = Store.init(git_repo)
    a = store.add_feature(_cap("a"))
    b = store.add_feature(_cap("b"))
    rid = _run(store)
    _proposal(store)
    store.add_edge(a, rid, "produced")
    store.add_edge(rid, a, "active")
    store.add_edge(a, b, "overlaps")
    store.add_edge(b, a, "overlaps")

    report = run_doctor(store)

    assert report["ok"] is True
    assert report["summary"] == {"errors": 0, "warnings": 0}
    assert report["schema"]["version"] == "1"
    assert report["findings"] == []


def test_doctor_reports_missing_feature_payload_object(git_repo):
    from rgit.doctor import run_doctor

    store = Store.init(git_repo)
    fid = store.add_feature(_cap())
    payload_hash = store.conn.execute(
        "SELECT payload_hash FROM features WHERE id=?", (fid,)
    ).fetchone()["payload_hash"]
    _object_path(store, payload_hash).unlink()

    report = run_doctor(store)

    assert report["ok"] is False
    assert "missing_feature_payload_object" in _codes(report, level="error")


def test_doctor_reports_missing_run_artifact_object(git_repo):
    from rgit.doctor import run_doctor

    store = Store.init(git_repo)
    artifact_hash = store.objects.put(b"artifact")
    _run(store, artifact=artifact_hash)
    _object_path(store, artifact_hash).unlink()

    report = run_doctor(store)

    assert "missing_run_artifact_object" in _codes(report, level="error")


def test_doctor_reports_missing_proposal_diff_object(git_repo):
    from rgit.doctor import run_doctor

    store = Store.init(git_repo)
    diff_ref = store.objects.put(b"diff")
    _proposal(store, diff=diff_ref)
    _object_path(store, diff_ref).unlink()

    report = run_doctor(store)

    assert "missing_proposal_diff_object" in _codes(report, level="error")


def test_doctor_reports_malformed_proposal_candidates_json(git_repo):
    from rgit.doctor import run_doctor

    store = Store.init(git_repo)
    pid = _proposal(store)
    store.conn.execute(
        "UPDATE proposals SET candidates=? WHERE id=?", ("not json", pid)
    )
    store.conn.commit()

    report = run_doctor(store)

    assert "malformed_proposal_candidates_json" in _codes(report, level="error")


def test_doctor_reports_structurally_malformed_feature_payload(git_repo):
    from rgit.doctor import run_doctor

    store = Store.init(git_repo)
    payload_hash = store.objects.put_json([{}])
    store.conn.execute(
        "INSERT INTO features (id, name, intent, status, base_commit, knobs, "
        "data_assumptions, resurrection_guide, result_summary, payload_hash) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            "feat_bad",
            "bad",
            "intent",
            "approved",
            "abc",
            "{}",
            None,
            None,
            None,
            payload_hash,
        ),
    )
    store.conn.commit()

    report = run_doctor(store)

    assert "malformed_feature_payload_json" in _codes(report, level="error")


def test_doctor_reports_structurally_malformed_candidate(git_repo):
    from rgit.doctor import run_doctor

    store = Store.init(git_repo)
    _proposal(store, candidates=[{}])

    report = run_doctor(store)

    assert "malformed_proposal_candidates_json" in _codes(report, level="error")


def test_doctor_reports_dangling_edge(git_repo):
    from rgit.doctor import run_doctor

    store = Store.init(git_repo)
    fid = store.add_feature(_cap())
    store.add_edge(fid, "run_missing", "produced")

    report = run_doctor(store)

    assert "dangling_edge" in _codes(report, level="error")


def test_doctor_accepts_normal_touches_module_edge(git_repo):
    from rgit.doctor import run_doctor

    store = Store.init(git_repo)
    fid = store.add_feature(_cap())
    store.add_edge(fid, "module:model.py", "touches")

    report = run_doctor(store)

    assert "dangling_edge" not in _codes(report, level="error")


def test_doctor_warns_on_missing_symmetric_reverse_edge(git_repo):
    from rgit.doctor import run_doctor

    store = Store.init(git_repo)
    a = store.add_feature(_cap("a"))
    b = store.add_feature(_cap("b"))
    store.add_edge(a, b, "overlaps")

    report = run_doctor(store)

    assert report["ok"] is True
    assert "missing_reverse_edge" in _codes(report, level="warning")


def test_cli_doctor_json_output(git_repo, monkeypatch, capsys):
    import rgit.cli as cli

    monkeypatch.chdir(git_repo)
    Store.init(git_repo)

    rc = cli.main(["doctor", "--json"])
    report = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert report["ok"] is True
    assert report["summary"] == {"errors": 0, "warnings": 0}
    assert report["schema"]["version"] == "1"


def test_cli_doctor_warns_without_overwriting_schema_version(
        git_repo, monkeypatch, capsys):
    import rgit.cli as cli

    monkeypatch.chdir(git_repo)
    store = Store.init(git_repo)
    store.conn.execute(
        "UPDATE schema_metadata SET value=? WHERE key=?",
        ("999", "schema_version"),
    )
    store.conn.commit()
    store.conn.close()

    rc = cli.main(["doctor", "--json"])
    report = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert report["schema"]["version"] == "999"
    assert "schema_version_mismatch" in _codes(report, level="warning")


def test_cli_doctor_reports_missing_schema_metadata_without_recreating_it(
        git_repo, monkeypatch, capsys):
    import rgit.cli as cli

    monkeypatch.chdir(git_repo)
    store = Store.init(git_repo)
    store.conn.execute("DROP TABLE schema_metadata")
    store.conn.commit()
    store.conn.close()

    rc = cli.main(["doctor", "--json"])
    report = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert "missing_schema_metadata" in _codes(report, level="warning")
    conn = sqlite3.connect(git_repo / ".rgit" / "graph.db")
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='schema_metadata'"
        ).fetchone()
        assert row is None
    finally:
        conn.close()


def test_cli_doctor_returns_one_when_errors_exist(git_repo, monkeypatch, capsys):
    import rgit.cli as cli

    monkeypatch.chdir(git_repo)
    store = Store.init(git_repo)
    artifact_hash = store.objects.put(b"artifact")
    _run(store, artifact=artifact_hash)
    _object_path(store, artifact_hash).unlink()

    rc = cli.main(["doctor"])
    out = capsys.readouterr().out

    assert rc == 1
    assert "errors: 1" in out
    assert "missing_run_artifact_object" in out


def test_cli_doctor_json_output_when_store_missing(tmp_path, monkeypatch, capsys):
    import rgit.cli as cli

    monkeypatch.chdir(tmp_path)

    rc = cli.main(["doctor", "--json"])
    report = json.loads(capsys.readouterr().out)

    assert rc == 1
    assert report["ok"] is False
    assert report["summary"]["errors"] == 1
    assert report["findings"][0]["code"] == "store_open_failed"
