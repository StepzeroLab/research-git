"""Managed global guidance block for agent clients."""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path


START = "<!-- research-git:start -->"
END = "<!-- research-git:end -->"

# Modes a user may pin in the managed block; carried across reinstalls so an
# upgrade does not silently reset a deliberate choice back to `default`.
KNOWN_MODES = ("default", "manual-only", "custom")
_MODE_RE = re.compile(r"^Current mode:[ \t]*(.+)$", re.MULTILINE)

# Fingerprinted START marker: h= is the canonical hash of the block body, so
# the update path can tell an official block (safe to replace) from one the
# user edited (never touch). The bare `START` form is what pre-0.0.5 releases
# wrote; _START_RE accepts both.
_START_RE = re.compile(r"<!-- research-git:start(?: h=([0-9a-f]{12}))? -->")

# canonical_hash of every official block body ever shipped without a
# fingerprint (see docs/superpowers/specs/2026-07-04-runtime-update-check-design.md).
HISTORICAL_HASHES = frozenset({
    "9e20fa27047f",   # v0.0.1 - v0.0.3
    "c7d73fc2ba60",   # v0.0.4
})


def canonical_hash(block: str) -> str:
    """12-hex digest of a block's body: markers and mode line excluded, so the
    hash survives mode pinning and marker-format changes."""
    lines = block.strip().splitlines()
    body = [l.rstrip() for l in lines[1:-1]
            if not l.startswith("Current mode:")]
    text = "\n".join(body).strip() + "\n"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def render_global_block(mode: str = "default") -> str:
    if mode not in KNOWN_MODES:
        mode = "default"
    body = (
        "## research-git\n"
        "\n"
        "research-git is installed as a default agent capability.\n"
        "\n"
        f"Current mode: {mode}\n"
        "\n"
        "Mode options:\n"
        "- `default`: After code changes, consider capture. Capture meaningful "
        "research/code ideas; skip mechanical changes.\n"
        "- `manual-only`: Use research-git only when the user explicitly asks to "
        "capture, save, recall, resurrect, or bring back an idea.\n"
        "- `custom`: Inherit `default`, then apply repo-specific rules from this "
        "repo.\n"
        "\n"
        "Priority:\n"
        "- Session/user instruction overrides repo and global guidance.\n"
        "- Repo-level research-git preferences override global guidance.\n"
        "\n"
        "Repo preference recording:\n"
        "- If the user clearly asks to remember a stable repo-level research-git "
        "preference, update this repo's guidance file.\n"
        "- For unclear preferences, ask first.\n"
        "- Do not write repo preferences for one-off session instructions.\n"
        "\n"
        "Use:\n"
        "- After meaningful code/research changes, run `rgit capture` — it "
        "captures uncommitted work, or the last commit when the tree is "
        "clean, so committing first is fine. Then use the `rgit-capture` "
        "skill to segment.\n"
        "- For a specific span of commits: `rgit capture main..HEAD` (any "
        "A..B range works).\n"
        "- If a post-commit hook is installed (`rgit install-hooks`), commits "
        "are captured automatically; do not capture the same commit again.\n"
        "- Skip mechanical formatting, dependency churn, generated files, or "
        "changes with no reusable research/code idea.\n"
        "- For recall/resurrection requests, use the `rgit-recall` skill.\n"
        "- If `.rgit/` is missing in a git repo: when operating autonomously "
        "(no human to ask), bootstrap the store with `rgit capture --init` "
        "(store only — never install hooks unless asked); in an interactive "
        "session, tell the user to run `rgit init` rather than initializing "
        "silently.\n"
        "- In final feedback, mention any capsules created, approved, applied, "
        "or skipped, plus important graph relations.\n"
    )
    provisional = f"{START}\n{body}{END}\n"
    h = canonical_hash(provisional)
    return f"<!-- research-git:start h={h} -->\n{body}{END}\n"


def manual_status(mode: str = "default") -> dict:
    return {
        "action": "manual",
        "block": render_global_block(mode),
        "instructions": "Add this managed block to your agent's global guidance file.",
    }


def manual_uninstall_status() -> dict:
    return {
        "action": "manual",
        "instructions": (
            "remove the research-git managed block from your agent's global "
            "guidance file if you added it manually."
        ),
    }


def upsert_managed_block(path: Path, *, mode: str | None = None,
                         dry_run: bool = False) -> dict:
    # mode=None means "no explicit choice this run": render the default block but
    # preserve any mode the user previously pinned. An explicit mode overrides.
    explicit = mode is not None
    block = render_global_block(mode or "default")
    exists = path.exists()
    text = path.read_text(encoding="utf-8") if exists else ""
    new_text, action = _upsert_text(text, block, exists, carry=not explicit)
    if dry_run:
        return {"action": _dry_action(action), "path": str(path), "block": block}
    if new_text != text:
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(path, new_text)
    return {"action": action if new_text != text else "unchanged",
            "path": str(path)}


