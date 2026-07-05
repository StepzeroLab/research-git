# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

research-git (`rgit`) is a memory system for agentic coding experiments: it captures code ideas as "Feature Capsules" in a graph beside the repo (`.rgit/`), and later regenerates a chosen idea onto today's codebase. Published to PyPI as `research-git`, console entry point `rgit` (`rgit.cli:main`, also `python -m rgit`).

## Commands

```bash
uv pip install -e ".[dev]"          # install for development (pip is blocked in this sandbox; use uv)
python -m pytest -q                 # run all tests (local: .venv/bin/python -m pytest -q)
python -m pytest tests/test_recall.py -q          # one file
python -m pytest tests/test_cli.py -k capture -q  # one test by keyword
```

pytest is configured in `pyproject.toml` with `pythonpath = ["src"]`, so tests run from the repo root without installing. There is no linter or formatter configured.

CI (`.github/workflows/ci.yml`) runs the suite on ubuntu/windows/macos with Python 3.11 via `uv pip install --system -e ".[dev]"`. `uv.lock` is gitignored — CI uses `uv pip`, not `uv sync`. **Keep code 3.11-compatible and Windows-safe** even though the local venv may be newer.

## Architecture

The core design split is **two planes**:

- **Engine (deterministic, free)** — all Python in `src/rgit/`: the graph store, content-addressed snapshots, diff→symbol mapping, ranking, composing briefs. Never calls an LLM API.
- **Intelligence (delegated)** — a Claude Code plugin shipped inside the wheel at `src/rgit/_plugin/`: three subagents (`capsule-segmenter`, `capsule-regenerator`, `edge-judge`) and two skills (`rgit-capture`, `rgit-recall`). The host agent dispatches these on the user's existing subscription. `.claude-plugin/marketplace.json` points at `src/rgit/_plugin`; the plugin files are wheel package-data, so installer changes must keep `pyproject.toml`'s `package-data` globs in sync.

Anywhere the engine needs intelligence it exposes an interface instead: e.g. `segmenter.py` defines a `Segmenter` Protocol, tests inject `MockSegmenter`, and the real segmentation happens when the plugin's subagent fills that role. Tests never dispatch real agents.

### Store layer (`src/rgit/store/`)

`Store` is a facade over two pieces living under `<repo>/.rgit/`: a SQLite graph (`db.py`, schema + idempotent migrations run on every open) and a sha256 content-addressed immutable blob store (`objects.py`). `models.py` defines the domain dataclasses: `Capsule` (a feature: intent, code slices, knobs, resurrection guide, result), `Run`, `Proposal`, `Event`.

### Pipelines built on the store

- **Capture**: `gitutil.py` (diff sources, worktree freeze) → `astmap.py` (libcst maps diff hunks to touched functions/classes) → `segmenter.py` (clusters diff into candidate capsules → `Proposal`) → `curation.py`/`rgit review` (approve) → `edges.py` deterministically over-produces `depends_on` candidates from identifier overlap, which the `edge-judge` subagent confirms. `watch.py` is background raw-material capture; `hooks.py` is the opt-in git post-commit capture hook; `toggles.py` detects commented-in/out variants.
- **Recall**: `ranking.py` (plain-Python lexical scoring, no embeddings) → `recall.py` (edge-aware: a matching neighbor boosts rank; each hit carries its one-hop subgraph) → `compose.py` (regeneration brief that reads the *current* symbol source via astmap so the agent regenerates against today's code).
- **Run**: `runner.py` executes a command, freezes a byte-exact worktree snapshot into the object store (reproducibility contract: the agent is never in the replay path), and parses metrics (`metrics.py`, `metricdir.py`). Query surfaces over runs: `compare.py`, `ablation.py`, `provenance.py`, `graphview.py`.
- **MCP (`mcp_server.py`, FastMCP)**: read-only query plane (recall/compose/get/compare/ablation/provenance) so a team can share the graph; carries no intelligence by design — don't add mutating tools.
- **Installer**: `installer.py` + `agent_platforms.py` + `agent_guidance.py` wire the plugin, MCP config, and managed guidance blocks into each AI client (claude-code/codex/gemini/opencode/generic); adapters support `dry_run`. `selfupdate.py`/`updatecheck.py` implement `rgit update` and the throttled PyPI check. `cli.py` holds all argparse subcommands.

### Tests

Tests in `tests/` mirror source modules ~1:1. `conftest.py` provides a `git_repo` fixture (temp git repo, `core.autocrlf false` pinned so diffs are byte-identical on Windows — keep new fixtures byte-exact the same way). `test_e2e.py` covers the full loop.

## Notes

- `docs/superpowers/plans/` and `specs/` hold dated design plans for past features — useful context for why a subsystem looks the way it does.
- CLI interactive prompts write to stderr so stdout stays clean JSON; preserve that in new subcommands.
