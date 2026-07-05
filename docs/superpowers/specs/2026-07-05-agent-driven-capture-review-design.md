# Agent-driven capture review

**Date:** 2026-07-05
**Status:** approved

## Problem

The capture flow's human gate is implemented as homework. After segmentation, the `rgit-capture` skill shows a one-line summary and then tells the user to type `rgit review --approve <proposal_id> --name <name>` themselves — copying ids and capsule names out of agent output into a terminal. The agent could run these commands; only the *decision* needs a human.

Worse, the engine cannot express the common decision at all: `approve()` resolves the whole proposal after approving **one** candidate (`curation.py`), so when the segmenter splits one diff into three good capsules, the user can keep at most one. "Keep 1 and 3" fails on the second approve.

Secondary: the skill files carry weight that isn't instruction — duplicated rationale, an internal reference (`Understand-Anything plugin`) meaningless to the executing agent — and `rgit-recall` ends by handing the user a command template with placeholders instead of a paste-ready command.

## Principle

**The human makes the decision; the agent does the typing.** Approval stays human-gated — the agent must ask, even for a single capsule — but the mechanics (listing, approving, dismissing, edge-wiring) are the agent's job. One conversational answer ("keep rerank and cache") maps to one engine command.

## Design

### 1. Engine: batch decision — `rgit review --decide`

`curation.py` gains `decide(store, proposal_id, keep: list[str]) -> list[tuple[str, str]]` (returns `(name, feature_id)` pairs):

- **Validate everything before writing anything.** Proposal must be open; `keep` must be non-empty; every name in `keep` must match a candidate name — an unknown name rejects the whole call with the available names listed. No partial writes on rejection.
- Approve each kept candidate via a private helper factored out of `approve()` (candidate → Capsule + `touches`/`produced`/`variant_of` edges), so single-approve and batch-decide share one construction path.
- Candidates not in `keep` are dropped (recorded in the return/output, not stored).
- Set the proposal `resolved` once, at the end. A second `--decide` (or `--approve`) is refused by the existing "not open" check — no duplicate capsules.

CLI: `rgit review --decide [PID] --keep name1,name2`.

- `PID` optional via the existing `_sole_open_proposal` rule (one open proposal → it's the target).
- `--keep` is comma-separated candidate names (segmenter names are slugs; commas cannot appear).
- `--decide` without `--keep` errors with a hint: to keep nothing, use `--dismiss`.
- Output, one line per outcome:

```
approved -> <fid>  <name>
dropped     <name>
proposal <pid> resolved
```

`--approve`, `--index`, `--name`, `--dismiss` are unchanged; `--decide` with `--keep` of a single name is equivalent to today's single approve.

### 2. `rgit-capture` skill: step 5 becomes agent-driven

Step 5 ("Hand back for review") is rewritten:

1. After `resegment`, the agent presents each proposal's capsules directly — name + one-line intent (+ key knobs). The user never runs `rgit review` to see them.
2. The agent asks which capsules to keep — using the client's structured multi-select UI when one exists, plain conversation otherwise. **Always ask, even for a single capsule. Never auto-approve.**
3. The agent executes the answer: `rgit review --decide <pid> --keep <names>` per proposal, or `rgit review --dismiss <pid>` when nothing is kept; then echoes the approved feature ids.
4. Flow continues into step 6 (edges + edge-judge) automatically, as today.

### 3. `rgit-capture` skill: slim the rest

Structure (6 steps) unchanged; cuts only:

- Intro compressed to what-this-does + prerequisites; architecture justification ("no pay-per-use API", "MCP is query-only") dropped from the intro.
- Notes section reduced to the single sibling-flow pointer (`rgit-recall`). The "No paid API" and "Phase 1 vs Phase 2" notes duplicate the intro/steps; the "Understand-Anything plugin" comparison is a dangling internal reference the executing agent cannot use.
- Steps 1 and 6 tightened: keep commands and operational semantics (symmetric edges written both directions, `supersedes` one direction, reject coincidental overlaps), halve the surrounding prose.

Target ≈70 lines from today's 98.

### 4. `rgit-recall` skill: paste-ready close

The reproducibility contract is untouched — the human still runs `rgit run` (the agent is never in the replay path). The change is purely interactional: step 5 instructs the agent to emit the *complete* command with the real capsule id filled in (e.g. `rgit run --from feat_ab12 -- python eval.py`), asking the user only for their test command if it isn't already known from context — never a template with `<placeholders>`. Notes trimmed the same way as capture's (drop the duplicated "No paid API" note).

## Testing

- `decide()`: multi-keep creates one capsule per name, each with `touches` + `produced` edges; unknown name rejects the whole call with no partial writes; empty `keep` rejected; second `--decide`/`--approve` after resolve refused; single-name keep matches `approve()` behavior.
- CLI: `--decide` with omitted PID resolves the sole open proposal; `--decide` without `--keep` errors with the `--dismiss` hint; output format as specified.
- e2e: run → resegment → `--decide` keeping 2 of 3 → both capsules recallable, dropped one absent.
- Existing guidance-coupling tests keep passing (they parse `rgit` commands in the guidance block against the real parser; the block is unchanged).

## Out of scope

- Per-candidate status fields / multi-round decisions (atomic one-shot decide covers the conversational flow).
- Changing who runs `rgit run` in the recall flow (deliberate contract).
- Guidance-block (`agent_guidance.py`) wording changes.
