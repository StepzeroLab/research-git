# tests/test_metricdir_store.py
import sqlite3
from rgit.store.store import Store
from rgit.store.db import connect, init_schema


def test_set_and_get_direction_roundtrip(git_repo):
    store = Store.init(git_repo)
    store.set_metric_direction("eval_loss", "lower")
    store.set_metric_direction("accuracy", "higher")
    assert store.get_metric_direction("eval_loss") == "lower"
    assert store.get_metric_direction("accuracy") == "higher"
    assert store.get_metric_direction("unknown_metric") is None


def test_set_direction_upserts(git_repo):
    store = Store.init(git_repo)
    store.set_metric_direction("m", "lower")
    store.set_metric_direction("m", "higher")    # overwrite, not duplicate
    assert store.get_metric_direction("m") == "higher"
    assert store.list_metric_directions() == {"m": "higher"}


def test_open_migrates_db_without_metric_directions(git_repo, tmp_path):
    """A graph.db created before the metric_directions table still opens."""
    # Build a legacy DB that has every table EXCEPT metric_directions.
    legacy = git_repo / ".rgit" / "graph.db"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(legacy)
    conn.execute("CREATE TABLE features (id TEXT PRIMARY KEY)")  # minimal stand-in
    conn.commit()
    conn.close()
    # Opening through Store must add the table via the idempotent migration.
    store = Store.init(git_repo)
    store.set_metric_direction("eval_loss", "lower")
    assert store.get_metric_direction("eval_loss") == "lower"
