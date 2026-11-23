"""The single ranking function shared by offline evaluation and the API (MODEL-01).

Responsibilities kept here (and nowhere else):
- candidate set = eligible items minus the user's seen items (full ranking)
- reject non-finite scores
- score descending, ties broken by item_id ascending
- Pop fallback for users the model does not know
- top-k truncation
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

import numpy as np

from reviewpulse.models.base import Recommender


class RankingError(ValueError):
    """Raised for invalid ranking inputs or non-finite model scores."""


@dataclass(frozen=True)
class ItemSpace:
    """Eligible items sorted by item_id, so index order equals tie-break order."""

    items: tuple[str, ...]
    index: dict[str, int] = field(repr=False)

    @classmethod
    def from_items(cls, item_ids: Iterable[str]) -> ItemSpace:
        items = tuple(sorted(set(item_ids)))
        if not items:
            raise RankingError("eligible item set is empty")
        return cls(items=items, index={i: n for n, i in enumerate(items)})

    def __len__(self) -> int:
        return len(self.items)


@dataclass(frozen=True)
class RankResult:
    item_ids: list[str]
    scores: list[float]
    ranks: list[int]
    fallback: bool
    candidate_count: int


def _scores(model: Recommender, user_key: str, space: ItemSpace) -> np.ndarray:
    raw = model.score_items(user_key, space.items)
    scores = np.asarray(raw, dtype=np.float64)
    if scores.shape != (len(space),):
        raise RankingError(
            f"{model.kind} returned {scores.shape} scores for {len(space)} eligible items"
        )
    if not np.isfinite(scores).all():
        raise RankingError(f"{model.kind} produced non-finite scores for a user")
    return scores


def rank(
    model: Recommender,
    user_key: str,
    seen: Iterable[str],
    space: ItemSpace,
    fallback: Recommender,
    k: int = 10,
) -> RankResult:
    if k < 1:
        raise RankingError("k must be >= 1")
    use_fallback = not model.knows_user(user_key)
    scorer = fallback if use_fallback else model
    if use_fallback and not fallback.knows_user(user_key):
        raise RankingError("fallback model must score every user")
    scores = _scores(scorer, user_key, space)

    mask = np.ones(len(space), dtype=bool)
    seen_idx = [space.index[i] for i in seen if i in space.index]
    mask[seen_idx] = False
    cand = np.flatnonzero(mask)
    if cand.size == 0:
        return RankResult([], [], [], use_fallback, 0)

    cand_scores = scores[cand]
    if cand.size > k:
        # keep everything tied with the k-th score, then sort exactly
        kth = np.partition(cand_scores, cand.size - k)[cand.size - k]
        keep = cand_scores >= kth
        cand, cand_scores = cand[keep], cand_scores[keep]
    order = np.lexsort((cand, -cand_scores))[:k]
    top = cand[order]
    return RankResult(
        item_ids=[space.items[i] for i in top],
        scores=[float(s) for s in scores[top]],
        ranks=list(range(1, len(top) + 1)),
        fallback=use_fallback,
        candidate_count=int(mask.sum()),
    )
