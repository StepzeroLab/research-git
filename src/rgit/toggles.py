from __future__ import annotations
import re

from .astmap import symbol_at_line
from .store.store import Store

_FILE = re.compile(r"^\+\+\+ b/(.+)$")
_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
_COMMENT = re.compile(r"^(\s*)#\s?(.*)$")


def _decomment(s: str) -> tuple[bool, str]:
    """(is_comment, code-body-stripped). Python '#' only, one optional space."""
    m = _COMMENT.match(s)
    if m:
        return True, (m.group(1) + m.group(2)).strip()
    return False, s.strip()


def detect_toggles(diff: str) -> list[dict]:
    """Find comment-in/out toggles in a unified diff (Python '#' only).

    code -> comment = deactivate ; comment -> code = activate. The reported
    `line` is the new-side line number of the added line.
    Returns [{file, line, kind, text}].

    Limitations (v2): matching is hunk-level by stripped body (set membership), not line-adjacency, so an unrelated removed code line and an added comment line within the same hunk can produce a false-positive toggle. This is an accepted v2 simplification (toggle events are advisory); line-adjacency precision is deferred.
    """
    out: list[dict] = []
    file = None
    new_ln = 0
    removed_code: set[str] = set()       # stripped code bodies removed in this hunk
    removed_comment_bodies: set[str] = set()
    for raw in diff.splitlines():
        mf = _FILE.match(raw)
        if mf:
            file = mf.group(1)
            removed_code, removed_comment_bodies = set(), set()
            continue
        mh = _HUNK.match(raw)
        if mh:
            new_ln = int(mh.group(1))
            removed_code, removed_comment_bodies = set(), set()
            continue
        if file is None or raw.startswith(("diff --git", "--- ", "index ")):
            continue
        if raw.startswith("-") and not raw.startswith("---"):
            is_c, body = _decomment(raw[1:])
            (removed_comment_bodies if is_c else removed_code).add(body)
            continue
        if raw.startswith("+") and not raw.startswith("+++"):
            is_c, body = _decomment(raw[1:])
            if is_c and body in removed_code:
                out.append({"file": file, "line": new_ln, "kind": "deactivate",
                            "text": raw[1:]})
            elif (not is_c) and body in removed_comment_bodies:
                out.append({"file": file, "line": new_ln, "kind": "activate",
                            "text": raw[1:]})
            new_ln += 1
            continue
        # context line advances the new-side counter
        new_ln += 1
    return out


def map_to_capsules(store: Store, toggles: list[dict]) -> list[dict]:
    """Map each toggle to an approved capsule whose code_slices cover it.

    Prefer an exact (file, symbol) match; fall back to file-only when the
    enclosing symbol can't be resolved. Returns [{capsule_id, kind}].

    Limitations (v2): when the enclosing symbol can't be resolved, the file-only fallback matches the first approved capsule touching that file in `list_features()` order, so among multiple file-only matches only one (insertion-order-first) is reported.
    """
    caps = [c for c in store.list_features() if c.status == "approved"]
    out = []
    for t in toggles:
        sym = symbol_at_line(store.root, t["file"], t["line"])
        for c in caps:
            hit = any(sl.file == t["file"] and (sym is None or sl.symbol == sym)
                      for sl in c.code_slices)
            if hit:
                out.append({"capsule_id": c.id, "kind": t["kind"]})
                break
    return out
