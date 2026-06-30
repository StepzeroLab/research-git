# rgit graph export (text + DOT) — Design

**Status:** Approved (2026-06-28)

## Problem

The graph in `.rgit/graph.db` holds real structure — capsules linked by `variant_of` / `conflicts_with` / `depends_on`, and runs linked by `produced` / `active` — but there is no way to *see* it. The research-layer commands (`compare` / `ablation` / `provenance`) print tables about runs and metrics; none of them shows the shape of the idea graph itself.

We want a read-only command that renders the graph to the terminal. Two constraints shape it:

1. **Deterministic, not model-generated.** rgit emits the graph syntax from code, never from an LLM, so the output is always syntactically valid by construction — there is no "weak model emits broken syntax" failure mode.
2. **Default to zero-dependency text.** DOT (and Mermaid) need an external renderer (`dot`/Graphviz, a Mermaid viewer). A plain-text view needs nothing — it renders in any terminal, pipe, or log. So the default is plain text; DOT is opt-in for when a real picture is wanted.

This stays in character with the project: terminal-only, deterministic, query-only, print to stdout.

## Goals

- `rgit graph` prints a readable plain-text view of the capsule graph to stdout, with no external tooling.
- `rgit graph --dot` emits valid Graphviz DOT to stdout for nicer rendering.
- `--runs` optionally folds run nodes (and `produced` / `active` edges) into either format.

## Non-goals (YAGNI)

- Mermaid output.
- `--serve` / HTML / interactive viewer.
- Metric-based node coloring.
- Status filtering (the `features` table is effectively the approved capsules).
- An MCP `graph_tool`.
- A legend cluster in the DOT.

---

## 1. Architecture

A new query-only module `src/rgit/graphview.py`. No writes; unit-testable in isolation like `compare.py` / `ablation.py`.

```python
def _collect(store, include_runs: bool) -> dict:
    """Gather the graph once: capsule nodes, optional run nodes, typed edges."""

def to_text(store, *, include_runs: bool = False) -> str:
    """Plain-text variant-cluster tree (the default view)."""

def to_dot(store, *, include_runs: bool = False) -> str:
    """Graphviz DOT (the --dot view)."""
```

Both renderers consume the same `_collect` output, so they can never disagree on what the graph contains.

`_collect` returns a dict with:
- `capsules`: list of `Capsule` (from `store.list_features()`).
- `edges`: list of `(src, dst, type)` for the capsule↔capsule types (`variant_of`, `conflicts_with`, `depends_on`), read via `store.conn.execute("SELECT src, dst, type FROM edges WHERE type IN (...)")`.
- `runs`: when `include_runs`, the `Run` objects reachable by `produced` / `active` edges, plus those edges appended to `edges`.

Edge reads go straight through `store.conn.execute` (the pattern `compare.py` already uses); no new Store method is required, though a thin `store.all_edges(types)` helper is acceptable if it keeps the module clean.

## 2. CLI

```
rgit graph            # plain-text variant-cluster tree (default; no renderer needed)
rgit graph --dot      # emit DOT to stdout
rgit graph --runs     # include run nodes + produced/active edges (both formats)
```

- New subparser `graph` with `--dot` (`store_true`) and `--runs` (`store_true`).
- The branch opens the store through the shared guarded `Store.open()` site (so a missing `.rgit/` yields the clean message + exit 1 already implemented), then prints `to_dot(...)` if `args.dot` else `to_text(...)`, and returns 0.
- DOT is the only non-default format; `--dot` selects it. `--runs` composes with either.

## 3. Plain-text view (default)

Capsules are grouped into **variant clusters** — the transitive closure over `variant_of` in both directions, reusing the closure logic from `compare.py` (`_variant_cluster`). Each cluster prints as an indented tree:

- **Root** = the capsule in the cluster that is not itself a variant of anything (i.e. never appears as the `src` of a `variant_of` edge — the original). If a cluster has more than one such capsule, each is printed as its own root in the same block.
- **Children** = capsules that are `variant_of` this node (`src` of an edge whose `dst` is this node), printed indented with `└─` / `├─` connectors, depth-first. A `seen` set guards against cycles.
- **Singletons** = capsules with no `variant_of` edges print as a one-line block.
- **Cross-edge markers** appended to each capsule's line:
  - `conflicts_with` (symmetric) → `⚔ <name>` for each neighbor.
  - `depends_on` (this capsule depends on X) → `→needs <name>` for each outgoing neighbor.
- Clusters are ordered by their root's name; markers within a line are sorted by neighbor name (deterministic output).

With `--runs`, each capsule's runs (its `produced` and `active` runs) are listed indented beneath it, one per line, showing the short run id and its metrics dict (e.g. `run_a33c  {eval_loss: 0.92}`).

Empty graph (no capsules) → prints `(no capsules)`.

Example (capsule-only):

```
temp-0.7
└─ temp-1.0          ⚔ entropy
   └─ temp-1.3
entropy              ⚔ temp-1.0  →needs tokenizer
```

## 4. DOT view (`--dot`)

A single `digraph rgit { rankdir=LR; ... }`.

Nodes:
- **capsule** → `[shape=box style=rounded label="<name>"]`, node id = capsule id (stable, unique; avoids collisions when two capsules share a name).
- **run** (only with `--runs`) → `[shape=ellipse label="<short id>\n<metrics>"]`, node id = run id.

Edges, styled by type (color + linetype + a small `label` so meaning survives in black and white):

| edge | direction | DOT attributes |
|---|---|---|
| `variant_of` | src→dst | `[color=black label="variant_of"]` |
| `depends_on` | src→dst | `[color=blue label="depends_on"]` |
| `conflicts_with` | src—dst | `[color=red style=dashed dir=none label="conflicts_with"]` |
| `produced` (--runs) | capsule→run | `[color=gray style=dotted label="produced"]` |
| `active` (--runs) | run→capsule | `[color=green style=dashed label="active"]` |

Node ids and labels are emitted with quotes escaped so arbitrary names cannot break the syntax. Empty graph → `digraph rgit {\n}` (valid, renders to nothing).

## 5. Error handling

| Situation | Behavior |
|---|---|
| no `.rgit/` | clean message + exit 1 (shared guarded `Store.open()`) |
| empty graph, text | print `(no capsules)`, exit 0 |
| empty graph, `--dot` | print empty `digraph rgit {}`, exit 0 |
| capsule/run name contains quotes or special chars | escaped in both formats; output stays valid |

## 6. Testing

`to_text` (unit, pure string assertions):
- A `variant_of` chain renders as a nested tree with the original as root.
- `conflicts_with` shows `⚔` on both endpoints; `depends_on` shows `→needs` on the dependent only.
- A capsule with no edges renders as a singleton block.
- `--runs` nests each capsule's runs beneath it with metrics; default omits runs.
- Empty store → `(no capsules)`.

`to_dot` (unit):
- Each edge type emits its styled line with the right color/linetype/label.
- Capsule nodes are `box`; with `--runs`, run nodes are `ellipse` and produced/active edges appear; default omits them.
- Output starts with `digraph rgit {` and is balanced; empty store → empty digraph.
- A capsule name containing `"` is escaped (output still has matched braces/quotes).

CLI (`tests/test_cli.py`):
- `rgit graph` on a seeded store prints a tree and exits 0.
- `rgit graph --dot` prints a `digraph rgit {` block and exits 0.
- `rgit graph --runs` output contains a run node / run line.
- `rgit graph` with no `.rgit/` → exit 1 with the clean "no .rgit/" message.
