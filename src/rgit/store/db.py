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
    payload_hash TEXT,
    origin TEXT NOT NULL DEFAULT 'live'
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
    from_features TEXT,
    source_commit TEXT
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
CREATE TABLE IF NOT EXISTS digest_units (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    shas TEXT NOT NULL,
    score REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    skip_reason TEXT,
    proposal_id TEXT,
    capsule_ids TEXT,
    meta TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS digest_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS schema_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def connect(path: Path, readonly: bool = False) -> sqlite3.Connection:
    path = Path(path)
    if readonly:
        # URI mode=ro: the engine refuses writes and never creates the file.
        conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
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
    if "source_commit" not in pcols:
        conn.execute("ALTER TABLE proposals ADD COLUMN source_commit TEXT")
    rcols = {r[1] for r in conn.execute("PRAGMA table_info(runs)")}
    if "returncode" not in rcols:
        conn.execute("ALTER TABLE runs ADD COLUMN returncode INTEGER")
    fcols = {r[1] for r in conn.execute("PRAGMA table_info(features)")}
    if "origin" not in fcols:
        conn.execute(
            "ALTER TABLE features ADD COLUMN origin TEXT NOT NULL DEFAULT 'live'")
    # Stamp AFTER the migrations above so the value always means "migrations
    # up to this version have been applied". INSERT OR IGNORE would freeze the
    # first-ever stamp and make doctor warn schema_version_mismatch forever
    # once SCHEMA_VERSION moves.
    conn.execute(
        "INSERT INTO schema_metadata VALUES (?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        ("schema_version", SCHEMA_VERSION))
    conn.commit()
