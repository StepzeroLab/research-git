# Agent-Driven Capture Review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One-shot batch review (`rgit review --decide --keep`) so the agent can execute the user's conversational review decision, plus skill rewrites so the agent — not the user — runs the review commands.

**Architecture:** `curation.py` gains `decide(store, proposal_id, keep)` sharing a factored-out capsule-construction helper with `approve()`; `cli.py` wires it as `review --decide [PID] --keep name,name`. The two plugin skill files (`rgit-capture`, `rgit-recall`) are rewritten: the agent presents capsules, asks the user which to keep, and executes the decision itself; approval stays human-gated.

**Tech Stack:** Python (stdlib argparse, sqlite via existing store), pytest.

**Spec:** `docs/superpowers/specs/2026-07-05-agent-driven-capture-review-design.md`

## Global Constraints

- Python 3.11-compatible, Windows-safe (CI matrix: ubuntu/windows/macos, py3.11).
- No new dependencies.
- Run tests with `.venv/bin/python -m pytest` from the repo root (pip is sandbox-blocked; the venv exists. `uv pip install -e ".[dev]"` only if the venv is missing).
- Existing flags `--approve/--index/--name/--dismiss` must keep working unchanged.
- Skill files keep their current paths (`src/rgit/_plugin/skills/<name>/SKILL.md`) — wheel package-data globs in `pyproject.toml` depend on them.
- Skill frontmatter (`name:`, `description:`) is unchanged in both skills.

---

### Task 1: `curation.decide()` — batch approval sharing `approve()`'s construction path

**Files:**
- Modify: `src/rgit/curation.py` (refactor `approve()` lines ~9-58, add `_capsule_from_candidate` + `decide`)
- Test: `tests/test_curation.py`

**Interfaces:**
- Consumes: existing `Store` API (`get_proposal`, `add_feature`, `add_edge`, `set_proposal_status`), `Capsule`/`CodeSlice` models.
- Produces: `decide(store: Store, proposal_id: str, keep: list[str]) -> list[tuple[str, str]]` returning `(candidate_name, feature_id)` pairs in `keep` order (deduplicated). Task 2's CLI handler calls exactly this.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_curation.py` (it already imports `pytest`, `approve`, `dismiss`, `MockSegmenter`, `segment_diff`, `Store`, `Run`, and defines `_seed_proposal`):

```python
from rgit.curation import decide


def _seed_multi_proposal(store, run_id=None):
    def cand(name):
        return {
            "name": name, "intent": f"intent of {name}",
            "code_slices": [{"file": "model.py", "symbol": "forward",
                             "anchor": "L1", "code": f"# {name}", "kind": "wrap"}],
            "knobs": {}, "data_assumptions": None,
            "resurrection_guide": f"guide for {name}", "confidence": 0.9,
        }
    return segment_diff(store, "manual",
                        MockSegmenter([cand("rerank"), cand("cache"),
                                       cand("logging")]), run_id)


def test_decide_keeps_multiple_drops_rest_and_resolves(git_repo):
    store = Store.init(git_repo)
    (git_repo / "model.py").write_text("def forward(x):\n    return x*2\n")
    rid = store.add_run(Run("", "python train.py", "aa", {"acc": 0.9},
                            "abc", None, "2026-06-16T00:00:00"))
    pid = _seed_multi_proposal(store, run_id=rid)
    approved = decide(store, pid, ["rerank", "cache"])
    assert [n for n, _ in approved] == ["rerank", "cache"]
    assert {c.name for c in store.list_features()} == {"rerank", "cache"}
    for _, fid in approved:
        assert store.neighbors(fid, "produced") == [rid]
        assert store.neighbors(fid, "touches") == ["module:model.py"]
    assert store.get_proposal(pid).status == "resolved"


def test_decide_unknown_name_rejects_whole_call_no_partial_writes(git_repo):
    store = Store.init(git_repo)
    (git_repo / "model.py").write_text("def forward(x):\n    return x*2\n")
    pid = _seed_multi_proposal(store)
    with pytest.raises(ValueError, match="typo-name"):
        decide(store, pid, ["rerank", "typo-name"])
    assert store.list_features() == []
    assert store.get_proposal(pid).status == "open"


def test_decide_empty_keep_rejected(git_repo):
    store = Store.init(git_repo)
    (git_repo / "model.py").write_text("def forward(x):\n    return x*2\n")
    pid = _seed_multi_proposal(store)
    with pytest.raises(ValueError, match="nothing to keep"):
        decide(store, pid, [])
    assert store.get_proposal(pid).status == "open"


def test_decide_refused_after_resolve(git_repo):
    store = Store.init(git_repo)
    (git_repo / "model.py").write_text("def forward(x):\n    return x*2\n")
    pid = _seed_multi_proposal(store)
    decide(store, pid, ["rerank"])
    with pytest.raises(ValueError, match="not open"):
        decide(store, pid, ["cache"])
    assert {c.name for c in store.list_features()} == {"rerank"}


def test_decide_single_name_matches_approve_semantics(git_repo):
    store = Store.init(git_repo)
    (git_repo / "model.py").write_text("def forward(x):\n    return x*2\n")
    pid = _seed_proposal(store)
    [(name, fid)] = decide(store, pid, ["double-forward"])
    cap = store.get_feature(fid)
    assert name == "double-forward"
    assert cap.status == "approved"
    assert cap.knobs == {"factor": 2}
    assert store.get_proposal(pid).status == "resolved"


def test_decide_dedupes_repeated_names(git_repo):
    store = Store.init(git_repo)
    (git_repo / "model.py").write_text("def forward(x):\n    return x*2\n")
    pid = _seed_multi_proposal(store)
    approved = decide(store, pid, ["rerank", "rerank"])
    assert len(approved) == 1
    assert len(store.list_features()) == 1
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_curation.py -q`
Expected: ImportError — `cannot import name 'decide' from 'rgit.curation'`.

