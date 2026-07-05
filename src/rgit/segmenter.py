from __future__ import annotations
from typing import Optional, Protocol

from .astmap import changed_symbols
from .gitutil import DiffSource, WorktreeDiffSource, parse_git_diff_header
from .store.models import Proposal
from .store.store import Store


class Segmenter(Protocol):
    def segment(self, diff: str, symbols: list[dict]) -> list[dict]:
        """Return a list of candidate capsule dicts:
        {name, intent, code_slices[{file,symbol,anchor,code,kind}],
         knobs, data_assumptions, resurrection_guide, confidence}."""
        ...


class MockSegmenter:
    """Deterministic segmenter for tests."""

    def __init__(self, candidates: list[dict]):
        self.candidates = candidates
        self.last_symbols: list[dict] = []

    def segment(self, diff: str, symbols: list[dict]) -> list[dict]:
        self.last_symbols = symbols
        return self.candidates


def _diff_by_file(diff: str) -> dict[str, str]:
    """Split a unified diff into per-file sections keyed by the new path."""
    sections: dict[str, str] = {}
    current: Optional[str] = None
    lines: list[str] = []
    for line in diff.splitlines():
        if line.startswith("research-git: skipped "):
            if current is not None:
                sections[current] = "\n".join(lines)
            lines = []
            current = None
            continue
        if line.startswith("diff --git"):
            if current is not None:
                sections[current] = "\n".join(lines)
            lines = [line]
            current = None
        else:
            lines.append(line)
            matched, path = parse_git_diff_header(line, "+++")
            if matched:
                current = path
    if current is not None:
        sections[current] = "\n".join(lines)
    return sections


class HeuristicSegmenter:
    """Free, no-LLM default segmenter.

    Groups the changed symbols by file into one rough candidate per file, so the
    autonomous triggers always stage *something* at zero cost and zero API
    credits. Boundaries are deliberately crude (confidence 0.3) — the host agent
    re-segments them into high-quality capsules on demand via the MCP `resegment`
    tool, or you refine them at review time.
    """

    def segment(self, diff: str, symbols: list[dict]) -> list[dict]:
        by_file: dict[str, list[str]] = {}
        for s in symbols:
            by_file.setdefault(s["file"], []).append(s["symbol"])
        file_diffs = _diff_by_file(diff)
        candidates: list[dict] = []
        seen_names: dict[str, int] = {}
        for file, syms in by_file.items():
            stem = file.rsplit("/", 1)[-1].rsplit(".", 1)[0]
            # Basename stems collide (e.g. two packages' __init__.py). Candidate
            # names must be unique, so disambiguate deterministically: the first
            # occurrence keeps the plain name, later ones get -2, -3, ...
            base_name = f"{stem}-changes"
            count = seen_names.get(base_name, 0) + 1
            seen_names[base_name] = count
            name = base_name if count == 1 else f"{base_name}-{count}"
            code = file_diffs.get(file, "")
            candidates.append({
                "name": name,
                "intent": f"Changes to {', '.join(syms)} in {file}",
                "code_slices": [{"file": file, "symbol": sym, "anchor": None,
                                 "code": code, "kind": "wrap"} for sym in syms],
                "knobs": {},
                "data_assumptions": None,
                "resurrection_guide":
                    f"Re-apply the changes to {', '.join(syms)} in {file}.",
                "confidence": 0.3,
            })
        return candidates


class CaptureResult(str):
    """Proposal id plus whether this capture created a new proposal.

    A str subclass so every existing caller keeps treating it as the id.
    """

    created: bool

    def __new__(cls, proposal_id: str, *, created: bool) -> "CaptureResult":
        obj = str.__new__(cls, proposal_id)
        obj.created = created
        return obj


def _existing_open_proposal_for_diff(store: Store, diff_ref: str) -> Optional[str]:
    for prop in store.list_proposals("open"):
        if prop.diff_ref == diff_ref:
            return prop.id
    return None


def segment_diff(store: Store, trigger: str, segmenter: Segmenter,
                 run_id: Optional[str], from_features: Optional[list[str]] = None,
                 now: str = "",
                 source: Optional[DiffSource] = None) -> Optional[CaptureResult]:
    """Take a diff from `source`, segment it, store an open Proposal, and
    record comment-in/out toggle events against the capsules they touch.

    `source` selects where the diff comes from — the working tree vs HEAD by
    default, or a committed change (`CommitDiffSource` / `RangeDiffSource`) so
    work that was already committed stays capturable. Symbols are resolved
    against the same source the diff came from, never blindly against the
    worktree.

    `from_features` records the capsule(s) this work regenerated, so approving the
    resulting proposal links the new capsule `variant_of` those sources.
    """
    from .toggles import detect_toggles, map_to_capsules
    if source is None:
        source = WorktreeDiffSource()
    diff = source.diff(store.root)
    if not diff.strip():
        return None
    # Content-addressed dedup: hook + manual double-fire on the same change
    # must return the already-open proposal, not stack a duplicate. Resolved
    # or dismissed proposals never block a re-capture.
    diff_ref = store.objects.put(diff.encode("utf-8", errors="replace"))
    existing = _existing_open_proposal_for_diff(store, diff_ref)
    if existing is not None:
        return CaptureResult(existing, created=False)
    symbols = changed_symbols(
        diff, store.root,
        read_source=lambda file: source.read_new_side(store.root, file))
    candidates = segmenter.segment(diff, symbols)
    pid = store.add_proposal(Proposal(
        id="", trigger=trigger, diff_ref=diff_ref,
        candidates=candidates, status="open", run_id=run_id,
        from_features=from_features,
        source_commit=source.source_commit(store.root)))
    for ev in map_to_capsules(store, detect_toggles(diff)):
        store.add_event(ev["capsule_id"], ev["kind"], run_id, now)
    return CaptureResult(pid, created=True)
