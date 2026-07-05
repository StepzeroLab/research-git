import json
from rgit.store.store import Store
from rgit.store.models import Capsule, CodeSlice, Run


def make_cap(name="contrastive-loss", intent="add aux loss", base="abc"):
    return Capsule(
        id="", name=name, intent=intent, status="approved", base_commit=base,
        knobs={"lambda": 0.1}, data_assumptions="normalized embeddings",
        resurrection_guide="wrap compute_loss", result_summary=None,
        payload_hash=None,
        code_slices=[CodeSlice("model.py", "compute_loss", "L1", "loss+=x", "insert")],
    )


def test_add_and_get_feature_persists_payload(git_repo):
    store = Store.init(git_repo)
    cap = make_cap()
    fid = store.add_feature(cap)
    got = store.get_feature(fid)
    assert got.name == "contrastive-loss"
    assert got.code_slices[0].symbol == "compute_loss"   # came back from object store
    assert got.payload_hash is not None


def test_edges_and_neighbors(git_repo):
    store = Store.init(git_repo)
    a = store.add_feature(make_cap(name="a"))
    b = store.add_feature(make_cap(name="b"))
    store.add_edge(a, b, "depends_on")
    assert store.neighbors(a, "depends_on") == [b]


def test_add_and_get_run(git_repo):
    store = Store.init(git_repo)
    rid = store.add_run(Run("", "python train.py", "aa", {"acc": 0.9},
                            "abc", None, "2026-06-16T00:00:00"))
    assert store.get_run(rid).metrics == {"acc": 0.9}


def test_open_finds_rgit_upward(git_repo):
    Store.init(git_repo)
    sub = git_repo / "deep" / "nested"
    sub.mkdir(parents=True)
    store = Store.open(sub)
    assert store.root == git_repo


def test_set_proposal_candidates_replaces_them(git_repo):
    from rgit.store.models import Proposal
    store = Store.init(git_repo)
    pid = store.add_proposal(Proposal(id="", trigger="manual", diff_ref="d",
                                      candidates=[{"name": "rough"}]))
    store.set_proposal_candidates(pid, [{"name": "refined", "intent": "better"}])
    assert store.get_proposal(pid).candidates == [{"name": "refined", "intent": "better"}]


def test_update_capsule_refreshes_resurrection_guide(git_repo):
    store = Store.init(git_repo)
    fid = store.add_feature(make_cap())
    store.update_capsule(fid, resurrection_guide="REFRESHED GUIDE")
    assert store.get_feature(fid).resurrection_guide == "REFRESHED GUIDE"


def test_proposal_from_features_roundtrips(git_repo):
    from rgit.store.models import Proposal
    store = Store.init(git_repo)
    pid = store.add_proposal(Proposal(id="", trigger="run", diff_ref="d",
                                      candidates=[], from_features=["feat_a", "feat_b"]))
    assert store.get_proposal(pid).from_features == ["feat_a", "feat_b"]


def test_run_roundtrip_carries_returncode(git_repo):
    from rgit.store.store import Store
    from rgit.store.models import Run
    store = Store.init(git_repo)
    rid = store.add_run(Run(id="", cmd="x", artifact_hash="h", metrics=None,
                            base_commit="abc", env=None, created_at="t",
                            returncode=1))
    assert store.get_run(rid).returncode == 1


def test_add_and_latest_event(git_repo):
    from rgit.store.store import Store
    store = Store.init(git_repo)
    store.add_event("feat_1", "deactivate", "run_1", "t1")
    store.add_event("feat_1", "activate", "run_2", "t2")
    latest = store.latest_event("feat_1")
    assert latest.kind == "activate"
    assert store.latest_event("feat_unknown") is None


def test_latest_event_tiebreaks_on_insertion_order_for_equal_timestamps(git_repo):
    from rgit.store.store import Store
    store = Store.init(git_repo)
    # same created_at for both — the later-inserted one must win deterministically
    store.add_event("feat_z", "activate", "run_1", "t")
    store.add_event("feat_z", "deactivate", "run_1", "t")
    assert store.latest_event("feat_z").kind == "deactivate"


