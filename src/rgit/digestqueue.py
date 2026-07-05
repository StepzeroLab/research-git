"""The backfill queue: persist scan results, stage units as proposals, ingest.

The lifecycle is pending -> staged -> done|skipped, all recorded in the
digest_units table so any session can resume where the last one stopped.
Staging reuses the live-capture pipeline (DiffSource -> segment_diff ->
Proposal, trigger="backfill"); `accept` is the non-interactive counterpart of
review: every candidate becomes an approved origin=backfill capsule, and dead
units get their outcome written from git facts, never from an agent.
"""
from __future__ import annotations
import math
from typing import Optional

from . import digestscan
from .curation import _capsule_from_candidate
from .gitutil import (CommitDiffSource, EmptyTreeRangeDiffSource,
                      RangeDiffSource)
from .segmenter import Segmenter, segment_diff
from .store.models import DigestUnit, ResultSummary
from .store.store import Store

BATCH_DEFAULT = 10


def scan_into_store(store: Store, *, range_spec: Optional[str] = None,
                    mode: Optional[str] = None, window: Optional[int] = None,
                    all_history: bool = False, now: str = "") -> dict:
    """Run the deterministic scan and persist it. Idempotent and incremental:
    unit ids are sha-set hashes, so re-running only adds unseen units."""
    mode = mode or store.get_digest_meta("mode") or "layered"
    if mode not in digestscan.MODES:
        raise ValueError(f"unknown digest mode {mode!r}; "
                         f"expected one of {', '.join(digestscan.MODES)}")
    res = digestscan.scan(store.root, range_spec=range_spec,
                          window=window if window is not None
                          else digestscan.DEFAULT_WINDOW,
                          all_history=all_history)
    new = 0
    for u in res["units"]:
        unit = DigestUnit(id=u["id"], kind=u["kind"], shas=u["shas"],
                          score=u["score"], status=u["status"],
                          skip_reason=u["skip_reason"], meta=u["meta"],
                          created_at=now)
        if store.add_digest_unit(unit):
            new += 1
    store.set_digest_meta("mode", mode)
    store.set_digest_meta("head_at_scan", res["head"])
    if range_spec:
        store.set_digest_meta("range", range_spec)
    pending = pending_units(store)
    return {"mode": mode, "units_new": new,
            "units_total": len(store.list_digest_units()),
            "pending": len(pending),
            "batches_remaining": math.ceil(len(pending) / BATCH_DEFAULT),
            "total_mainline": res["total_mainline"],
            "window_applied": res["window_applied"],
            "shallow": res["shallow"], "head_at_scan": res["head"]}


def pending_units(store: Store) -> list[DigestUnit]:
    """Pending units the current mode wants, in queue (score) order."""
    mode = store.get_digest_meta("mode") or "layered"
    units = store.list_digest_units("pending")
    if mode == "trunk":
        return [u for u in units if u.kind == "landed"]
    if mode == "dead":
        return [u for u in units if u.kind == "dead"]
    return units                     # layered / archaeology: everything ranked


def _source_for(unit: DigestUnit):
    shas = unit.shas
    if unit.meta.get("merge"):
        return RangeDiffSource(f"{shas[-1]}^1..{shas[-1]}")
    if len(shas) == 1:
        return CommitDiffSource(shas[0])       # --root handles a root commit
    if unit.meta.get("has_root"):
        return EmptyTreeRangeDiffSource(shas[-1])
    return RangeDiffSource(f"{shas[0]}^..{shas[-1]}")


def _staged_item(store: Store, unit: DigestUnit, prop) -> dict:
    diff = store.objects.get(prop.diff_ref).decode(errors="replace")
    oversized = bool(unit.meta.get("oversized")) or \
        len(diff.encode("utf-8", errors="replace")) > digestscan.UNIT_MAX_DIFF_BYTES
    return {"unit_id": unit.id, "kind": unit.kind, "score": unit.score,
            "proposal_id": prop.id, "meta": unit.meta, "diff": diff,
            "candidates": prop.candidates, "oversized": oversized}


