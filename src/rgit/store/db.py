import sqlite3
from pathlib import Path

SCHEMA_VERSION = "1"

SCHEMA = """
CREATE TABLE IF NOT EXISTS features (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    intent TEXT NOT NULL,
    status TEXT NOT NULL,
    base_commit TEXT NOT NULL,
    knobs TEXT NOT NULL DEFAULT '{}',
    data_assumptions TEXT,
    resurrection_guide TEXT,
    result_summary TEXT,
    payload_hash TEXT
);
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    cmd TEXT NOT NULL,
    artifact_hash TEXT NOT NULL,
    metrics TEXT,
    base_commit TEXT NOT NULL,
    env TEXT,
    created_at TEXT NOT NULL,
    returncode INTEGER
);
CREATE TABLE IF NOT EXISTS edges (
    src TEXT NOT NULL,
    dst TEXT NOT NULL,
    type TEXT NOT NULL,
    UNIQUE(src, dst, type)
);
CREATE TABLE IF NOT EXISTS proposals (
    id TEXT PRIMARY KEY,
    trigger TEXT NOT NULL,
    diff_ref TEXT NOT NULL,
    candidates TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    run_id TEXT,
    from_features TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    capsule_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    run_id TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS metric_directions (
    metric TEXT PRIMARY KEY,
    direction TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS schema_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def connect(path: Path) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    # migrate older graphs that predate columns
    pcols = {r[1] for r in conn.execute("PRAGMA table_info(proposals)")}
    if "from_features" not in pcols:
        conn.execute("ALTER TABLE proposals ADD COLUMN from_features TEXT")
    rcols = {r[1] for r in conn.execute("PRAGMA table_info(runs)")}
    if "returncode" not in rcols:
        conn.execute("ALTER TABLE runs ADD COLUMN returncode INTEGER")
    conn.execute(
        "INSERT OR IGNORE INTO schema_metadata VALUES (?,?)",
        ("schema_version", SCHEMA_VERSION))
    conn.commit()
