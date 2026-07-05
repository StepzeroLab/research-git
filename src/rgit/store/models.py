from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Any, Optional


@dataclass
class CodeSlice:
    file: str
    symbol: Optional[str]
    anchor: Optional[str]
    code: str
    kind: str  # "add" | "wrap" | "insert"


@dataclass
class ResultSummary:
    verdict: Optional[str] = None       # "improved" | "neutral" | "regressed"
    key_delta: Optional[str] = None
    failure_reason: Optional[str] = None
    notes: Optional[str] = None


@dataclass
class Capsule:
    id: str
    name: str
    intent: str
    status: str                         # "proposed" | "approved"
    base_commit: str
    knobs: dict = field(default_factory=dict)
    data_assumptions: Optional[str] = None
    resurrection_guide: Optional[str] = None
    result_summary: Optional[ResultSummary] = None
    payload_hash: Optional[str] = None
    code_slices: list[CodeSlice] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Capsule":
        d = dict(d)
        rs = d.get("result_summary")
        d["result_summary"] = ResultSummary(**rs) if rs else None
        d["code_slices"] = [CodeSlice(**c) for c in d.get("code_slices", [])]
        return cls(**d)


@dataclass
class Run:
    id: str
    cmd: str
    artifact_hash: str
    metrics: Optional[dict]
    base_commit: str
    env: Optional[dict]
    created_at: str
    returncode: Optional[int] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Run":
        return cls(**d)


@dataclass
class Event:
    id: str
    capsule_id: str
    kind: str               # "activate" | "deactivate"
    run_id: Optional[str]
    created_at: str


@dataclass
class Edge:
    src: str
    dst: str
    type: str  # depends_on|variant_of|derived_from|supersedes|produced|touches|overlaps


@dataclass
class Proposal:
    id: str
    trigger: str                        # "run" | "commit" | "manual"
    diff_ref: str                       # object hash of the captured diff
    candidates: list[dict]
    status: str = "open"                # "open" | "resolved" | "dismissed"
    run_id: Optional[str] = None
    from_features: Optional[list[str]] = None   # source capsule(s) this run regenerated
    source_commit: Optional[str] = None  # commit whose diff was captured (None = worktree)
