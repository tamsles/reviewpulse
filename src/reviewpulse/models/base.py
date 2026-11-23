"""Unified recommender interface (spec 03, MODEL-01)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol


class Recommender(Protocol):
    kind: str  # "Pop" | "BPR"

    def score_items(self, user_key: str, item_ids: Sequence[str]) -> Sequence[float]:
        """Return one score per item_id (numpy arrays are allowed)."""
        ...

    def knows_user(self, user_key: str) -> bool: ...
