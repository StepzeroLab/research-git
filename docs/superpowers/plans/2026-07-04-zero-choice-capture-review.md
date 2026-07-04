# Zero-choice capture & review defaults — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bare `rgit capture`, `rgit review --approve`, and `rgit install` do the right thing with zero parameters; every legacy flag keeps parsing but leaves `--help`.

**Architecture:** All behavior changes live in the CLI layer (`cli.py`) plus one detection helper in `installer.py` and one subject helper in `gitutil.py`. The capture pipeline (`segment_diff`, `DiffSource`) is untouched — the CLI just picks sources smarter. Spec: `docs/superpowers/specs/2026-07-04-zero-choice-capture-review-design.md`.

**Tech Stack:** Python 3.11+, argparse, pytest (fixtures in `tests/conftest.py`, run via `.venv/bin/python -m pytest`).

## Global Constraints

- Every currently documented invocation keeps parsing and behaving identically (hidden flags via `help=argparse.SUPPRESS`, never removed).
- `pending --json` schema unchanged; explicit-platform `rgit install <p> --json` output byte-compatible with today's JSON.
- Hook template unchanged (`rgit capture --trigger commit --commit HEAD`).
- No new interactive prompts outside `install` on a TTY.
- Runtime notices go to stderr; machine stdout stays parseable.

---

### Task 1: capture auto-source + positional SOURCE

**Files:**
- Modify: `src/rgit/gitutil.py` (add `commit_subject` after `diff_of_commit`)
- Modify: `src/rgit/cli.py` (capture parser + capture branch in `main`)
- Test: `tests/test_cli.py`, `tests/test_gitutil.py`

**Interfaces:**
- Produces: `commit_subject(repo: Path, sha: str) -> str`; capture positional `args.source`; precedence explicit positional > legacy flags > trigger rule > auto.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_gitutil.py
def test_commit_subject_returns_first_line(git_repo):
    from rgit.gitutil import commit_subject
    assert commit_subject(git_repo, current_commit(git_repo)) == "init"

# tests/test_cli.py
def test_capture_auto_dirty_tree_captures_worktree(git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo); monkeypatch.setattr(cli, "_SEGMENTER", None)
    Store.init(git_repo)
    (git_repo / "model.py").write_text("def forward(x):\n    return x + 1\n")
    assert cli.main(["capture"]) == 0
    props = Store.open(git_repo).list_proposals("open")
    assert len(props) == 1 and props[0].source_commit is None

def test_capture_auto_clean_tree_captures_head_with_note(git_repo, monkeypatch, capsys):
    from rgit.gitutil import current_commit
    monkeypatch.chdir(git_repo); monkeypatch.setattr(cli, "_SEGMENTER", None)
    Store.init(git_repo)
    (git_repo / "model.py").write_text("def forward(x):\n    return x * 2\n")
    _commit_all(git_repo, "double it")
    assert cli.main(["capture"]) == 0
    out = capsys.readouterr().out
    assert "capturing last commit" in out and "double it" in out
    props = Store.open(git_repo).list_proposals("open")
    assert props[0].source_commit == current_commit(git_repo)

def test_capture_positional_commit_and_range(git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo); monkeypatch.setattr(cli, "_SEGMENTER", None)
    Store.init(git_repo)
    from rgit.gitutil import current_commit
    base = current_commit(git_repo)
    (git_repo / "model.py").write_text("def forward(x):\n    return x * 2\n")
    _commit_all(git_repo, "double")
    assert cli.main(["capture", "HEAD"]) == 0
    assert cli.main(["capture", f"{base}..HEAD"]) == 0   # dedup may report existing; rc 0
    assert len(Store.open(git_repo).list_proposals("open")) >= 1

def test_capture_positional_conflicts_with_legacy_flags(git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo); Store.init(git_repo)
    assert cli.main(["capture", "HEAD", "--worktree"]) == 1
    assert "not both" in capsys.readouterr().out

def test_capture_help_hides_legacy_source_flags(capsys):
    with pytest.raises(SystemExit):
        cli.main(["capture", "--help"])
    out = capsys.readouterr().out
    assert "--commit" not in out and "--range" not in out and "--worktree" not in out
```

- [ ] **Step 2: Run to verify failures** — `pytest tests/test_cli.py -k "auto or positional or hides" -q` → FAIL (unknown positional / note missing).

- [ ] **Step 3: Implement**

`gitutil.py`:
```python
def commit_subject(repo: Path, sha: str) -> str:
    """First line of a commit message, for human-facing capture notes."""
    return _git(repo, "log", "-1", "--format=%s", sha, "--").strip()
