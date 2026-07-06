import pytest
from conftest import commit_file, make_candidate, revert_head
from rgit import digestqueue
from rgit.segmenter import HeuristicSegmenter
from rgit.store.store import Store

T0 = 1_700_000_000
DAY = 86_400
NOW = "2026-07-05T00:00:00"


def _scripted_store(history_repo):
    """landed feature commit + reverted experiment + docs-only commit."""
    commit_file(history_repo, "model.py", "def f(x):\n    return x\n",
                "feat: base model", when=T0)
    commit_file(history_repo, "exp.py", "def trick(x):\n    return x + 1\n",
                "exp: additive trick", when=T0 + 5 * DAY, author="u2")
    revert_head(history_repo, when=T0 + 6 * DAY, author="u2")
    commit_file(history_repo, "README.md", "# hi\n", "docs", when=T0 + 10 * DAY)
    store = Store.init(history_repo)
    return store


def test_scan_into_store_persists_and_is_idempotent(history_repo):
    store = _scripted_store(history_repo)
    res = digestqueue.scan_into_store(store, now=NOW)
    assert res["mode"] == "layered"
    assert res["units_new"] == res["units_total"] >= 3
    assert store.get_digest_meta("mode") == "layered"
    assert store.get_digest_meta("head_at_scan")
    again = digestqueue.scan_into_store(store, now=NOW)
    assert again["units_new"] == 0                       # INSERT OR IGNORE
    with pytest.raises(ValueError):
        digestqueue.scan_into_store(store, mode="bogus", now=NOW)


def test_mode_filters_pending(history_repo):
    store = _scripted_store(history_repo)
    digestqueue.scan_into_store(store, mode="dead", now=NOW)
    pend = digestqueue.pending_units(store)
    assert pend and all(u.kind == "dead" for u in pend)
    store.set_digest_meta("mode", "trunk")
    assert all(u.kind == "landed" for u in digestqueue.pending_units(store))


def test_next_batch_stages_proposals_with_backfill_trigger(history_repo):
    store = _scripted_store(history_repo)
    digestqueue.scan_into_store(store, now=NOW)
    items = digestqueue.next_batch(store, segmenter=HeuristicSegmenter(), now=NOW)
    assert items
    first = items[0]
    prop = store.get_proposal(first["proposal_id"])
    assert prop.trigger == "backfill"
    assert prop.source_commit                             # pinned to history
    assert "diff --git" in first["diff"]
    unit = store.get_digest_unit(first["unit_id"])
    assert unit.status == "staged"
    # a second call re-emits still-open staged items instead of restaging
    again = digestqueue.next_batch(store, segmenter=HeuristicSegmenter(), now=NOW)
    assert {i["proposal_id"] for i in items} >= {i["proposal_id"] for i in again[:len(items)]}


def test_accept_ingests_all_candidates_as_backfill(history_repo):
    store = _scripted_store(history_repo)
    digestqueue.scan_into_store(store, now=NOW)
    items = digestqueue.next_batch(store, segmenter=HeuristicSegmenter(), now=NOW)
    dead_item = next(i for i in items if i["kind"] == "dead")
    store.set_proposal_candidates(dead_item["proposal_id"],
                                  [make_candidate("dead-trick", file="exp.py",
                                                  symbol="trick")])
    res = digestqueue.accept(store, dead_item["proposal_id"], now=NOW)
    (name, fid), = res["capsules"]
    assert name == "dead-trick"
    cap = store.get_feature(fid)
    assert cap.origin == "backfill"
    assert cap.status == "approved"
    assert cap.base_commit == store.get_proposal(dead_item["proposal_id"]).source_commit
    assert "reverted by" in cap.result_summary.notes      # engine-written outcome
    assert store.get_proposal(dead_item["proposal_id"]).status == "resolved"
    unit = store.get_digest_unit(dead_item["unit_id"])
    assert unit.status == "done" and unit.capsule_ids == [fid]
    with pytest.raises(ValueError):
        digestqueue.accept(store, dead_item["proposal_id"], now=NOW)  # not open
    with pytest.raises(KeyError):
        digestqueue.accept(store, "prop_unknown", now=NOW)


