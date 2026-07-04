# Capture Source CLI and Guidance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add explicit `rgit capture` sources for worktree, commit, and range captures; dedupe open proposals by diff; update default guidance to tell agents how to choose a source.

**Architecture:** Extend the existing `DiffSource` boundary in `segmenter.py` with a `range` source and an outcome object. Keep `rgit capture` compatible by defaulting to worktree capture, while argparse mutually excludes `--worktree`, `--commit`, and `--range`.

**Tech Stack:** Python, argparse, pytest, Git CLI.

---

## Files

- Modify `src/rgit/gitutil.py`: add range diff helper.
- Modify `src/rgit/segmenter.py`: add `DiffSource.range()`, dedupe open proposals by diff hash, return capture outcome metadata.
- Modify `src/rgit/cli.py`: add capture source flags and source-specific output.
- Modify `src/rgit/agent_guidance.py`: update default guidance decision tree.
- Modify `tests/test_segmenter.py`: cover dedupe and range source.
- Modify `tests/test_cli.py`: cover capture CLI source flags and validation.
- Modify `tests/test_agent_guidance.py`: cover default guidance text.

## Task 1: CLI source tests

- [ ] Add failing CLI tests for `--commit`, `--range`, invalid range, and mutually exclusive source flags.
- [ ] Run focused CLI tests and verify they fail for missing flags.

## Task 2: Segmenter source and dedupe tests

- [ ] Add failing tests for `DiffSource.range()` and duplicate open proposal behavior.
- [ ] Run focused segmenter tests and verify they fail.

## Task 3: Implement source helpers and dedupe

- [ ] Add `diff_range(repo, base, head)` to `gitutil.py`.
- [ ] Add `DiffSource.range(base, head)` and range handling to `_read_diff()`.
- [ ] Make `segment_diff()` store the diff hash before adding a proposal and return existing open proposal metadata when the hash already exists.
- [ ] Preserve existing callers by making the return object compare equal to proposal ids and `None` for empty diffs.

## Task 4: Implement CLI flags

- [ ] Add mutually exclusive `--worktree`, `--commit REV`, and `--range A..B` flags.
- [ ] Parse `--range` by requiring exactly one `..`.
- [ ] Pass the selected `DiffSource` to `segment_diff()`.
- [ ] Print `proposal <id> already exists for this diff` for dedupe outcomes.

## Task 5: Guidance update

- [ ] Update the managed default guidance block with the worktree/commit/range decision tree.
- [ ] Add tests asserting guidance mentions `rgit capture`, `rgit capture --commit HEAD`, and `rgit capture --range <base>..HEAD`.

## Task 6: Verification and commit

- [ ] Run focused tests.
- [ ] Run `python -m pytest -q`.
- [ ] Commit implementation.
