from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
from dataclasses import asdict, dataclass
from typing import Optional

from .gitutil import CommitDiffSource, commit_subject
from .segmenter import Segmenter, segment_diff
from .store.store import Store

MAX_HISTORY_DIFF_BYTES = 1_000_000


@dataclass
class HistoryItem:
    commit: str
    subject: str
    status: str
    reason: str = ""
    proposal_id: Optional[str] = None
    diff_bytes: Optional[int] = 0
    diff_bytes_at_least: Optional[int] = None
    diff_bytes_truncated: bool = False
    candidate_count: Optional[int] = None
    diff_ref: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class HistoryPlan:
    range: str
    max_commits: Optional[int]
    max_diff_bytes: Optional[int]
    truncated: bool
    items: list[HistoryItem]

    @property
    def summary(self) -> dict[str, int]:
        counts = {
            "would_capture": 0,
            "captured": 0,
            "existing": 0,
            "skipped": 0,
        }
        for item in self.items:
            counts[item.status] = counts.get(item.status, 0) + 1
        return counts

    def to_dict(self, *, write: bool) -> dict:
        return {
            "range": self.range,
            "write": write,
            "max_commits": self.max_commits,
            "max_diff_bytes": self.max_diff_bytes,
            "truncated": self.truncated,
            "summary": self.summary,
            "items": [item.to_dict() for item in self.items],
        }


@dataclass(frozen=True)
class ExistingProposal:
    id: str
    candidate_count: int


def plan_capture_history(
    store: Store,
    range_spec: str,
    *,
    max_commits: Optional[int] = None,
    max_diff_bytes: Optional[int] = MAX_HISTORY_DIFF_BYTES,
) -> HistoryPlan:
    """Plan historical commit capture without mutating the research-git store."""
    if max_diff_bytes is not None and max_diff_bytes <= 0:
        raise ValueError("--max-diff-bytes must be positive")
    commits, truncated = _rev_list(
        store.root, range_spec, max_commits=max_commits)
    items: list[HistoryItem] = []
    seen: dict[str, HistoryItem] = {}
    existing_by_diff = _open_proposals_by_diff(store)
    for sha in commits:
        source = CommitDiffSource(sha)
        subject = commit_subject(store.root, sha)
        if max_diff_bytes is not None:
            diff_bytes, too_large = _commit_diff_size_capped(
                store.root, sha, max_diff_bytes)
            if too_large:
                items.append(HistoryItem(
                    commit=source.source_commit(store.root) or sha,
                    subject=subject,
                    status="skipped",
                    reason=(
                        f"diff exceeds {max_diff_bytes} byte limit"
                    ),
                    diff_bytes=None,
                    diff_bytes_at_least=diff_bytes,
                    diff_bytes_truncated=True,
                ))
                continue
        diff = source.diff(store.root)
        data = diff.encode("utf-8", errors="replace")
        diff_bytes = len(data)
        diff_ref = _digest(data) if diff.strip() else None
        item = HistoryItem(
            commit=source.source_commit(store.root) or sha,
            subject=subject,
            status="would_capture",
            diff_bytes=diff_bytes,
            diff_ref=diff_ref,
        )
        if not diff.strip():
            item.status = "skipped"
            item.reason = "commit introduced no diff"
        elif diff_ref in seen:
            previous = seen[diff_ref]
            item.reason = f"same diff as {previous.commit[:12]}"
            item.proposal_id = previous.proposal_id
            item.candidate_count = previous.candidate_count
            item.status = "existing" if previous.proposal_id else "duplicate"
        else:
            existing = existing_by_diff.get(diff_ref)
            if existing is not None:
                item.status = "existing"
                item.proposal_id = existing.id
                item.candidate_count = existing.candidate_count
                seen[diff_ref] = item
            elif max_diff_bytes is not None and diff_bytes > max_diff_bytes:
                item.status = "skipped"
                item.reason = (
                    f"diff {diff_bytes} bytes exceeds {max_diff_bytes} byte limit"
                )
            else:
                seen[diff_ref] = item
        items.append(item)
    return HistoryPlan(
        range=range_spec,
        max_commits=max_commits,
        max_diff_bytes=max_diff_bytes,
        truncated=truncated,
        items=items,
    )