def test_accept_zero_candidates_resolves_as_infra(history_repo):
    store = _scripted_store(history_repo)
    digestqueue.scan_into_store(store, now=NOW)
    items = digestqueue.next_batch(store, segmenter=HeuristicSegmenter(), now=NOW)
    item = items[0]
    store.set_proposal_candidates(item["proposal_id"], [])
    res = digestqueue.accept(store, item["proposal_id"], now=NOW)
    assert res["capsules"] == [] and res["skipped"] == "infra"
    assert store.get_digest_unit(item["unit_id"]).skip_reason == "infra"
    assert store.get_proposal(item["proposal_id"]).status == "dismissed"


def test_reconcile_externally_resolved_and_dismissed(history_repo):
    store = _scripted_store(history_repo)
    digestqueue.scan_into_store(store, now=NOW)
    items = digestqueue.next_batch(store, segmenter=HeuristicSegmenter(), now=NOW)
    a, b = items[0], items[1]
    store.set_proposal_status(a["proposal_id"], "resolved")   # someone else resolved it
    store.set_proposal_status(b["proposal_id"], "dismissed")
    digestqueue.next_batch(store, segmenter=HeuristicSegmenter(), now=NOW)
    assert store.get_digest_unit(a["unit_id"]).status == "done"
    got_b = store.get_digest_unit(b["unit_id"])
    assert got_b.status == "skipped" and got_b.skip_reason == "user"


def test_skip_unit_dismisses_open_proposal(history_repo):
    store = _scripted_store(history_repo)
    digestqueue.scan_into_store(store, now=NOW)
    items = digestqueue.next_batch(store, segmenter=HeuristicSegmenter(), now=NOW)
    item = items[0]
    digestqueue.skip_unit(store, item["unit_id"])
    unit = store.get_digest_unit(item["unit_id"])
    assert unit.status == "skipped" and unit.skip_reason == "user"
    assert store.get_proposal(item["proposal_id"]).status == "dismissed"


def test_clear_removes_backfill_capsules_and_resets_units(history_repo):
    store = _scripted_store(history_repo)
    digestqueue.scan_into_store(store, now=NOW)
    items = digestqueue.next_batch(store, segmenter=HeuristicSegmenter(), now=NOW)
    item = items[0]
    store.set_proposal_candidates(item["proposal_id"], [make_candidate("bf")])
    digestqueue.accept(store, item["proposal_id"], now=NOW)
    from rgit.store.models import Capsule, CodeSlice
    hand = store.add_feature(Capsule(
        id="", name="hand-made", intent="i", status="approved", base_commit="c",
        knobs={}, data_assumptions=None, resurrection_guide=None,
        result_summary=None, payload_hash=None,
        code_slices=[CodeSlice("m.py", "f", None, "x", "wrap")]))
    res = digestqueue.clear(store)
    assert res["capsules_removed"] == 1
    assert res["units_reset"] >= 1
    assert store.get_feature(hand).name == "hand-made"     # live capsule untouched
    assert store.get_digest_unit(item["unit_id"]).status == "pending"


def test_duplicate_diff_unit_marked_skipped_duplicate(history_repo):
    commit_file(history_repo, "base.py", "b = 1\n", "base", when=T0)
    x = commit_file(history_repo, "dup.py", "d = 1\n", "experiment",
                    when=T0 + 5 * DAY, author="u2")
    revert_head(history_repo, when=T0 + 6 * DAY, author="u2")
    z = commit_file(history_repo, "dup.py", "d = 1\n", "bring it back",
                    when=T0 + 20 * DAY)
    store = Store.init(history_repo)
    digestqueue.scan_into_store(store, now=NOW)
    digestqueue.next_batch(store, segmenter=HeuristicSegmenter(), now=NOW)
    units = {tuple(u.shas): u for u in store.list_digest_units()}
    dead, dup = units[(x,)], units[(z,)]
    assert dead.status == "staged"                      # higher score, staged first
    assert dup.status == "skipped" and dup.skip_reason == "duplicate"
    assert dup.meta["duplicate_of"] == dead.id


def test_status_reports_progress(history_repo):
    store = _scripted_store(history_repo)
    digestqueue.scan_into_store(store, now=NOW)
    st = digestqueue.status(store)
    assert st["mode"] == "layered"
    assert st["units_total"] >= 3
    assert st["pending_in_mode"] >= 1
    assert st["dead_pending"] >= 1
    assert st["batches_remaining"] >= 1
    assert st["by_status"]["pending"] >= 1
