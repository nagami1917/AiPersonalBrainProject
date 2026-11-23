"""Ordering and diversity: what happens AFTER the arms return candidates.

This is the third stage the design names, and it is deliberately not a
cross-encoder. An LLM/neural reranker costs a model call per query and is worth it
once retrieval quality is *measurable*; until then it would add latency and a new
failure mode to hide behind. So the stages here are deterministic, cheap, and each
fixes a specific observed failure:

  * `dedupe`           — the same paragraph reached twice through two arms;
  * `limit_per_source` — one long document crowding out better sources;
  * `normalize_fused_scores` — RRF values are rank signals (~0.01–0.03), not
    probabilities, so they must be rescaled before a UI shows them as a bar.

Upgrading to a real reranker means adding a function here that takes a query and
candidates and reorders them; callers already pass a list through this module, so
nothing else has to change. Leaving that seam visible is the point of the file.

Honest limitation, stated rather than implied: RRF discards score magnitude. A
chunk that is a near-perfect vector match can rank below one that is merely good
in both arms. That is the documented trade-off for comparable scales without
per-corpus normalization, and it is why `min_similarity` is applied in SQL before
fusion — relevance must be decided before magnitude is thrown away.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from app.ai.retrieval.vector_search import Hit


def _tokens(text: str) -> set[str]:
    return {w for w in text.lower().split() if len(w) > 2}


def _jaccard(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def dedupe(hits: Sequence[Hit], threshold: float = 0.85) -> list[Hit]:
    """Drop near-identical passages (the same paragraph reached twice)."""
    kept: list[Hit] = []
    for hit in hits:
        if any(
            hit.source_kind == other.source_kind
            and hit.source_id == other.source_id
            and _jaccard(hit.content, other.content) >= threshold
            for other in kept
        ):
            continue
        kept.append(hit)
    return kept


def limit_per_source(hits: Sequence[Hit], max_share: float = 0.6) -> list[Hit]:
    """No single note/document may dominate the context unless it is all we have.

    Applied across the union of both source kinds, keyed on (kind, id), because a
    long document crowding out a better note is the same failure either way.
    """
    if not hits:
        return []
    distinct = {(h.source_kind, h.source_id) for h in hits}
    if len(distinct) <= 1:
        return list(hits)

    cap = max(1, math.ceil(len(hits) * max_share))
    counts: dict[tuple[str, str], int] = {}
    kept: list[Hit] = []
    overflow: list[Hit] = []
    for hit in hits:
        key = (hit.source_kind, hit.source_id)
        if counts.get(key, 0) < cap:
            counts[key] = counts.get(key, 0) + 1
            kept.append(hit)
        else:
            overflow.append(hit)
    return kept + overflow[: max(0, len(overflow) - len(kept))]


def normalize_scores(scores: Sequence[float]) -> list[float]:
    """The same rescaling as `normalize_fused_scores`, for callers holding only
    score values rather than `Hit` objects — citations carry a float, not a Hit.
    """
    if not scores:
        return []
    best = max(scores) or 1.0
    return [round(min(1.0, score / best), 4) for score in scores]


def normalize_fused_scores(hits: Sequence[Hit]) -> list[float]:
    """Map raw RRF values (~0.01–0.03) to a 0–1 display range.

    RRF is a ranking signal, not a probability. Normalizing by the best hit gives
    users a meaningful "how much better than the rest" bar without implying a
    confidence we cannot compute.
    """
    return normalize_scores([h.fused_score for h in hits])


def order(hits: Sequence[Hit], *, limit: int) -> list[Hit]:
    """The full ordering pass, in the one sequence that is correct.

    Dedupe first (a duplicate would otherwise consume a diversity slot), then
    per-source cap, then truncate. Truncating before dedupe would return fewer
    results than requested for no reason.
    """
    ordered = dedupe(hits)
    ordered = limit_per_source(ordered)
    return ordered[:limit]
