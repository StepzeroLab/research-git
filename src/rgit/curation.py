from __future__ import annotations
from typing import Optional

from .gitutil import current_commit
from .store.models import Capsule, CodeSlice
from .store.store import Store


def _require_open(prop, proposal_id: str, verb: str) -> None:
    """Guard the write path: reject a proposal that is no longer open.

    Shared by approve/decide/dismiss so re-resolving a proposal can't create a
    duplicate capsule. `verb` carries the action (and any parenthetical) so each
    caller keeps its own tailored message.
    """
    if prop.status != "open":
        raise ValueError(
            f"proposal {proposal_id!r} is {prop.status}, not open; cannot {verb}")


def approve(store: Store, proposal_id: str, candidate_index: int = 0,
            name: Optional[str] = None) -> str:
    """Turn one candidate into an approved Capsule; link it to the run.

    When `name` matches a candidate's own name, that candidate is selected by
    name (and `candidate_index` is ignored). This is the robust path for a
    proposal with several candidates: `--approve <pid> --name <candidate-name>`
    always picks the right one, so a forgotten `--index` can't silently approve
    (and mislabel) the wrong candidate. Otherwise `candidate_index` is used.
    """
    prop = store.get_proposal(proposal_id)
    _require_open(prop, proposal_id,
                  "approve (re-approving would create a duplicate capsule)")
    if not prop.candidates:
        raise ValueError(f"proposal {proposal_id!r} has no candidates to approve")
    by_name = [i for i, c in enumerate(prop.candidates) if c.get("name") == name]
    if name is not None and not by_name:
        # A typo must fail loudly, not silently approve (and mislabel) candidate 0.
        available = [c.get("name") for c in prop.candidates]
        raise ValueError(
            f"no candidate named {name!r} in proposal {proposal_id!r}; "
            f"available: {available}")
    idx = by_name[0] if name is not None else candidate_index
    if idx < 0 or idx >= len(prop.candidates):
        raise ValueError(
            f"candidate index {idx} out of range for proposal {proposal_id!r} "
            f"with {len(prop.candidates)} candidate(s)")
    fid = _capsule_from_candidate(store, prop, idx)
    store.set_proposal_status(proposal_id, "resolved")
    return fid


def _capsule_from_candidate(store: Store, prop, idx: int) -> str:
    """Materialize candidate `idx` as an approved Capsule with its edges.

    Shared by approve() and decide(); does not touch proposal status.
    """
    cand = prop.candidates[idx]
    # A committed-diff capture pins the capsule to the commit that contains the
    # change; only worktree captures fall back to HEAD at approve time.
    base = prop.source_commit or current_commit(store.root)
    cap = Capsule(
        id="", name=cand["name"], intent=cand["intent"],
        status="approved", base_commit=base,
        knobs=cand.get("knobs", {}), data_assumptions=cand.get("data_assumptions"),
        resurrection_guide=cand.get("resurrection_guide"), result_summary=None,
        payload_hash=None,
        code_slices=[CodeSlice(**c) for c in cand["code_slices"]])
    fid = store.add_feature(cap)
    for slice_ in cap.code_slices:                       # touches edges
        store.add_edge(fid, f"module:{slice_.file}", "touches")
    if prop.run_id:                                      # produced edge
        store.add_edge(fid, prop.run_id, "produced")
    for src in (prop.from_features or []):               # regenerated from -> variant_of
        store.add_edge(fid, src, "variant_of")
    return fid


def decide(store: Store, proposal_id: str, keep: list[str]) -> list[tuple[str, str]]:
    """Approve the named candidates, drop the rest, resolve the proposal.

    One call expresses a whole review decision ("keep these"), so an agent
    driving a conversational review executes the user's answer atomically.
    Everything is validated before anything is written: an unknown name
    rejects the whole call with no partial writes.
    """
    prop = store.get_proposal(proposal_id)
    _require_open(prop, proposal_id,
                  "decide (re-deciding would create duplicate capsules)")
    ordered = list(dict.fromkeys(keep))          # dedupe, keep order
    if not ordered:
        raise ValueError("nothing to keep; use dismiss to drop the whole proposal")
    by_name: dict[str, int] = {}
    for i, c in enumerate(prop.candidates):      # first occurrence wins, like approve()
        by_name.setdefault(c.get("name"), i)
    unknown = [n for n in ordered if n not in by_name]
    if unknown:
        available = [c.get("name") for c in prop.candidates]
        raise ValueError(
            f"no candidate(s) named {unknown!r} in proposal {proposal_id!r}; "
            f"available: {available}")
    approved = [(n, _capsule_from_candidate(store, prop, by_name[n]))
                for n in ordered]
    store.set_proposal_status(proposal_id, "resolved")
    return approved


def dismiss(store: Store, proposal_id: str) -> None:
    prop = store.get_proposal(proposal_id)
    _require_open(prop, proposal_id, "dismiss")
    store.set_proposal_status(proposal_id, "dismissed")


_CODE_SLICE_FIELDS = {"file", "symbol", "anchor", "code", "kind"}


def validate_candidates(candidates: object) -> None:
    """Reject malformed candidate input before it is stored.

    `resegment` accepts arbitrary JSON from the host agent; without this a
    missing/extra field only surfaces later as an uncaught KeyError/TypeError in
    `approve()` or the `review` listing. Raises ValueError with a clear message.
    An empty list is valid (a deliberate 0-candidate proposal).
    """
    if not isinstance(candidates, list):
        raise ValueError("candidates must be a JSON list of candidate objects")
    for i, c in enumerate(candidates):
        where = f"candidate {i}"
        if not isinstance(c, dict):
            raise ValueError(f"{where} must be a JSON object")
        for field in ("name", "intent"):
            if not isinstance(c.get(field), str) or not c[field].strip():
                raise ValueError(f"{where} is missing a non-empty {field!r}")
        slices = c.get("code_slices")
        if not isinstance(slices, list):
            raise ValueError(f"{where} must have a 'code_slices' list")
        for j, s in enumerate(slices):
            if not isinstance(s, dict):
                raise ValueError(f"{where} code_slices[{j}] must be a JSON object")
            missing = _CODE_SLICE_FIELDS - set(s)
            if missing:
                raise ValueError(f"{where} code_slices[{j}] missing field(s): "
                                 f"{', '.join(sorted(missing))}")
            extra = set(s) - _CODE_SLICE_FIELDS
            if extra:
                raise ValueError(f"{where} code_slices[{j}] has unknown field(s): "
                                 f"{', '.join(sorted(extra))}")