def remove_managed_block(path: Path, *, dry_run: bool = False) -> dict:
    if not path.exists():
        return {"action": "absent", "path": str(path)}
    text = path.read_text(encoding="utf-8")
    span = _managed_span(text)
    if span is None:
        return {"action": "absent", "path": str(path)}
    start, end = span
    new_text = text[:start] + text[end:]
    if dry_run:
        return {"action": "would_remove", "path": str(path)}
    _atomic_write(path, new_text)
    return {"action": "removed", "path": str(path)}


def _upsert_text(text: str, block: str, exists: bool,
                 carry: bool = True) -> tuple[str, str]:
    span = _managed_span(text)
    if span is not None:
        start, end = span
        if carry:
            block = _carry_mode(text[start:end], block)
        new_text = text[:start] + block + text[end:]
        return new_text, "updated"
    if not exists or not text:
        return block, "created"
    sep = "" if text.endswith("\n") else "\n"
    return text + sep + "\n" + block, "appended"


def _carry_mode(old_block: str, new_block: str) -> str:
    """Preserve a user-pinned `Current mode:` from the existing block.

    Re-rendering always emits `Current mode: default`. If the user had pinned a
    different recognized mode, keep it so an upgrade does not reset their choice.
    """
    m = _MODE_RE.search(old_block)
    if not m:
        return new_block
    mode = m.group(1).strip()
    if mode == "default" or mode not in KNOWN_MODES:
        return new_block
    return _MODE_RE.sub(f"Current mode: {mode}", new_block, count=1)


def _managed_span(text: str) -> tuple[int, int] | None:
    m = _START_RE.search(text)
    if m is None:
        return None
    end = text.find(END, m.start())
    if end < 0:
        return None
    after_end = end + len(END)
    if text[after_end:after_end + 1] == "\n":
        after_end += 1
    return m.start(), after_end


def classify_block(text: str) -> str:
    """absent | broken | pristine | customized (update-path policy input)."""
    span = _managed_span(text)
    if span is None:
        if _START_RE.search(text) or END in text:
            return "broken"
        return "absent"
    block = text[span[0]:span[1]]
    h = canonical_hash(block)
    stamped = _START_RE.match(block).group(1)
    if h == stamped or h in HISTORICAL_HASHES \
            or h == canonical_hash(render_global_block()):
        return "pristine"
    return "customized"


def refresh_managed_block(path: Path) -> dict:
    """Update-path guidance refresh: replace only pristine official blocks.

    Unlike upsert_managed_block (explicit-install semantics), this never
    appends a missing block and never overwrites user edits - it skips and
    explains instead.
    """
    if not path.exists():
        return {"action": "absent_file", "path": str(path)}
    text = path.read_text(encoding="utf-8")
    kind = classify_block(text)
    if kind == "absent":
        return {"action": "skipped_removed", "path": str(path),
                "hint": (f"no research-git block in {path} (removed on "
                         f"purpose?) — run `rgit install` to restore it")}
    if kind == "broken":
        return {"action": "skipped_broken", "path": str(path),
                "hint": (f"research-git markers in {path} look damaged (one "
                         f"of the start/end pair is missing) — fix or remove "
                         f"them manually, then run `rgit install`")}
    if kind == "customized":
        return {"action": "skipped_customized", "path": str(path),
                "hint": (f"customized research-git block in {path} left "
                         f"untouched — run `rgit install` to overwrite it "
                         f"with the new official version")}
    start, end = _managed_span(text)
    block = _carry_mode(text[start:end], render_global_block("default"))
    new_text = text[:start] + block + text[end:]
    if new_text == text:
        return {"action": "unchanged", "path": str(path)}
    _atomic_write(path, new_text)
    return {"action": "updated", "path": str(path)}


def _dry_action(action: str) -> str:
    return {
        "created": "would_create",
        "appended": "would_append",
        "updated": "would_update",
    }[action]


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.research-git.tmp")
    # newline="" disables newline translation so the user's exact text (LF) is
    # preserved byte-for-byte — on Windows write_text would otherwise rewrite
    # every "\n" to "\r\n" and corrupt the surrounding user content.
    tmp.write_text(text, encoding="utf-8", newline="")
    if path.exists():
        os.chmod(tmp, path.stat().st_mode)
    tmp.replace(path)