- [ ] **Step 3: Implement — factor out the helper, add `decide()`**

In `src/rgit/curation.py`, replace the capsule-construction body of `approve()` (everything from `cand = prop.candidates[idx]` through `store.set_proposal_status(...)`; keep all its validation above that point) with a call to a new private helper, and add `decide()`:

```python
def _capsule_from_candidate(store: Store, prop, idx: int,
                            name: Optional[str] = None) -> str:
    """Materialize candidate `idx` as an approved Capsule with its edges.

    Shared by approve() and decide(); does not touch proposal status.
    """
    cand = prop.candidates[idx]
    # A committed-diff capture pins the capsule to the commit that contains the
    # change; only worktree captures fall back to HEAD at approve time.
    base = prop.source_commit or current_commit(store.root)
    cap = Capsule(
        id="", name=name or cand["name"], intent=cand["intent"],
        status="approved", base_commit=base,
        knobs=cand.get("knobs", {}), data_assumptions=cand.get("data_assumptions"),
        resurrection_guide=cand.get("resurrection_guide"), result_summary=None,
        payload_hash=None,
        code_slices=[CodeSlice(**c) for c in cand["code_slices"]])
    fid = store.add_feature(cap)
    for slice_ in cap.code_slices:                       # touches edges
        store.add_edge(fid, f"module:{slice_.file}", "touches")
    if prop.run_id:                                      # produced edge
        store.add_edge(fid, prop.run_id, "produced")
    for src in (prop.from_features or []):               # regenerated from -> variant_of
        store.add_edge(fid, src, "variant_of")
    return fid
```

`approve()` ends with:

```python
    fid = _capsule_from_candidate(store, prop, idx, name)
    store.set_proposal_status(proposal_id, "resolved")
    return fid
```

New function (after `approve()`, before `dismiss()`):

