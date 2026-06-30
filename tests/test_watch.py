from rgit.watch import snapshot, tick
from rgit.store.store import Store


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