def capture_history(
    store: Store,
    range_spec: str,
    segmenter: Segmenter,
    *,
    max_commits: Optional[int] = None,
    max_diff_bytes: Optional[int] = MAX_HISTORY_DIFF_BYTES,
    now: str = "",
) -> HistoryPlan:
    """Create open proposals for every capturable commit in a history plan."""
    plan = plan_capture_history(
        store,
        range_spec,
        max_commits=max_commits,
        max_diff_bytes=max_diff_bytes,
    )
    proposals_by_diff = {
        item.diff_ref: ExistingProposal(item.proposal_id, item.candidate_count or 0)
        for item in plan.items
        if item.diff_ref and item.proposal_id
    }
    for item in plan.items:
        if item.status == "duplicate" and item.diff_ref:
            existing = proposals_by_diff.get(item.diff_ref)
            if existing is not None:
                item.proposal_id = existing.id
                item.candidate_count = existing.candidate_count
                item.status = "existing"
            continue
        if item.status != "would_capture":
            continue
        pid = segment_diff(
            store,
            "history",
            segmenter,
            run_id=None,
            now=now,
            source=CommitDiffSource(item.commit),
        )
        if pid is None:
            item.status = "skipped"
            item.reason = "commit introduced no diff"
            continue
        item.proposal_id = str(pid)
        prop = store.get_proposal(str(pid))
        item.candidate_count = len(prop.candidates)
        item.status = "captured" if getattr(pid, "created", True) else "existing"
        if item.diff_ref:
            proposals_by_diff[item.diff_ref] = ExistingProposal(
                str(pid), item.candidate_count)
    return plan


def _rev_list(
    repo,
    range_spec: str,
    *,
    max_commits: Optional[int] = None,
) -> tuple[list[str], bool]:
    if "..." in range_spec:
        raise ValueError(
            f"invalid range {range_spec!r}: capture-history expects A..B")
    if ".." not in range_spec:
        raise ValueError(
            f"invalid range {range_spec!r}: expected A..B"
        )
    if max_commits is not None and max_commits <= 0:
        raise ValueError("--max-commits must be positive")
    cmd = ["git", "rev-list"]
    if max_commits is not None:
        cmd += ["--max-count", str(max_commits + 1)]
    cmd.append(range_spec)
    proc = subprocess.run(
        cmd,
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip()
        raise ValueError(
            f"cannot enumerate commits for {range_spec!r}"
            + (f": {detail}" if detail else "")
        )
    newest_first = [line.strip() for line in proc.stdout.splitlines()
                    if line.strip()]
    truncated = max_commits is not None and len(newest_first) > max_commits
    if max_commits is not None:
        newest_first = newest_first[:max_commits]
    return (list(reversed(newest_first)), truncated)


def _commit_diff_size_capped(repo, sha: str, max_bytes: int) -> tuple[int, bool]:
    proc = subprocess.Popen(
        ["git", "-c", "core.quotePath=false", "diff-tree", "--root",
         "--no-commit-id", "-p", sha, "--", ":(exclude).rgit"],
        cwd=repo,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert proc.stdout is not None
    data = proc.stdout.read(max_bytes + 1)
    too_large = len(data) > max_bytes
    if too_large:
        proc.kill()
        proc.communicate()
        return len(data), True
    rest, err = proc.communicate()
    if proc.returncode != 0:
        detail = err.decode("utf-8", errors="replace").strip()
        raise ValueError(
            f"cannot diff commit {sha[:12]}" + (f": {detail}" if detail else "")
        )
    data += rest
    return len(data), False


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _open_proposals_by_diff(store: Store) -> dict[str, ExistingProposal]:
    try:
        rows = store.conn.execute(
            "SELECT id, diff_ref, candidates FROM proposals WHERE status=?",
            ("open",),
        ).fetchall()
    except sqlite3.OperationalError as e:
        raise ValueError(
            "cannot read open proposals; run `rgit doctor` to inspect the store"
        ) from e
    index: dict[str, ExistingProposal] = {}
    for row in rows:
        try:
            candidate_count = len(json.loads(row["candidates"] or "[]"))
        except json.JSONDecodeError:
            candidate_count = 0
        index.setdefault(
            row["diff_ref"],
            ExistingProposal(row["id"], candidate_count),
        )
    return index