def test_open_migrates_legacy_v1_db(git_repo):
    # A pre-v2 graph: runs table without the returncode column. Opening it must
    # run the migration so returncode-aware writes work (regression: migration
    # used to run only at `rgit init`, never on open).
    import sqlite3
    from rgit.store.store import Store
    from rgit.store.models import Run
    rgit_dir = git_repo / ".rgit"
    rgit_dir.mkdir()
    conn = sqlite3.connect(rgit_dir / "graph.db")
    conn.execute("CREATE TABLE runs (id TEXT PRIMARY KEY, cmd TEXT NOT NULL, "
                 "artifact_hash TEXT NOT NULL, metrics TEXT, base_commit TEXT NOT NULL, "
                 "env TEXT, created_at TEXT NOT NULL)")
    conn.commit(); conn.close()
    store = Store.open(git_repo)
    rid = store.add_run(Run(id="", cmd="x", artifact_hash="h", metrics=None,
                            base_commit="b", env=None, created_at="t", returncode=2))
    assert store.get_run(rid).returncode == 2


def test_set_proposal_status_unknown_id_raises(git_repo):
    import pytest
    store = Store.init(git_repo)
    with pytest.raises(KeyError):
        store.set_proposal_status("prop_does_not_exist", "resolved")


def test_set_proposal_candidates_unknown_id_raises(git_repo):
    import pytest
    store = Store.init(git_repo)
    with pytest.raises(KeyError):
        store.set_proposal_candidates("prop_does_not_exist", [])


def test_proposal_source_commit_round_trip(git_repo):
    from rgit.store.models import Proposal
    store = Store.init(git_repo)
    pid = store.add_proposal(Proposal(id="", trigger="commit", diff_ref="d",
                                      candidates=[], source_commit="c" * 40))
    assert store.get_proposal(pid).source_commit == "c" * 40


def test_proposal_source_commit_defaults_to_none(git_repo):
    from rgit.store.models import Proposal
    store = Store.init(git_repo)
    pid = store.add_proposal(Proposal(id="", trigger="manual", diff_ref="d",
                                      candidates=[]))
    assert store.get_proposal(pid).source_commit is None


def test_open_migrates_proposals_without_source_commit(git_repo):
    # A pre-source_commit graph must gain the column on open, and both the
    # legacy row and new commit-sourced writes must read back correctly.
    import sqlite3
    from rgit.store.models import Proposal
    rgit_dir = git_repo / ".rgit"
    rgit_dir.mkdir()
    conn = sqlite3.connect(rgit_dir / "graph.db")
    conn.execute("CREATE TABLE proposals (id TEXT PRIMARY KEY, trigger TEXT NOT NULL, "
                 "diff_ref TEXT NOT NULL, candidates TEXT NOT NULL, "
                 "status TEXT NOT NULL DEFAULT 'open', run_id TEXT, from_features TEXT)")
    conn.execute("INSERT INTO proposals VALUES ('prop_old', 'manual', 'd', '[]', "
                 "'open', NULL, NULL)")
    conn.commit(); conn.close()
    store = Store.open(git_repo)
    assert store.get_proposal("prop_old").source_commit is None
    pid = store.add_proposal(Proposal(id="", trigger="commit", diff_ref="d",
                                      candidates=[], source_commit="c" * 40))
    assert store.get_proposal(pid).source_commit == "c" * 40


def test_open_readonly_refuses_writes_and_skips_migrations(git_repo):
    import sqlite3
    Store.init(git_repo)
    ro = Store.open(git_repo, readonly=True)
    try:
        with __import__("pytest").raises(sqlite3.OperationalError):
            ro.conn.execute("INSERT INTO edges VALUES ('a','b','overlaps')")
    finally:
        ro.conn.close()


def test_open_readonly_missing_db_raises_filenotfound(git_repo):
    import pytest
    (git_repo / ".rgit").mkdir()          # dir exists, graph.db does not
    with pytest.raises(FileNotFoundError):
        Store.open(git_repo, readonly=True)


