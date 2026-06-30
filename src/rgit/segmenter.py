from __future__ import annotations
from typing import Optional, Protocol

from .astmap import changed_symbols
from .gitutil import diff_since
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
        if line.startswith("diff --git"):
            if current is not None:
                sections[current] = "\n".join(lines)
            lines = [line]
            current = None
        else:
            lines.append(line)
            if line.startswith("+++ b/"):
                current = line[len("+++ b/"):].strip()
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
        for file, syms in by_file.items():
            stem = file.rsplit("/", 1)[-1].rsplit(".", 1)[0]
            code = file_diffs.get(file, "")
            candidates.append({
                "name": f"{stem}-changes",
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


def segment_diff(store: Store, trigger: str, segmenter: Segmenter,
                 run_id: Optional[str], from_features: Optional[list[str]] = None,
                 now: str = "") -> str:
    """Diff the working tree vs HEAD, segment it, store an open Proposal, and
    record comment-in/out toggle events against the capsules they touch.

    `from_features` records the capsule(s) this work regenerated, so approving the
    resulting proposal links the new capsule `variant_of` those sources.
    """
    from .toggles import detect_toggles, map_to_capsules
    diff = diff_since(store.root, "HEAD")
    symbols = changed_symbols(diff, store.root)
    candidates = segmenter.segment(diff, symbols)
    diff_ref = store.objects.put(diff.encode())
    pid = store.add_proposal(Proposal(
        id="", trigger=trigger, diff_ref=diff_ref,
        candidates=candidates, status="open", run_id=run_id,
        from_features=from_features))
    for ev in map_to_capsules(store, detect_toggles(diff)):
        store.add_event(ev["capsule_id"], ev["kind"], run_id, now)
    return pid
