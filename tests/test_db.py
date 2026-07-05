from rgit.store.db import connect, init_schema


def test_schema_creates_all_tables(tmp_path):
    conn = connect(tmp_path / "graph.db")
    init_schema(conn)
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"features", "runs", "edges", "proposals"} <= names


def test_init_schema_is_idempotent(tmp_path):
    conn = connect(tmp_path / "graph.db")
    init_schema(conn)
    init_schema(conn)  # must not raise


def test_events_table_and_run_returncode_exist(tmp_path):
    from rgit.store.db import connect, init_schema
    conn = connect(tmp_path / "g.db")
    init_schema(conn)
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "events" in tables
    run_cols = {r[1] for r in conn.execute("PRAGMA table_info(runs)")}
    assert "returncode" in run_cols


def test_returncode_migration_adds_column_to_old_runs(tmp_path):
    import sqlite3
    from rgit.store.db import init_schema
    # simulate a pre-v2 db: runs without returncode
    conn = sqlite3.connect(tmp_path / "old.db")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE runs (id TEXT PRIMARY KEY, cmd TEXT NOT NULL, "
                 "artifact_hash TEXT NOT NULL, metrics TEXT, base_commit TEXT NOT NULL, "
                 "env TEXT, created_at TEXT NOT NULL)")
    conn.commit()
    init_schema(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(runs)")}
    assert "returncode" in cols


def test_schema_stamp_updates_after_migrations(tmp_path):
    # An older store carries the stamp of the code that last opened it; a
    # newer code version must move the stamp forward once its migrations run,
    # or doctor would warn schema_version_mismatch forever on healthy stores.
    conn = connect(tmp_path / "graph.db")
    init_schema(conn)
    conn.execute("UPDATE schema_metadata SET value='0' WHERE key='schema_version'")
    conn.commit()
    init_schema(conn)
    row = conn.execute(
        "SELECT value FROM schema_metadata WHERE key='schema_version'").fetchone()
    from rgit.store.db import SCHEMA_VERSION
    assert row["value"] == SCHEMA_VERSION


def test_edge_type_vocabulary_is_centralized():
    # doctor (and future write-side validation) must share one definition
    from rgit import doctor
    from rgit.store.models import CAPSULE_EDGE_TYPES, SYMMETRIC_EDGE_TYPES
    assert doctor.CAPSULE_EDGE_TYPES is CAPSULE_EDGE_TYPES
    assert doctor.SYMMETRIC_EDGE_TYPES is SYMMETRIC_EDGE_TYPES
    assert SYMMETRIC_EDGE_TYPES <= CAPSULE_EDGE_TYPES
