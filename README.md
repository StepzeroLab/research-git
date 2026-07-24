<h1 align="center"><img src="assets/logo.png" alt="research-git logo" height="64" align="absmiddle" />&nbsp;&nbsp;&nbsp;research-git</h1>

<p align="center">
  <strong>Reapply or remove previous experiments &amp; features safely on today’s code.</strong>
  <br />
  <em>Works with Claude Code, Codex, Gemini CLI, and opencode.</em>
</p>

<p align="center">
  <a href="#-quick-start"><img src="https://img.shields.io/badge/Quick_Start-blue" alt="Quick Start" /></a>
  <img src="https://img.shields.io/badge/License-MIT-yellow" alt="License: MIT" />
  <img src="https://img.shields.io/badge/Claude_Code-000000" alt="Claude Code" />
  <img src="https://img.shields.io/badge/Codex-000000" alt="Codex" />
  <img src="https://img.shields.io/badge/Python-3.11+-3776AB" alt="Python 3.11+" />
</p>

<p align="center">
  <img src="assets/awesome-rgit-demo.svg" alt="research-git capture and removal workflow in Codex" width="847" />
</p>

research-git is a new Git tool for researchers and developers, built for the agentic coding era.

It captures important experiments and feature decisions as reusable semantic units, so coding agents can reapply, adapt, or safely remove them on today’s codebase.

## Why research-git

AI coding tools can generate many different experiments and features in a day. But when you try to reintroduce a previously removed experiment just a few days later, the codebase may have changed so much that the experiment no longer fits the current infrastructure.

Traditional Git preserves commits and diffs, but it does not preserve the context behind them. It cannot tell an agent which changes belong to an experiment, why they were made, what assumptions they depended on, or what results they produced. Without that context, reverting may erase later work, replaying an old diff may fail against a changed architecture, and removing a feature may damage shared infrastructure.

research-git records experiments and feature decisions as reusable Capsules, capturing their intent, relevant code, dependencies, configuration, results, and restoration guidance. This gives coding agents the context to safely reapply or remove them on today’s codebase without restoring an old snapshot or deleting code piece by piece.

## How it works

One loop: capture each idea into a graph, then regenerate it onto today's code. The engine (blue) is free and deterministic; intelligence happens at exactly two points (green) — subagents dispatched onto your existing subscription, never a paid API.

<p align="center">
  <img src="assets/hero.png" alt="A Git tool for ambitious researchers and developers in the agentic era." width="800" />
</p>

```mermaid
flowchart LR
    A["edit code /<br/>rgit run -- ..."] -->|"free, deterministic"| B["raw proposal<br/>(diff staged)"]
    B -->|"/rgit-capture"| C{{"capsule-<br/>segmenter"}}
    C --> D[("Feature Capsule<br/>graph (.rgit/)")]
    D -->|"/rgit-recall «query»"| E["compose brief vs<br/>today's code"]
    E --> F{{"capsule-<br/>regenerator"}}
    F --> G["reviewable diff<br/>on today's code"]
    G -.->|"rgit run — freeze + link variant"| D

    classDef engine fill:#eef2ff,stroke:#5b6cff,color:#1e2a78;
    classDef agent fill:#eafff0,stroke:#36a85f,color:#0f5132;
    class A,B,D,E,G engine;
    class C,F agent;
```

## The Feature Capsule

Every idea you keep becomes one capsule — a self-contained unit a future agent can read and bring back:

| Field | What it holds |
|-------|---------------|
| **intent** | why this change existed — the hypothesis, not a diff restatement |
| **code slices** | the relevant snippets / files / symbols |
| **knobs** | parameters / flags / configs |
| **dependencies** | other capsules it needs + silent assumptions |
| **result** | metrics / notes / why it worked or didn't, linked to the runs it produced |
| **resurrection guide** | how to regenerate it onto a changed codebase |

Capsules live in a small graph beside your repo (`.rgit/`), on top of normal git. Every run you launch through research-git also freezes a **byte-exact, content-addressed snapshot** of the code that ran — so "the code behind this result" is always a perfect replay, never at the mercy of an agent.

## 🚀 Quick Start

### 1. Install

```bash
pip install research-git
rgit install        # wires research-git into every agent client on this machine
cd your-project
rgit init           # creates the .rgit/ store in your repo
```

That's the whole setup. Start a new agent session afterwards so it picks everything up.

Adopting rgit on a repo that already has history? `rgit init` offers to **digest that history into capsules** — pick a mode in the prompt, then let your agent run the `rgit-digest` skill so recall has something to find from day one.

<details>
<summary>Install details: choosing platforms, guidance modes, capture-on-commit</summary>

- `rgit install claude-code` (or `codex` / `gemini` / `opencode` / `generic`) targets one client; `--list` shows all; `--uninstall` removes.
- The installer also writes a short guidance block into your client's global file (`~/.claude/CLAUDE.md`, `~/.codex/AGENTS.md`, …) so the agent knows when to save ideas. On an interactive terminal you pick how proactive that should be (`default` / `manual-only` / `none`); pass `--guidance <mode>` to choose non-interactively.
- **Optional:** `rgit install-hooks` (per repo) makes every `git commit` stage its own snapshot automatically, so nothing slips through even when you forget. It never touches an existing hook, hooks never approve anything, and `rgit install-hooks --uninstall` removes it. Skip it in CI or shared clones.
- Manual route on Claude Code: `/plugin marketplace add StepzeroLab/research-git` then `/plugin install research-git@research-git`.

</details>

### 2. Working with an agent? Just talk to it

After install your agent does the remembering. Work as usual — it saves each meaningful idea as a Feature Capsule (asking you before anything is kept). Weeks later, when the code has moved on, just ask:

