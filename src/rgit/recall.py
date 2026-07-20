from __future__ import annotations
from .ranking import tokenize, lexical_score, score
from .store.store import Store

_SAME_REGION = ("overlaps", "alternative_to", "composable_with", "supersedes", "conflicts_with")


def recall(store: Store, query: str, *, exclude_backfill: bool = False) -> list[dict]:
    """Edge-aware ranked recall over approved capsules.

    Each hit carries its score and both one-hop subgraphs (depends_on,
    overlaps). A capsule surfaces on its own lexical merit; a matching
    neighbor only boosts its rank.
    exclude_backfill drops history-digested capsules from both hits and neighbor subgraphs.
    """
    tokens = tokenize(query)
    if not tokens:
        return []
    caps = [c for c in store.list_features() if c.status == "approved"
            and not (exclude_backfill and c.origin == "backfill")]
    by_id = {c.id: c for c in caps}
    lex = {c.id: lexical_score(c, tokens) for c in caps}

    results = []
    for c in caps:
        if lex[c.id] <= 0:
            continue
        dep_ids = store.neighbors(c.id, "depends_on")
        conf_ids, seen = [], set()
        for t in _SAME_REGION:
            for i in store.neighbors(c.id, t):
                if i not in seen:
                    seen.add(i)
                    conf_ids.append(i)
        neigh_lex = [lex[i] for i in (dep_ids + conf_ids) if i in lex]
        results.append({
            "capsule": c,
            "score": score(c, tokens, neigh_lex),
            "depends_on": [by_id[i] for i in dep_ids if i in by_id],
            "overlaps": [by_id[i] for i in conf_ids if i in by_id],
        })
    results.sort(key=lambda r: (-r["score"], r["capsule"].name))
    return results
