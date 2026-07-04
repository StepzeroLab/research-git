# Commit Hook Capture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `rgit install-hooks` post-commit capture stage proposals from the commit that just completed.

**Architecture:** Add an internal diff-source boundary to capture segmentation. Keep working-tree capture as the default, but make `trigger="commit"` resolve to a committed-diff source for `HEAD`.

**Tech Stack:** Python, pytest, Git CLI.

---

## Files

- Modify `src/rgit/gitutil.py`: add committed-diff helper.
- Modify `src/rgit/segmenter.py`: add `DiffSource` and select a diff source in `segment_diff()`.
- Modify `tests/test_segmenter.py`: cover commit-trigger capture and manual capture preservation.
- Modify `tests/test_cli.py`: cover the installed hook end-to-end.

## Task 1: Failing segmenter tests

- [ ] Add a test proving `trigger="commit"` captures the latest committed diff while the working tree is clean.
- [ ] Add a test proving `trigger="manual"` still returns no proposal when only committed changes exist.
- [ ] Run the focused tests and verify the commit-trigger test fails before production code changes.

## Task 2: Diff source implementation

- [ ] Add `diff_commit(repo, rev="HEAD")` to `src/rgit/gitutil.py`.
- [ ] Add `DiffSource` to `src/rgit/segmenter.py`.
- [ ] Change `segment_diff()` to accept `diff_source=None`.
- [ ] Resolve `diff_source=None` to `DiffSource.commit("HEAD")` only when `trigger == "commit"`; otherwise use `DiffSource.working_tree()`.
- [ ] Run focused segmenter tests and verify they pass.

## Task 3: Hook integration test

- [ ] Add an end-to-end CLI test that runs `rgit init`, `rgit install-hooks`, commits a change, then verifies `rgit pending --json` contains the committed diff.
- [ ] Run the focused hook/CLI test and verify it passes.

## Task 4: Verification and commit

- [ ] Run `python -m pytest tests/test_segmenter.py tests/test_hooks.py tests/test_cli.py -q`.
- [ ] Run `python -m pytest -q`.
- [ ] Commit the implementation.