def test_objectstore_path_for_matches_layout_and_no_create(tmp_path):
    from rgit.store.objects import ObjectStore
    target = tmp_path / "objects"
    ro = ObjectStore(target, create=False)
    assert not target.exists()            # create=False must not mkdir
    digest = "ab" + "c" * 62
    assert ro.path_for(digest) == target / "ab" / ("c" * 62)
    assert ro.path_for(digest) == ro._path(digest)   # legacy alias intact


def _capsule(name="cap", origin="live"):
    from rgit.store.models import Capsule, CodeSlice
    return Capsule(id="", name=name, intent="i", status="approved",
                   base_commit="c", knobs={}, data_assumptions=None,
                   resurrection_guide=None, result_summary=None, payload_hash=None,
                   code_slices=[CodeSlice("m.py", "f", None, "x", "wrap")],
                   origin=origin)


def test_capsule_origin_roundtrip(tmp_path):
    from rgit.store.store import Store
    store = Store(tmp_path)
    fid = store.add_feature(_capsule(origin="backfill"))
    assert store.get_feature(fid).origin == "backfill"
    live = store.add_feature(_capsule(name="live-one"))
    assert store.get_feature(live).origin == "live"


def test_delete_feature_cascades_edges(tmp_path):
    import pytest
    from rgit.store.store import Store
    store = Store(tmp_path)
    a = store.add_feature(_capsule(name="a"))
    b = store.add_feature(_capsule(name="b"))
    store.add_edge(a, b, "depends_on")
    store.add_edge(b, a, "overlaps")
    store.delete_feature(a)
    with pytest.raises(KeyError):
        store.get_feature(a)
    assert store.neighbors(b, "overlaps") == []
    rows = store.conn.execute("SELECT * FROM edges WHERE src=? OR dst=?",
                              (a, a)).fetchall()
    assert rows == []
    with pytest.raises(KeyError):
        store.delete_feature("feat_missing")


def test_digest_unit_crud_and_queue_order(tmp_path):
    import pytest
    from rgit.store.models import DigestUnit
    from rgit.store.store import Store
    store = Store(tmp_path)
    low = DigestUnit(id="dig_low", kind="landed", shas=["s1"], score=1.0,
                     meta={"subjects": ["low"]}, created_at="t")
    high = DigestUnit(id="dig_high", kind="dead", shas=["s2", "s3"], score=9.0,
                      meta={"subjects": ["high"]}, created_at="t")
    assert store.add_digest_unit(low) is True
    assert store.add_digest_unit(high) is True
    assert store.add_digest_unit(high) is False              # idempotent rescan
    units = store.list_digest_units()
    assert [u.id for u in units] == ["dig_high", "dig_low"]  # score DESC
    assert units[0].shas == ["s2", "s3"]
    assert units[0].meta == {"subjects": ["high"]}

    store.update_digest_unit("dig_high", status="staged", proposal_id="prop_1")
    assert store.get_digest_unit("dig_high").proposal_id == "prop_1"
    assert store.digest_unit_by_proposal("prop_1").id == "dig_high"
    assert store.digest_unit_by_proposal("prop_none") is None
    assert [u.id for u in store.list_digest_units("pending")] == ["dig_low"]

    store.update_digest_unit("dig_high", status="done",
                             capsule_ids=["feat_1", "feat_2"])
    assert store.get_digest_unit("dig_high").capsule_ids == ["feat_1", "feat_2"]

    store.reset_digest_unit("dig_high")
    fresh = store.get_digest_unit("dig_high")
    assert fresh.status == "pending"
    assert fresh.proposal_id is None and fresh.capsule_ids == []

    with pytest.raises(KeyError):
        store.get_digest_unit("dig_missing")
    with pytest.raises(KeyError):
        store.update_digest_unit("dig_missing", status="done")


def test_digest_meta_upsert(tmp_path):
    from rgit.store.store import Store
    store = Store(tmp_path)
    assert store.get_digest_meta("mode") is None
    store.set_digest_meta("mode", "layered")
    store.set_digest_meta("mode", "dead")
    assert store.get_digest_meta("mode") == "dead"