```

`cli.py` parser: add positional before flags; hide legacy source flags:
```python
    p_cap.add_argument("source", nargs="?", metavar="REV|A..B",
                       help="what to capture: a commit (HEAD, abc123) or a range "
                            "(main..HEAD). Omit to auto-pick: the working tree "
                            "if it has changes, else the last commit.")
    # legacy spellings — permanent, hidden
    cap_src.add_argument("--commit", ..., help=argparse.SUPPRESS)
    cap_src.add_argument("--range", ..., help=argparse.SUPPRESS)
    cap_src.add_argument("--worktree", action="store_true", help=argparse.SUPPRESS)
```

`main` capture branch — replace source selection with precedence chain:
```python
        explicit_flag = (args.range_spec is not None or args.commit is not None
                         or args.worktree)
        if args.source is not None and explicit_flag:
            print("give either a positional source or --commit/--range/--worktree, not both")
            return 1
        try:
            if args.source is not None:
                source = (RangeDiffSource(args.source) if ".." in args.source
                          else CommitDiffSource(args.source))
            elif args.range_spec is not None:
                source = RangeDiffSource(args.range_spec)
            elif args.commit is not None:
                source = CommitDiffSource(args.commit)
            elif args.worktree:
                source = WorktreeDiffSource()
            elif args.trigger == "commit":
                source = CommitDiffSource("HEAD")   # hook knows its context
            elif diff_since(store.root, "HEAD").strip():
                source = WorktreeDiffSource()
            else:
                sha = resolve_commit(store.root, "HEAD")
                print(f'capturing last commit {sha[:12]} '
                      f'("{commit_subject(store.root, sha)}")')
                source = CommitDiffSource(sha)
            pid = segment_diff(store, args.trigger, _segmenter(), run_id=None,
                               now=_now(), source=source)
        except ValueError as e:
            print(str(e)); return 1
```

- [ ] **Step 4: Run** — targeted tests PASS, then `pytest tests/test_cli.py tests/test_gitutil.py -q` all green.
- [ ] **Step 5: Commit** — `feat(capture): auto-pick source; positional REV|A..B; hide legacy flags`

### Task 2: review actions without an id

**Files:** Modify `src/rgit/cli.py` (review parser + branch, `_sole_open_proposal` helper). Test: `tests/test_cli.py`.

**Interfaces:** Produces `_sole_open_proposal(store) -> str` (raises ValueError with listing).

- [ ] **Step 1: Failing tests** — bare `--approve` with one open proposal approves it; with none → exit 1 "no pending proposals"; with two → exit 1 message listing both ids; bare `--dismiss` with one dismisses.
- [ ] **Step 2: Verify failures** (argparse "expected one argument").
- [ ] **Step 3: Implement** — `--approve` / `--dismiss` become `nargs="?", const="", default=None`; branch tests `is not None`; empty string resolves via:

```python
def _sole_open_proposal(store: Store) -> str:
    opens = store.list_proposals("open")
    if not opens:
        raise ValueError("no pending proposals")
    if len(opens) > 1:
        lines = []
        for p in opens:
            names = ", ".join(c["name"] for c in p.candidates) or "0 candidate(s)"
            lines.append(f"  {p.id}  [{p.trigger}]  {names}")
        raise ValueError("several proposals are open; pass an id:\n" + "\n".join(lines))
    return opens[0].id
```

- [ ] **Step 4: Full test_cli green** (explicit-id tests are the regression net).
- [ ] **Step 5: Commit** — `feat(review): approve/dismiss default to the only open proposal`

### Task 3: install platform detection + fan-out

**Files:** Modify `src/rgit/installer.py` (`detect_platforms`), `src/rgit/cli.py` (install branch, `_prompt_platform_numbered`). Test: `tests/test_installer.py`, `tests/test_cli.py`.

**Interfaces:** Produces `installer.detect_platforms() -> list[str]` (subset of PLATFORMS, never "generic").

- [ ] **Step 1: Failing tests** — detection with patched `shutil.which`/`Path.home` (tmp home with `.codex`/`.gemini` dirs); bare non-TTY install with zero detected → exit 1 + platform list; bare install with two detected runs installer for both (monkeypatch `installer.install` recording calls).
- [ ] **Step 2: Verify failures.**
- [ ] **Step 3: Implement**

```python
# installer.py
import shutil

def detect_platforms() -> list[str]:
    """Agent clients present on this machine. `generic` is never detected —
    it is an alias for any ~/.agents/skills client, not an install signal."""
    home = Path.home()
    found = []
    if shutil.which("claude"):
        found.append("claude-code")
    if (home / ".codex").is_dir():
        found.append("codex")
    if (home / ".gemini").is_dir():
        found.append("gemini")
    if shutil.which("opencode") or (home / ".config" / "opencode").is_dir():
        found.append("opencode")
    return found
