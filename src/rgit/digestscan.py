"""Deterministic history scan: cluster mainline commits into digestion units.

Pure planning — no LLM, no store writes. `scan()` walks the first-parent
lineage, pairs reverts into dead units, clusters the rest (a merge commit is
one unit; same-author streaks over overlapping files merge), pre-drops pure
infra units, detects deleted-from-HEAD work, and scores everything.
digestqueue persists the result; this module never touches the store.
"""
from __future__ import annotations
import datetime
import hashlib
import math
import re
from pathlib import Path
from typing import Optional

from .gitutil import (current_commit, head_files, is_shallow, mainline_commits,
                      mainline_count)

MODES = ("layered", "trunk", "dead", "archaeology")

DEFAULT_WINDOW = 400                 # first-parent commits scanned by default
STREAK_MAX_COMMITS = 10
STREAK_MAX_GAP_SECONDS = 48 * 3600
STREAK_MAX_CHURN = 4000              # changed lines: scan-time proxy for the
                                     # 300 KB diff cap (bytes are unknown here)
UNIT_MAX_DIFF_BYTES = 300_000        # staging-time oversized flag (digestqueue)

_REVERT_TRAILER = re.compile(r"This reverts commit ([0-9a-f]{7,40})", re.IGNORECASE)

_LOCKFILE_NAMES = {
    "uv.lock", "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock",
    "Pipfile.lock", "Cargo.lock", "go.sum", "composer.lock", "Gemfile.lock",
}
_INFRA_NAMES = {".gitignore", ".gitattributes", ".editorconfig", "LICENSE",
                "CODEOWNERS", ".pre-commit-config.yaml"}
_INFRA_PREFIXES = (".github/", ".gitlab/", "docs/")
_DOC_SUFFIXES = (".md", ".rst")


def _is_infra_path(path: str) -> bool:
    """Conservative noise classifier: only paths that are near-certainly not a
    feature. Ambiguity (pyproject, configs, code) stays with the segmenter."""
    name = path.rsplit("/", 1)[-1]
    if name in _LOCKFILE_NAMES or name in _INFRA_NAMES:
        return True
    if path.startswith(_INFRA_PREFIXES):
        return True
    return path.endswith(_DOC_SUFFIXES)


def unit_id(shas: list[str]) -> str:
    """Deterministic unit identity: hash of the commit-sha set. Rescans emit
    the same id for the same commits, so INSERT OR IGNORE makes scan idempotent."""
    h = hashlib.sha256("\n".join(sorted(shas)).encode("utf-8")).hexdigest()
    return f"dig_{h[:16]}"


def _iso_date(at: int) -> str:
    return datetime.datetime.fromtimestamp(
        at, tz=datetime.timezone.utc).date().isoformat()


def _pair_reverts(commits: list[dict]) -> tuple[dict, set]:
    """({original_sha: revert_meta}, consumed_shas).

    A revert whose original lies outside the scanned range stays a normal
    commit — its diff (the removal) is still real history.
    """
    shas = [c["sha"] for c in commits]
    dead: dict[str, dict] = {}
    consumed: set[str] = set()
    for c in commits:
        m = _REVERT_TRAILER.search(c["body"])
        if not m:
            continue
        prefix = m.group(1)
        target = next((s for s in shas if s.startswith(prefix)), None)
        if target is None or target in dead:
            continue
        dead[target] = {"reverted_by": c["sha"], "revert_at": c["at"],
                        "revert_date": _iso_date(c["at"]),
                        "revert_subject": c["subject"]}
        consumed.add(target)
        consumed.add(c["sha"])
    return dead, consumed