> *"bring back the re-ranking retrieval step"*

The agent finds the capsule and **re-implements the idea onto today's code**, leaving you a reviewable diff. No commands to memorize — but if you like being explicit, `/rgit-capture` saves recent work and `/rgit-recall <what you want back>` brings an idea home.

### 3. Working in the terminal? Three commands

```bash
rgit run -- python eval_agent.py --retrieval rerank   # run an experiment; freezes a byte-exact snapshot + metrics
rgit review                                           # see what's been captured, approve what's worth keeping
rgit compare rerank                                   # which variant won?
```

`rgit capture` saves the current changes (or the last commit) when you're not using `rgit run`. Bringing an idea *back* needs an agent session — that's where the intelligence lives; from the terminal you can always browse the memory with `rgit features` and `rgit graph`.

More commands as your store grows: [More commands](#more-commands).

## Updating

```bash
rgit update
```

Upgrades the package (via whichever of uv/pipx/pip installed it) and refreshes every installed platform surface: the Claude Code plugin copy, MCP config, and the managed guidance blocks. Guidance blocks you have customized or removed are left alone — the command tells you how to restore them instead.

rgit checks PyPI for a newer release at most once a day (in the background, terminal sessions only). Once one is found, it prints a one-line upgrade notice after every qualifying command until you upgrade or turn the notice off — the check is throttled, the reminder is not. Silence it for good with `rgit update --off`, or per-environment with `RGIT_UPDATE_CHECK=0`.

## 🧩 Where it fits

Anywhere you try many variations of one thing and later want a single one back — cleanly, on top of how the code looks now.

- 🤖 **Agent / Prompt engineering** — you tried four prompt structures, two tool-splitting schemes, and a different retrieval step. Last week's version scored better; bring *that* idea back onto the agent you've since rewritten.
- ⚙️ **Backend / Systems** — three caching strategies, two rate-limiters, a reworked query plan. Which won? Pull the winning variant forward without reverting everything built since.
- 🎨 **Frontend** — competing interaction flows and layout variants, half commented out. Resurrect the one that tested best onto the current component tree.

Also at home in ML research — different loss terms, attention blocks, augmentations. Same shape: the experiment is the idea, the metrics are the result, and you want one variant back on today's code.

## 🤝 Share the memory with your team

The graph is served over MCP **read-only** (`recall` / `compose` / `get`, plus the query commands `compare` / `ablation` / `provenance`). Point a teammate's client at your `rgit mcp` server and they get the same Feature Capsules and the same answers — then *their* session regenerates an idea onto *their* code, on *their* subscription. The memory is shared; the intelligence is local.

## 🔧 Under the Hood

### Build the memory, borrow the agent

The engine owns the durable, deterministic parts — the graph, content-addressed object store, git diffing, and the byte-exact run freeze. The agentic parts are delegated to subagents the host already provides. We don't reimplement an agent loop, and we never call a paid API.

### Two-phase capture

A free, deterministic Phase 1 (`libcst` maps diff hunks to the functions/classes they touch) produces a rough candidate for every change. Phase 2 is a dispatched `capsule-segmenter` subagent that clusters the diff into coherent features, drops infrastructure noise, and writes the real intent, knobs, assumptions, and resurrection guide. Once a capsule is approved, the engine deterministically links same-region edges and over-produces `depends_on` candidates from name overlap, which an `edge-judge` subagent confirms or rejects.

### Ranked, edge-aware recall

Recall scores every approved capsule against your query in plain Python — no embeddings, no SQL `LIKE` traps — and boosts a hit when a connected capsule also matches, so related work surfaces together. Each result carries its related subgraph.

### Two planes

- **MCP — shared memory (query-only).** Returns graph snippets; safe to expose so a team shares one memory. Carries no intelligence.
- **Plugin — local intelligence.** Three subagents (`capsule-segmenter`, `capsule-regenerator`, `edge-judge`) and two skills (`rgit-capture`, `rgit-recall`) define *how* a session acts on those snippets, natively, on its own subscription.

### Reproducibility contract

The agent helps you *author*; it is never in the *replay* path. `rgit run` freezes the exact bytes that ran, content-addressed and immutable. "The code behind run X" is a byte-identical re-materialization of a stored blob.

## More commands

The five-step loop above is the core. These show up as your store grows — run `rgit <command> --help` for any of them:

| Command | What it does |
|---------|--------------|
| `rgit watch` | free, deterministic background capture — stages raw material as you edit, so fleeting in-between states aren't lost |
| `rgit capture [REV \| A..B]` | bare: auto-picks the working tree or, when clean, the last commit; pass a commit or an A..B range for precise control |
| `rgit install-hooks` | opt-in: stage every commit's diff via a post-commit hook (not installed by `rgit install`; won't touch an existing hook) — see install details above |
| `rgit run --from <capsule>` | run a recalled variant and link the new run as a `variant_of` the original |
| `rgit compare <query>` | which variant won: ranked table, Δ vs baseline, ★ winner |
| `rgit provenance <run_id>` | per-feature clean (capsule) vs agent-adapted (frozen) diff for a run |
| `rgit mcp` | serve the graph read-only so a teammate's client can recall against it |
| `rgit digest scan [A..B]` | cluster a mature repo's git history into a scored digestion plan (`rgit init` offers this interactively); `rgit digest status` shows progress, the **rgit-digest** skill drains the queue into `origin=backfill` capsules, and `rgit digest clear` removes them all if you change your mind |

## License

<p align="center">
  <strong>MIT</strong> © Stepzero Lab
  <br />
  <sub>Core contributors: Yuxiang Lin · Fengrong Wan · Jiajun Sun</sub>
</p>