```

CLI: bare platform → `detect_platforms()`; empty + stdin TTY → numbered picker (pattern of `_prompt_guidance_mode_numbered`, listing `installer.PLATFORMS`); empty + non-TTY → list + exit 1; loop platforms → collect results (explicit platform keeps today's single-object payload; bare yields a list).

- [ ] **Step 4: Green.**  - [ ] **Step 5: Commit** — `feat(install): auto-detect installed agent clients`

### Task 4: install human output + flag hiding

**Files:** Modify `src/rgit/cli.py` (`_render_install_result`, hidden `--json/--dry-run/--guidance/--scope`). Test: `tests/test_cli.py`.

- [ ] **Step 1: Failing tests** — human output contains `✓`, "restart", and the `rgit install-hooks` nudge (agents-family platform with tmp home); `--json` (explicit platform) stdout parses and matches today's keys (`platform`, `links`/`results`, `guidance`); `install --help` hides the four flags.
- [ ] **Step 2: Verify failures.**
- [ ] **Step 3: Implement** — renderer walks the result dict: claude-code `results[].cmd/rc`, agents-family `links[]`, `skills_dir`, `instructions`, `guidance.action/path`, `errors[]` (✗ + hint); after all platforms print `restart your CLI/agent session to pick up the skills` and `note: rgit install-hooks enables per-commit capture (opt-in)`. `--json` prints exactly the old `json.dumps(res, indent=2, ensure_ascii=False)`.
- [ ] **Step 4: Green** — migrate the existing JSON-asserting CLI tests to `--json`; keep `test_install_list_and_dry_run` asserting the human dry-run mentions "marketplace".
- [ ] **Step 5: Commit** — `feat(install): human-readable output; hide plumbing flags behind --json`

### Task 5: non-TTY guidance stops failing

**Files:** Modify `src/rgit/cli.py` (`_prompt_guidance_mode` gating). Test: `tests/test_cli.py` (update the PR #19 non-TTY tests).

- [ ] **Step 1: Failing test** — patched non-TTY stdin: `rgit install codex --json` (tmp home) exits 0, no prompt text, stderr contains `guidance mode: default — change with --guidance`.
- [ ] **Step 2: Verify failure** (today exits 1 with homework).
- [ ] **Step 3: Implement** — numbered fallback only when `sys.stdin.isatty()`; otherwise return `None` (preserves a pinned mode, else writes `default`) and print the stderr notice. TTY picker unchanged. Update the #19 tests that assert exit 1 to assert the new contract.
- [ ] **Step 4: Green.**  - [ ] **Step 5: Commit** — `feat(install): non-interactive installs default guidance with a notice`

### Task 6: teaching text collapses to bare commands

**Files:** Modify `src/rgit/agent_guidance.py`, `src/rgit/_plugin/skills/rgit-capture/SKILL.md`, `README.md`. Test: `tests/test_agent_guidance.py` (update capture-path assertions), `tests/test_guidance_coupling.py` (auto).

- [ ] **Step 1: Update tests first** — block must contain bare `rgit capture` teaching and `rgit capture A..B`; must NOT contain `--trigger manual` or `--commit HEAD` in the Use section examples; bootstrap line becomes `rgit capture --init`.
- [ ] **Step 2: Verify failures.**
- [ ] **Step 3: Rewrite texts** — guidance Use section: one capture line ("run `rgit capture` — it captures uncommitted work, or the last commit when the tree is clean; committing first is fine"), one span line (`rgit capture A..B`), keep hook-dedup + skip-mechanical lines. SKILL.md step 1: two-line code block (bare + span) + auto-pick sentence. README: quick-start bare capture, hooks paragraph and More-commands row updated to `rgit capture [REV|A..B]`, install section shows bare `rgit install`.
- [ ] **Step 4: Green** (`test_guidance_coupling` re-validates every taught command).
- [ ] **Step 5: Commit** — `docs: teach the zero-choice command forms`

### Task 7: full verification + PR

- [ ] Full suite `.venv/bin/python -m pytest -q` green.
- [ ] Live smoke in a scratch repo: bare capture dirty/clean/repeat, bare review approve, bare install with fake `~/.codex` home (`HOME=... rgit install`), non-TTY guidance notice.
- [ ] Push branch `zero-choice-capture-review`, open PR (spec + plan + implementation), body lists the two deliberate semantic changes (bare install writes; #19 reversal).

### Task 8: git-style misuse hints (added mid-implementation at maintainer request)

**Files:** Modify `src/rgit/cli.py` (`_Parser.error` did-you-mean, install unknown-platform catch, capture ref hint, dismiss unknown-id hint). Test: `tests/test_cli.py`.

- [x] Failing tests: unknown subcommand `captur` → exit 2 + `did you mean` + `capture`; no hint when nothing close; `install codx` → exit 1 + `did you mean 'codex'` (no traceback); `capture no-such-ref` → hint naming `git log --oneline`; `review --dismiss prop_nope` → hint naming `rgit review`.
- [x] Implement: `_Parser(argparse.ArgumentParser)` overriding `error()` with difflib close matches over `tuple(sub.choices)`; ValueError catch around the installer fan-out; hint lines at the capture and dismiss error sites.
- [x] Full suite green; commit `feat(cli): git-style did-you-mean and misuse hints`.