def _make_unit(commits: list[dict], *, merge: bool) -> dict:
    shas = [c["sha"] for c in commits]
    files = sorted({f for c in commits for f in c["files"]})
    churn = sum(c["churn"] for c in commits)
    meta = {
        "subjects": [c["subject"] for c in commits],
        "author": commits[-1]["author"],
        "start_at": commits[0]["at"], "end_at": commits[-1]["at"],
        "start_date": _iso_date(commits[0]["at"]),
        "end_date": _iso_date(commits[-1]["at"]),
        "files": files, "files_count": len(files),
        "code_files": sum(1 for f in files if not _is_infra_path(f)),
        "churn": churn, "merge": merge,
        "has_root": not commits[0]["parents"],
        "oversized": churn > STREAK_MAX_CHURN,
    }
    return {"id": unit_id(shas), "kind": "landed", "shas": shas, "score": 0.0,
            "status": "pending", "skip_reason": None, "meta": meta}


def _extends_streak(streak: list[dict], c: dict) -> bool:
    last = streak[-1]
    if c["author"] != last["author"]:
        return False
    # abs(): rebases and scripted fixtures make dates non-monotonic on the
    # first-parent chain; adjacency is what the 48h rule is really about.
    if abs(c["at"] - last["at"]) > STREAK_MAX_GAP_SECONDS:
        return False
    if len(streak) >= STREAK_MAX_COMMITS:
        return False
    if sum(x["churn"] for x in streak) + c["churn"] > STREAK_MAX_CHURN:
        return False
    streak_files = {f for x in streak for f in x["files"]}
    return bool(streak_files & set(c["files"]))


def _score(unit: dict, newest_at: int, oldest_at: int) -> float:
    m = unit["meta"]
    s = math.log2(1 + m["churn"])
    s += 2.0 * (m["code_files"] / max(1, m["files_count"]))
    if unit["kind"] == "dead":
        s += 3.0                     # the only knowledge otherwise lost
    if m["merge"]:
        s += 1.0                     # structurally "one feature" already
    span = max(1, newest_at - oldest_at)
    s += (m["end_at"] - oldest_at) / span          # recency, 0..1
    return round(s, 4)


def scan(repo: Path, *, range_spec: Optional[str] = None,
         window: int = DEFAULT_WINDOW, all_history: bool = False) -> dict:
    """Cluster the selected mainline slice into digestion-unit drafts."""
    total = mainline_count(repo)
    limit = None if (range_spec or all_history) else window
    commits = mainline_commits(repo, range_spec=range_spec, limit=limit)
    head_set = head_files(repo)
    dead_meta, consumed = _pair_reverts(commits)

    units: list[dict] = []
    streak: list[dict] = []

    def flush() -> None:
        if streak:
            units.append(_make_unit(list(streak), merge=False))
            streak.clear()

    for c in commits:
        sha = c["sha"]
        if sha in dead_meta:                       # reverted original
            flush()
            u = _make_unit([c], merge=False)
            u["kind"] = "dead"
            u["meta"]["dead"] = "reverted"
            u["meta"].update(dead_meta[sha])
            units.append(u)
            continue
        if sha in consumed:                        # the revert commit itself
            flush()                                # never let a streak span it
            continue
        if len(c["parents"]) > 1:                  # merge = one PR unit
            flush()
            units.append(_make_unit([c], merge=True))
            continue
        if streak and not _extends_streak(streak, c):
            flush()
        streak.append(c)
    flush()

    newest_at = max((c["at"] for c in commits), default=0)
    oldest_at = min((c["at"] for c in commits), default=0)
    for u in units:
        m = u["meta"]
        if u["kind"] == "landed" and m["files"] and \
                not any(f in head_set for f in m["files"]):
            u["kind"] = "dead"
            m["dead"] = "deleted"
        if m["files"] and all(_is_infra_path(f) for f in m["files"]):
            u["status"] = "skipped"
            u["skip_reason"] = "infra"
        u["score"] = _score(u, newest_at, oldest_at)
        m["files"] = m["files"][:50]               # cap AFTER detection above
    return {"units": units, "total_mainline": total,
            "window_applied": limit is not None and total > limit,
            "shallow": is_shallow(repo), "head": current_commit(repo)}
