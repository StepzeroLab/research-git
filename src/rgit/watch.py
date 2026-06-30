from __future__ import annotations
from typing import Callable, Optional

from .gitutil import _snapshot_paths, diff_since
from .segmenter import HeuristicSegmenter, segment_diff
from .store.store import Store


def snapshot(store: Store) -> dict:
    """Cheap worktree fingerprint: {relpath: mtime_ns} over tracked/untracked-not-
    ignored files, excluding the .rgit/ store itself. No file contents."""
    snap: dict[str, int] = {}
    for rel in _snapshot_paths(store.root, exclude_root=store.dir):
        p = store.root / rel
        try:
            snap[rel] = p.stat().st_mtime_ns
        except FileNotFoundError:
            continue
    return snap


def tick(store: Store, last_snapshot: dict, now: str) -> tuple[dict, Optional[str]]:
    """One watch step. Returns (new_snapshot, staged_proposal_id_or_None).

    Stages a free Phase-1 proposal only when the tree is IDLE (unchanged since the
    prior snapshot) AND has an uncommitted diff that is not already staged in an
    open proposal. Deterministic given (snapshot, tree, now). Never calls an agent.
    """
    cur = snapshot(store)
    if cur != last_snapshot:
        return cur, None                                  # still moving — debounce
    diff = diff_since(store.root, "HEAD")
    if not diff.strip():
        return cur, None                                  # idle but nothing to capture
    for p in store.list_proposals("open"):
        if p.diff_ref and store.objects.get(p.diff_ref).decode() == diff:
            return cur, None                              # this exact state already staged
    pid = segment_diff(store, "watch", HeuristicSegmenter(), run_id=None, now=now)
    return cur, pid


def loop(store: Store, interval: float, idle: float,
         now_fn: Callable[[], str]) -> None:  # pragma: no cover - timing loop
    """Foreground watch loop. Background it with `nohup rgit watch &` or launchd."""
    import time
    last = snapshot(store)
    while True:
        time.sleep(max(interval, idle))
        last, pid = tick(store, last, now_fn())
        if pid:
            print(f"staged proposal {pid}")