```python
def decide(store: Store, proposal_id: str, keep: list[str]) -> list[tuple[str, str]]:
    """Approve the named candidates, drop the rest, resolve the proposal.

    One call expresses a whole review decision ("keep these"), so an agent
    driving a conversational review executes the user's answer atomically.
    Everything is validated before anything is written: an unknown name
    rejects the whole call with no partial writes.
    """
    prop = store.get_proposal(proposal_id)
    if prop.status != "open":
        raise ValueError(
            f"proposal {proposal_id!r} is {prop.status}, not open; cannot decide "
            f"(re-deciding would create duplicate capsules)")
    ordered = list(dict.fromkeys(keep))          # dedupe, keep order
    if not ordered:
        raise ValueError("nothing to keep; use dismiss to drop the whole proposal")
    by_name: dict[str, int] = {}
    for i, c in enumerate(prop.candidates):      # first occurrence wins, like approve()
        by_name.setdefault(c.get("name"), i)
    unknown = [n for n in ordered if n not in by_name]
    if unknown:
        available = [c.get("name") for c in prop.candidates]
        raise ValueError(
            f"no candidate(s) named {unknown!r} in proposal {proposal_id!r}; "
            f"available: {available}")
    approved = [(n, _capsule_from_candidate(store, prop, by_name[n]))
                for n in ordered]
    store.set_proposal_status(proposal_id, "resolved")
    return approved
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_curation.py -q`
Expected: all pass (existing `approve`/`dismiss` tests prove the refactor is behavior-preserving).

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/rgit/curation.py tests/test_curation.py
git commit -m "feat(curation): decide() — batch keep/drop that resolves a proposal once"
```

---

### Task 2: CLI — `rgit review --decide [PID] --keep name,name`

**Files:**
- Modify: `src/rgit/cli.py` (import at ~line 225, parser at ~line 420 `p_rev`, handler at ~line 791 `if args.cmd == "review":`)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `decide()` from Task 1; existing `_sole_open_proposal(store)` (raises `ValueError` when zero or >1 open proposals).
- Produces: the exact CLI surface the Task 4 skill text teaches: `rgit review --decide <pid> --keep <names>` and its output lines `approved -> <fid>  <name>` / `dropped     <name>` / `proposal <pid> resolved`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli.py` (uses the existing `git_repo`, `monkeypatch`, `capsys` pattern; add imports only if not already present at top: `from rgit import cli`, `from rgit.store.store import Store`, `from rgit.segmenter import MockSegmenter, segment_diff`):

```python
def _seed_three_candidates(git_repo):
    store = Store.init(git_repo)
    (git_repo / "model.py").write_text("def forward(x):\n    return x*2\n")
    def cand(name):
        return {
            "name": name, "intent": f"intent of {name}",
            "code_slices": [{"file": "model.py", "symbol": "forward",
                             "anchor": "L1", "code": f"# {name}", "kind": "wrap"}],
            "knobs": {}, "data_assumptions": None,
            "resurrection_guide": f"guide for {name}", "confidence": 0.9,
        }
    # segment_diff returns a CaptureResult, a str subclass that IS the proposal id
    pid = segment_diff(store, "manual",
                       MockSegmenter([cand("rerank"), cand("cache"),
                                      cand("logging")]), None)
    return store, pid


def test_review_decide_keeps_and_drops(git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    store, pid = _seed_three_candidates(git_repo)
    assert cli.main(["review", "--decide", pid, "--keep", "rerank,cache"]) == 0
    out = capsys.readouterr().out
    assert out.count("approved -> ") == 2
    assert "rerank" in out and "cache" in out
    assert "dropped" in out and "logging" in out
    assert f"proposal {pid} resolved" in out
    assert {c.name for c in store.list_features()} == {"rerank", "cache"}


def test_review_decide_defaults_to_sole_open_proposal(git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    store, pid = _seed_three_candidates(git_repo)
    assert cli.main(["review", "--decide", "--keep", "rerank"]) == 0
    out = capsys.readouterr().out
    assert f"proposal {pid} resolved" in out
    assert {c.name for c in store.list_features()} == {"rerank"}


def test_review_decide_requires_keep(git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    _seed_three_candidates(git_repo)
    assert cli.main(["review", "--decide"]) == 1
    out = capsys.readouterr().out
    assert "--keep" in out and "--dismiss" in out


def test_review_decide_unknown_name_fails_with_hint(git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    store, pid = _seed_three_candidates(git_repo)
    assert cli.main(["review", "--decide", pid, "--keep", "nope"]) == 1
    out = capsys.readouterr().out
    assert "nope" in out and "rgit pending --json" in out
    assert store.list_features() == []
    assert store.get_proposal(pid).status == "open"
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_cli.py -k decide -q`
Expected: SystemExit / exit code 2 from argparse — `unrecognized arguments: --decide` (the `_Parser` may convert this; any failure is fine, passing is not).

