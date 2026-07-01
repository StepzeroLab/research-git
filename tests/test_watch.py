from rgit.watch import snapshot, tick
from rgit.store.store import Store
from rgit.store.models import Proposal


def _dirty(repo):
    (repo / "model.py").write_text("def forward(x):\n    return x + 1\n")


def test_tick_waits_for_idle_then_stages(git_repo):
    store = Store.init(git_repo)
    _dirty(git_repo)
    snap = snapshot(store)
    # first tick: tree moved relative to an empty prior snapshot -> no staging
    snap2, pid = tick(store, {}, now="t1")
    assert pid is None
    # second tick: snapshot unchanged since `snap2` -> idle -> stage
    snap3, pid2 = tick(store, snap2, now="t2")
    assert pid2 is not None
    assert len(store.list_proposals("open")) == 1


def test_tick_dedupes_already_staged_state(git_repo):
    store = Store.init(git_repo)
    _dirty(git_repo)
    snap = snapshot(store)
    _, pid = tick(store, snap, now="t1")     # idle immediately (same snapshot) -> stage
    assert pid is not None
    _, pid2 = tick(store, snap, now="t2")    # same diff already staged -> skip
    assert pid2 is None
    assert len(store.list_proposals("open")) == 1


def test_tick_idle_clean_tree_stages_nothing(git_repo):
    store = Store.init(git_repo)
    snap = snapshot(store)
    _, pid = tick(store, snap, now="t1")
    assert pid is None


def test_tick_decodes_existing_proposal_diff_tolerantly(git_repo):
    store = Store.init(git_repo)
    (git_repo / "latin.py").write_bytes(b"def cafe():\n    return 'caf\xe9'\n")
    # Simulate an older/corrupt proposal object with non-UTF-8 bytes. The watch
    # dedupe pass must not crash before it can stage the current diff.
    diff_ref = store.objects.put(b"diff with invalid byte \xff")
    store.add_proposal(Proposal(id="", trigger="manual", diff_ref=diff_ref,
                                candidates=[]))
    snap = snapshot(store)
    _, pid = tick(store, snap, now="t1")
    assert pid is not None
