# Capture source CLI and default guidance design

## Problem

The post-commit hook fix makes `rgit capture --trigger commit` capture the
latest commit diff. That solves hook correctness, but default agent mode still
needs a clearer way to capture meaningful work after different Git states.

Today the managed guidance says:

```text
After meaningful code/research changes, consider `rgit capture --trigger manual`
```

That is too narrow:

- before commit, working-tree capture is correct;
- after one coherent commit, commit capture is correct;
- after several small commits, range capture is usually the right semantic unit;
- if the same diff is captured through multiple paths, duplicate proposals are
  possible.

## Goals

- Add explicit CLI diff sources for committed work.
- Keep `rgit capture` backwards-compatible as working-tree capture.
- Let agents choose their own range base instead of guessing in the CLI.
- Avoid duplicate open proposals for the same captured diff.
- Update default guidance so agents choose capture source by repository state and
  only capture meaningful research/code ideas.

## Non-goals

- Do not add a `--branch` shortcut in this pass.
- Do not combine committed and uncommitted changes into one mixed capture.
- Do not auto-approve proposals.
- Do not install hooks by default.
- Do not solve semantic grouping beyond letting range capture produce one
  proposal that the segmenter can split into candidates.

## CLI design

Keep the existing command:

```sh
rgit capture
```

It remains working-tree capture.

Add an explicit alias:

```sh
rgit capture --worktree
```

Add committed sources:

```sh
rgit capture --commit REV
rgit capture --range BASE..HEAD
```

`--commit REV` captures one commit's own patch, equivalent to:

```sh
git show --format= --patch REV
```

`--range BASE..HEAD` captures the cumulative patch from `BASE` to `HEAD`,
equivalent to:

```sh
git diff BASE HEAD
```

The first version only supports `A..B` as one argument. It does not support
`--range A B`. This keeps the interface close to Git and keeps argparse simple.

The source options are mutually exclusive:

```text
rgit capture --worktree
rgit capture --commit HEAD
rgit capture --range origin/main..HEAD
```

Invalid:

```text
rgit capture --worktree --commit HEAD
rgit capture --commit HEAD --range origin/main..HEAD
```

## Mixed state behavior

If a task has both committed changes and uncommitted changes, capture them
separately:

```sh
rgit capture --range origin/main..HEAD
rgit capture
```

The CLI should not try to merge committed and uncommitted sources. Separate
captures are easier to reason about, easier to dedupe, and preserve clear
provenance.

## Proposal trigger and source

The proposal `trigger` remains provenance: `manual`, `commit`, `watch`, or
`run`. The diff source is the input selection: working tree, commit, or range.

For direct CLI use:

- `rgit capture` uses trigger `manual` and source `working_tree`.
- `rgit capture --commit HEAD` uses trigger `manual` and source `commit(HEAD)`.
- `rgit capture --range origin/main..HEAD` uses trigger `manual` and source
  `range(origin/main, HEAD)`.

For hook use:

- `rgit capture --trigger commit` keeps trigger `commit` and defaults source to
  `commit(HEAD)`.

This keeps hook provenance intact while letting manual/agent captures select a
source explicitly.

## Dedupe design

Before creating a new open proposal, capture should detect whether an open
proposal already exists for the same diff bytes.

Recommended rule:

```text
same normalized diff bytes => same open proposal
```

Do not include trigger in the dedupe key. If the post-commit hook already
captured `HEAD`, and an agent later runs `rgit capture --commit HEAD`, the second
command should not create a duplicate proposal.

Behavior:

- if no open proposal has the same diff, create a new proposal as today;
- if an open proposal has the same diff, return the existing proposal id;
- CLI output should distinguish creation from dedupe, for example:

```text
proposal prop_abc created
```

or:

```text
proposal prop_abc already exists for this diff
```

This is intentionally scoped to open proposals. Approved/dismissed historical
captures should not prevent a user from capturing a similar diff later.

## Range capture shape

`--range` creates one proposal. The segmenter can produce one or more candidates
inside that proposal.

This matches the product model:

- a range is a semantic work unit;
- commits inside that range may be too small individually;
- review/segmentation decides which reusable capsules should be approved.

## Default guidance update

Default mode should describe a decision tree, not a single command.

Proposed managed guidance language:

```text
After meaningful research/code changes, consider capture:
- If there are uncommitted changes, run `rgit capture`.
- If the work was committed as one coherent commit, run `rgit capture --commit HEAD`.
- If the work spans multiple small commits, run `rgit capture --range <base>..HEAD`.
- Skip mechanical formatting, dependency churn, generated files, or changes with
  no reusable research/code idea.
```

The agent chooses `<base>` from context. Common choices include `origin/main`,
`main`, or the branch merge-base. The CLI does not guess this in the first pass.

## Error handling

- `--range` must reject strings that do not contain exactly one `..`.
- Empty source diff returns the existing friendly no-op behavior.
- Invalid Git revisions should return a clear CLI error and non-zero exit code.
- Dedupe should not turn Git errors into "already exists"; it only runs after a
  diff has been read successfully.

## Testing

Add tests for:

- `rgit capture --commit HEAD` captures a clean committed diff.
- `rgit capture --range BASE..HEAD` captures cumulative changes across multiple
  commits as one proposal.
- `rgit capture` still captures working-tree changes.
- source flags are mutually exclusive.
- invalid range strings fail clearly.
- duplicate capture of the same open diff returns the existing proposal instead
  of creating another one.
- default guidance contains the worktree/commit/range decision tree.