- [ ] **Step 3: Implement parser + handler**

In `build_parser()`, after the `--dismiss` argument of `p_rev`:

```python
    p_rev.add_argument("--decide", nargs="?", const="", default=None,
                       metavar="PROPOSAL_ID",
                       help="decide PROPOSAL_ID in one shot — or, with no id, "
                            "the only open proposal; requires --keep")
    p_rev.add_argument("--keep", default=None, metavar="NAME[,NAME...]",
                       help="with --decide: comma-separated candidate names "
                            "to approve; every other candidate is dropped")
```

Update the import near line 225:

```python
from .curation import approve, decide, dismiss
```

In the `if args.cmd == "review":` handler, insert the `--decide` branch first (before the `--dismiss` branch):

```python
        if args.decide is not None:
            keep = [n.strip() for n in (args.keep or "").split(",") if n.strip()]
            if not keep:
                print("--decide requires --keep NAME[,NAME...]; "
                      "to keep nothing, use --dismiss")
                return 1
            try:
                target = args.decide or _sole_open_proposal(store)
                approved = decide(store, target, keep)
            except (KeyError, ValueError) as e:
                print(str(e))
                print("hint: inspect with `rgit pending --json`; if there are "
                      "0 candidates, resegment before deciding.")
                return 1
            kept = {n for n, _ in approved}
            for name, fid in approved:
                print(f"approved -> {fid}  {name}")
            for c in store.get_proposal(target).candidates:
                if c.get("name") not in kept:
                    print(f"dropped     {c.get('name')}")
            print(f"proposal {target} resolved")
            return 0
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_cli.py -q`
Expected: all pass (including the pre-existing review tests).

- [ ] **Step 5: Run the full suite** (guidance-coupling tests parse every `rgit ...` string in the guidance block against this parser — they must still pass)

Run: `.venv/bin/python -m pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/rgit/cli.py tests/test_cli.py
git commit -m "feat(cli): rgit review --decide --keep — one command per review decision"
```

---

### Task 3: e2e — segment three, keep two, both recallable

**Files:**
- Test: `tests/test_e2e.py`

**Interfaces:**
- Consumes: `decide()` (Task 1), existing `segment_diff`, `MockSegmenter`, `recall`, `Store`.

- [ ] **Step 1: Write the test**

Append to `tests/test_e2e.py` (already imports `recall`, `MockSegmenter`, `Store`; add `from rgit.curation import decide` and `from rgit.segmenter import segment_diff` next to the existing imports):

```python
def test_decide_multi_capsule_end_to_end(git_repo):
    store = Store.init(git_repo)
    (git_repo / "model.py").write_text(
        "def forward(x):\n    return rerank(cache(x))\n")

    def cand(name, intent):
        return {
            "name": name, "intent": intent,
            "code_slices": [{"file": "model.py", "symbol": "forward",
                             "anchor": "L2", "code": f"# {name}", "kind": "wrap"}],
            "knobs": {}, "data_assumptions": None,
            "resurrection_guide": f"re-add {name}", "confidence": 0.9,
        }
    # the CaptureResult return value IS the proposal id (str subclass)
    pid = segment_diff(store, "manual", MockSegmenter([
        cand("rerank-retrieval", "re-rank retrieved candidates"),
        cand("query-cache", "cache query embeddings"),
        cand("debug-logging", "temporary logging"),
    ]), None)

    approved = decide(store, pid, ["rerank-retrieval", "query-cache"])
    assert len(approved) == 2

    hits = recall(store, "rerank retrieved")
    assert hits and hits[0]["capsule"].name == "rerank-retrieval"
    hits = recall(store, "cache query embeddings")
    assert hits and hits[0]["capsule"].name == "query-cache"
    assert "debug-logging" not in {c.name for c in store.list_features()}
```

- [ ] **Step 2: Run it**

Run: `.venv/bin/python -m pytest tests/test_e2e.py -q`
Expected: all pass. (This test exercises code written in Task 1, so it passes immediately — it exists to lock the loop: segment → decide → recall.)

