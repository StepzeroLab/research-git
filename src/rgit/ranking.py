from __future__ import annotations
import json
import re

from .store.models import Capsule

ALPHA = 0.5  # weight of the best matching one-hop neighbor
_WORD = re.compile(r"[A-Za-z0-9]+")


def tokenize(text: str) -> list[str]:
    """Lowercase, split on non-alphanumerics, drop empties. Deterministic."""
    if not text:
        return []
    return [w for w in _WORD.findall(text.lower())]


def _hits(text: str, tokens: set[str], weight: float) -> float:
    """weight x (number of distinct query tokens present in `text`)."""
    if not text:
        return 0.0
    words = set(tokenize(text))
    return weight * len(tokens & words)


def lexical_score(capsule: Capsule, query_tokens: list[str]) -> float:
    """Weighted field-hit score for one capsule against the query tokens.

    Weights: intent/name x3 ; knobs/result_summary x2 ; code/guide x1.
    Structural boost: +2 per query token that exactly equals a slice symbol or
    a slice file stem. A token counts once per field (set membership), so longer
    text does not inflate the score. Wildcard-safe: no SQL, pure Python.
    """
    toks = set(query_tokens)
    s = 0.0
    s += _hits(capsule.intent, toks, 3.0)
    s += _hits(capsule.name, toks, 3.0)
    s += _hits(json.dumps(capsule.knobs), toks, 2.0)
    if capsule.result_summary is not None:
        s += _hits(json.dumps(capsule.result_summary.__dict__), toks, 2.0)
    s += _hits(capsule.resurrection_guide or "", toks, 1.0)
    for sl in capsule.code_slices:
        s += _hits(sl.code or "", toks, 1.0)
        if sl.symbol and sl.symbol.lower() in toks:
            s += 2.0
        stem = sl.file.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()
        if stem in toks:
            s += 2.0
    return s


def score(capsule: Capsule, query_tokens: list[str],
          neighbor_lexical: list[float], alpha: float = ALPHA) -> float:
    """Edge-aware final score: own lexical + alpha * best matching neighbor."""
    return lexical_score(capsule, query_tokens) + alpha * max(neighbor_lexical, default=0.0)
