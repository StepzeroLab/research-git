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