- [ ] **Step 3: Commit**

```bash
git add tests/test_e2e.py
git commit -m "test(e2e): segment three candidates, keep two via decide(), recall both"
```

---

### Task 4: rewrite `rgit-capture` SKILL.md — agent-driven step 5, slimmed everywhere

**Files:**
- Modify: `src/rgit/_plugin/skills/rgit-capture/SKILL.md` (full-file replacement below; frontmatter unchanged)

**Interfaces:**
- Consumes: the CLI surface from Task 2 (`rgit review --decide <pid> --keep ...`, `rgit review --dismiss <pid>`).

- [ ] **Step 1: Replace the file body**

Keep the existing frontmatter (lines 1–4: `---`, `name: rgit-capture`, the `description:` line, `---`) byte-identical. Replace everything after it with:

````markdown

# rgit-capture

Orchestrates the two-phase capture: a free, deterministic Phase 1 through the `rgit` CLI, then an agentic Phase 2 dispatched natively onto the host session's subscription — no paid API.

**Prerequisites:** the target repo has been `rgit init`-ed.

**Locating the agent definitions.** On Claude Code the plugin runtime resolves agent paths for you. On other CLIs (Codex, Gemini, opencode) this skill is symlinked into `~/.agents/skills/rgit-capture`, so resolve the plugin root once and reference the agents from there:

```bash
SKILL_REAL=$(realpath ~/.agents/skills/rgit-capture 2>/dev/null || readlink -f ~/.agents/skills/rgit-capture)
PLUGIN_ROOT=$(dirname "$(dirname "$SKILL_REAL")")    # the bundled _plugin/ directory
```

Every `agents/<name>.md` reference below (`agents/capsule-segmenter.md`, `agents/edge-judge.md`) lives at `$PLUGIN_ROOT/agents/<name>.md`.

## Process

### 1. Ensure there are proposals to segment (Phase 1 — free, deterministic)

If the user just made changes and there is no open proposal yet:

```
rgit capture                 # picks for you: uncommitted work, or the last commit when the tree is clean
rgit capture main..HEAD      # a specific span of commits (any A..B range)
```

Repeated captures of the same diff dedup into the existing proposal, and repos with the post-commit hook (`rgit install-hooks`) capture each commit automatically — don't capture the same commit twice. Proposals also appear from `rgit run` and the `rgit watch` daemon.

### 2. Read the pending captures

Run `rgit pending --json` → a list of `{proposal_id, trigger, diff, candidates}`. The `diff` is the raw material; the `candidates` are crude heuristic guesses you are about to replace. If the list is empty, tell the user there is nothing to segment and stop.

### 3. Dispatch the capsule-segmenter subagent (Phase 2 — agentic, on subscription)

For each pending proposal, dispatch a subagent using the **`capsule-segmenter`** agent definition (`agents/capsule-segmenter.md`); run independent proposals concurrently. Pass in the dispatch prompt: `proposal_id`, `repo_root` (absolute path of the target repo), `diff` (verbatim from `rgit pending`), and `symbols` if available. The subagent returns `{"capsules": [...], "dropped": [...]}` — high-quality capsules with real `intent` / `knobs` / `data_assumptions` / `resurrection_guide`, infrastructure noise dropped.

### 4. Write the capsules back

For each proposal, pipe the subagent's `capsules` array back through the CLI:

```
echo '<capsules-json-array>' | rgit resegment <proposal_id> --from-json -
```

This replaces the crude heuristic candidates with the agent-quality ones. Do NOT approve anything yet.

### 5. Review with the user (you run the commands; the user decides)

Approval is human-gated, but the human only decides — never make them type `rgit` commands or copy ids.

1. Show each proposal's capsules: name + one-line intent (+ key knobs if they matter).
2. Ask which capsules to keep — use the client's structured multi-select question UI if it has one, otherwise ask in plain conversation. **Always ask, even when there is a single capsule. Never auto-approve.**
3. Execute the decision yourself, one command per proposal:

```
rgit review --decide <proposal_id> --keep <name>[,<name>...]   # approves these, drops the rest
rgit review --dismiss <proposal_id>                            # the user kept nothing
```

