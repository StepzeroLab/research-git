# Commit hook capture should read committed diffs

## Problem

Issue #20 tracks a mismatch in the `rgit install-hooks` flow.

`rgit install-hooks` installs a Git `post-commit` hook that runs:

```sh
rgit capture --trigger commit || true
```

The hook runs after `git commit` has already advanced `HEAD`. At that point a
normal clean commit leaves no working-tree diff. However, the current
`rgit capture` path calls `segment_diff()`, which always reads the working tree
against `HEAD`.

That means `--trigger commit` currently records where the capture came from, but
does not change what diff is captured. In the normal hook path, the just-created
commit is often missed.

## Goals

- Make `rgit install-hooks` useful: after a normal commit, the hook should stage
  a proposal for the commit that just completed.
- Preserve existing manual capture behavior: `rgit capture` and
  `rgit capture --trigger manual` should continue to read working-tree changes.
- Keep the hook safe: capture failures must not fail the user's commit.
- Keep the implementation extensible for later committed-range capture, without
  exposing that larger CLI surface in this change.

## Non-goals

- Do not change default agent guidance in this issue.
- Do not add README documentation in this issue.
- Do not add public `rgit capture --commit` or `--range` flags yet.
- Do not solve small-commit aggregation yet.
- Do not special-case merge commit semantics in this first pass.

## Design

Introduce an internal diff-source concept for capture segmentation.

The current shape is effectively:

```python
segment_diff(store, trigger, segmenter, run_id=None, now="")
    diff = diff_since(store.root, "HEAD")
```

Change it to keep the existing call shape, but allow the diff source to be
selected internally:

```python
segment_diff(
    store,
    trigger,
    segmenter,
    run_id=None,
    now="",
    diff_source=None,
)
```

`diff_source=None` means "derive the source from the trigger":

- `trigger == "commit"` uses a commit source for `HEAD`.
- all other triggers use the existing working-tree source.

The proposal trigger remains `"commit"` for hook-created proposals. The trigger
is provenance; the diff source is the capture input. Keeping those separate lets
the system say "this proposal came from a commit hook" while capturing the right
bytes.

## Diff sources

First-pass implemented sources:

```python
DiffSource.working_tree()
DiffSource.commit("HEAD")
```

`working_tree` uses the existing `diff_since(repo, "HEAD")` behavior, including
the current handling for untracked files and special path safety.

`commit("HEAD")` reads the patch for a committed revision, using behavior
equivalent to:

```sh
git show --format= --patch HEAD
```

Committed-diff capture should not include untracked working-tree files. Those
files are not part of the commit.

## Future extension points

The internal source boundary should leave room for:

```python
DiffSource.commit("abc123")
DiffSource.range("main", "HEAD")
```

Possible future CLI shapes:

```sh
rgit capture --commit HEAD
rgit capture --commit abc123
rgit capture --range main..HEAD
```

These are intentionally not part of this issue. They matter for default agent
mode later because agents may create several small commits during one task. A
range source can capture the whole branch/task span, while the post-commit hook
can continue to preserve each commit as raw material.

The product model should remain:

- hook capture prevents committed work from being lost;
- manual/range capture can represent a larger semantic unit;
- review/segmentation decides which raw changes become reusable capsules.

## Empty diff behavior

If the selected source has no diff, `segment_diff()` should still return `None`.

For the CLI, the existing friendly empty message can remain for this issue. A
later polish can make the message source-specific, for example:

- `nothing to capture (working tree has no diff)`
- `nothing to capture (commit has no diff)`

The hook already runs with `|| true`, so an empty commit or unsupported commit
shape must not break `git commit`.

## Testing

Add focused tests for:

- `segment_diff(..., trigger="commit")` captures the latest committed diff even
  when the working tree is clean.
- `segment_diff(..., trigger="manual")` keeps capturing working-tree diff.
- `install_hooks()` still writes the same safe `post-commit` hook command.
- An end-to-end hook test: install hooks, commit a change, then verify a pending
  proposal exists and contains the committed diff.

Existing hook safety behavior remains unchanged:

- foreign hooks are not overwritten;
- uninstall only removes research-git-managed hooks;
- hook failures do not fail the user's commit.
