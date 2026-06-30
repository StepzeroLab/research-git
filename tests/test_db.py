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
