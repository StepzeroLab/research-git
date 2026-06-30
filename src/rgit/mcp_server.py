from __future__ import annotations
from typing import Any

from mcp.server.fastmcp import FastMCP

from .ablation import ablation as ablation_fn
from .compare import compare as compare_fn
from .compose import compose
from .provenance import provenance as provenance_fn
from .recall import recall
from .store.store import Store

mcp = FastMCP("research-git")


def _capsule_dict(cap) -> dict[str, Any]:
    return cap.to_dict()


def recall_tool(query: str) -> list[dict]:
    """Find feature capsules by keyword/structure; ranked, with subgraphs."""
    store = Store.open()
    return [{"capsule": _capsule_dict(r["capsule"]),
             "score": r["score"],
             "depends_on": [_capsule_dict(d) for d in r["depends_on"]],
             "overlaps": [_capsule_dict(d) for d in r["overlaps"]]}
            for r in recall(store, query)]


def compose_tool(feature_ids: list[str]) -> dict:
    """Build a regeneration brief for the given capsules onto current code."""
    return compose(Store.open(), feature_ids)


def get_feature_tool(feature_id: str) -> dict:
    """Fetch a single capsule by id."""
    return _capsule_dict(Store.open().get_feature(feature_id))


def list_features_tool() -> list[dict]:
    """List all capsules."""
    return [_capsule_dict(c) for c in Store.open().list_features()]


def compare_tool(target: str, metric: str | None = None) -> dict:
    """Rank a feature's variant cluster by a run metric (read-only)."""
    return compare_fn(Store.open(), target, metric)        # no direction arg -> never writes


def ablation_tool(capsule_ids: list[str], metric: str | None = None) -> dict:
    """Base/+A/+A+B metric grid over a set of capsules (read-only)."""
    return ablation_fn(Store.open(), capsule_ids, metric)


def provenance_tool(run_id: str) -> dict:
    """Per-slice clean-vs-adapted audit for a run (read-only)."""
    return provenance_fn(Store.open(), run_id)


# Register as MCP tools (functions remain directly unit-testable).
mcp.tool()(recall_tool)
mcp.tool()(compose_tool)
mcp.tool()(get_feature_tool)
mcp.tool()(list_features_tool)
mcp.tool()(compare_tool)
mcp.tool()(ablation_tool)
mcp.tool()(provenance_tool)


def run() -> None:  # pragma: no cover - entry point
    mcp.run()


if __name__ == "__main__":  # pragma: no cover
    run()