def next_batch(store: Store, *, batch: int = BATCH_DEFAULT,
               segmenter: Optional[Segmenter] = None, now: str = "") -> list[dict]:
    """Reconcile staged work, then stage the next highest-ranked units.

    Crash-safe by construction: staged units whose proposal is still open are
    re-emitted (never re-staged); ones resolved or dismissed through another
    path are reconciled to done / skipped=user instead of duplicating work.
    """
    if segmenter is None:
        from .segmenter import HeuristicSegmenter
        segmenter = HeuristicSegmenter()
    out: list[dict] = []
    for unit in store.list_digest_units("staged"):
        prop = store.get_proposal(unit.proposal_id)
        if prop.status == "resolved":
            store.update_digest_unit(unit.id, status="done")
        elif prop.status == "dismissed":
            store.update_digest_unit(unit.id, status="skipped", skip_reason="user")
        elif len(out) < batch:
            out.append(_staged_item(store, unit, prop))
    for unit in pending_units(store):
        if len(out) >= batch:
            break
        pid = segment_diff(store, "backfill", segmenter, run_id=None, now=now,
                           source=_source_for(unit))
        if pid is None:
            store.update_digest_unit(unit.id, status="skipped",
                                     skip_reason="empty")
            continue
        store.update_digest_unit(unit.id, status="staged", proposal_id=str(pid))
        out.append(_staged_item(store, unit, store.get_proposal(str(pid))))
    return out


def _dead_outcome(unit: DigestUnit) -> ResultSummary:
    """Outcome facts come from git, never from the agent."""
    m = unit.meta
    if m.get("reverted_by"):
        when = m.get("revert_date", "")
        notes = f"reverted by {m['reverted_by'][:12]}"
        if when:
            notes += f" on {when}"
        return ResultSummary(verdict=None, key_delta=None,
                             failure_reason=m.get("revert_subject"), notes=notes)
    return ResultSummary(verdict=None, key_delta=None, failure_reason=None,
                         notes="files deleted from HEAD")


def accept(store: Store, proposal_id: str, now: str = "") -> dict:
    """Non-interactive ingestion: every candidate -> approved backfill capsule."""
    unit = store.digest_unit_by_proposal(proposal_id)
    if unit is None:
        raise KeyError(f"no digest unit staged for proposal {proposal_id!r}")
    prop = store.get_proposal(proposal_id)
    if prop.status != "open":
        raise ValueError(
            f"proposal {proposal_id!r} is {prop.status}, not open; cannot accept")
    if not prop.candidates:
        store.set_proposal_status(proposal_id, "dismissed")
        store.update_digest_unit(unit.id, status="skipped", skip_reason="infra")
        return {"unit_id": unit.id, "capsules": [], "skipped": "infra"}
    base = prop.source_commit or unit.shas[-1]
    capsules: list[list[str]] = []
    for idx, cand in enumerate(prop.candidates):
        fid = _capsule_from_candidate(store, prop, idx, base, origin="backfill")
        if unit.kind == "dead":
            store.update_capsule(fid, result_summary=_dead_outcome(unit))
        capsules.append([cand["name"], fid])
    store.set_proposal_status(proposal_id, "resolved")
    store.update_digest_unit(unit.id, status="done",
                             capsule_ids=[fid for _, fid in capsules])
    return {"unit_id": unit.id, "capsules": capsules}


def skip_unit(store: Store, unit_id: str) -> None:
    unit = store.get_digest_unit(unit_id)
    if unit.proposal_id:
        prop = store.get_proposal(unit.proposal_id)
        if prop.status == "open":
            store.set_proposal_status(unit.proposal_id, "dismissed")
    store.update_digest_unit(unit_id, status="skipped", skip_reason="user")


def clear(store: Store) -> dict:
    """The regret channel: delete every backfill capsule (edges cascade) and
    put digested/staged units back in the queue. Hand-made capsules and
    deliberate skips (infra/user) are untouched."""
    removed = 0
    for cap in store.list_features():
        if cap.origin == "backfill":
            store.delete_feature(cap.id)
            removed += 1
    reset = 0
    for unit in store.list_digest_units():
        if unit.status == "staged" and unit.proposal_id:
            prop = store.get_proposal(unit.proposal_id)
            if prop.status == "open":
                store.set_proposal_status(unit.proposal_id, "dismissed")
        if unit.status in ("done", "staged"):
            store.reset_digest_unit(unit.id)
            reset += 1
    return {"capsules_removed": removed, "units_reset": reset}


def status(store: Store) -> dict:
    units = store.list_digest_units()
    by_status: dict[str, int] = {}
    for u in units:
        by_status[u.status] = by_status.get(u.status, 0) + 1
    pending = pending_units(store)
    return {"mode": store.get_digest_meta("mode"),
            "range": store.get_digest_meta("range"),
            "head_at_scan": store.get_digest_meta("head_at_scan"),
            "units_total": len(units), "by_status": by_status,
            "pending_in_mode": len(pending),
            "dead_pending": sum(1 for u in pending if u.kind == "dead"),
            "batches_remaining": math.ceil(len(pending) / BATCH_DEFAULT)}