4. Echo the `approved -> <feature_id>` lines back to the user, then continue to step 6.

### 6. Infer graph edges (deterministic baseline + agent-judged relationships)

After approval, wire the new capsules into the graph:

```
rgit edges --apply
```

This writes a neutral `overlaps` baseline edge between capsules touching the same file+symbol and prints `overlap_pairs` (just connected) plus `depends_candidates` (over-produced `{src, dst, evidence}` hypotheses from name overlap). `overlaps` only says "same region" — it does not mean conflict.

Dispatch the **`edge-judge`** subagent (`agents/edge-judge.md`) once, passing both lists plus the referenced capsules' names/intents/slices. It returns confirmed `depends_on` edges and, per overlap pair, a precise relationship: `alternative_to`, `composable_with`, `supersedes` (directed), `conflicts_with`, or "leave as overlaps". Write each result:

```
rgit edges --add depends_on <src> <dst>          # confirmed dependency
rgit edges --add alternative_to <a> <b>          # symmetric: write BOTH directions
rgit edges --add alternative_to <b> <a>
rgit edges --add supersedes <newer> <older>      # directed: one line
```

Pairs the judge leaves unclassified keep their neutral `overlaps` baseline — the graph renderer hides it once a richer edge exists, so delete nothing. Reject coincidental overlaps: a missing edge is cheaper than a wrong one.

## Notes

- **Sibling flow:** recalling a capsule and regenerating it onto today's code is the `rgit-recall` skill, driven by the `capsule-regenerator` agent.
````

- [ ] **Step 2: Sanity checks**

Run: `.venv/bin/python -m pytest tests/test_installer.py tests/test_guidance_coupling.py -q`
Expected: pass (skill file still exists at the same path with the same frontmatter `name:`).

Verify the taught commands parse:

```bash
.venv/bin/python - <<'EOF'
from rgit import cli
for args in (["review", "--decide", "pid", "--keep", "a,b"],
             ["review", "--dismiss", "pid"],
             ["pending", "--json"],
             ["edges", "--apply"],
             ["edges", "--add", "depends_on", "a", "b"]):
    cli.build_parser().parse_args(args)
print("ok")
EOF
```

Expected: `ok`.

- [ ] **Step 3: Commit**

```bash
git add src/rgit/_plugin/skills/rgit-capture/SKILL.md
git commit -m "feat(skill): rgit-capture — agent runs the review, user only decides; slim prose"
```

---

### Task 5: `rgit-recall` SKILL.md — paste-ready close, trimmed notes

**Files:**
- Modify: `src/rgit/_plugin/skills/rgit-recall/SKILL.md` (sections `### 5. Review + close the loop` and `## Notes` only; everything above stays untouched)

- [ ] **Step 1: Replace step 5 and Notes**

Replace from the `### 5. Review + close the loop` heading to end-of-file with:

````markdown
### 5. Review + close the loop

Show the user the resulting working-tree diff (`git diff`) and the subagent's provenance/adaptation notes. **Do not commit, run, or freeze for them** — the human runs the experiment, and that run is what freezes the reproducible artifact.

Hand them a complete, paste-ready command with the real capsule id filled in — never a template with `<placeholders>`. If you don't already know their test command from the conversation, ask for it first. Example of the shape (with a real id):

```
rgit run --from feat_ab12 -- python eval.py --retrieval rerank
```

That records a new `run` node, freezes a byte-exact artifact, links a `produced` edge from the source capsule, and (on approving the resulting proposal) establishes `variant_of` back to the original. If the subagent returned an `updated_resurrection_guide`, write it to a file and add `--refresh-guide-file <path>` to that same command.

## Notes

- **Reproducibility stays intact.** The subagent only *authors*; the human's `rgit run` is the only thing that freezes the artifact — the agent is never in the replay path.
- **Sibling flow:** capture/segmentation is the `rgit-capture` skill (`capsule-segmenter` agent).
````

- [ ] **Step 2: Full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass.

- [ ] **Step 3: Commit**

```bash
git add src/rgit/_plugin/skills/rgit-recall/SKILL.md
git commit -m "feat(skill): rgit-recall — paste-ready close command, deduped notes"
```
