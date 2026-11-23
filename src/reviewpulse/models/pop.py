"""Popularity baseline (spec 03, MODEL-02).

score(i) = number of distinct users who rated i positively before train_end.
Items with only low ratings score 0 but stay recommendable (they are eligible).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import numpy as np
import pandas as pd

POP_FILE = "pop.json"
POP_FORMAT = "reviewpulse.pop.v1"


class PopModelError(ValueError):
    """Raised when a Pop model file is invalid."""


class PopModel:
    kind = "Pop"

    def __init__(self, scores: dict[str, int], known_users: set[str] | None = None):
        self.scores = dict(scores)
        # None = knows everyone (offline evaluation, fallback list). When serving a Pop
        # champion, known_users = users with history before train_end, so users without
        # history go through rank()'s fallback path and are flagged fallback_used=true.
        self.known_users = known_users
        self._cache_key: tuple[str, ...] | None = None
        self._cache_val: np.ndarray | None = None

    @classmethod
    def fit(cls, train: pd.DataFrame) -> PopModel:
        """Fit on the train window only (callers must not pass later data)."""
        items = sorted(set(train["item_id"]))
        pos = train.loc[train["is_positive"], ["user_id", "item_id"]].drop_duplicates()
        counts = pos.groupby("item_id").size()
        return cls({i: int(counts.get(i, 0)) for i in items})

    def knows_user(self, user_key: str) -> bool:
        return self.known_users is None or user_key in self.known_users

    def score_items(self, user_key: str, item_ids: Sequence[str]) -> np.ndarray:
        if item_ids is self._cache_key and self._cache_val is not None:
            return self._cache_val
        vals = np.array([self.scores.get(i, 0) for i in item_ids], dtype=np.float64)
        if isinstance(item_ids, tuple):
            self._cache_key, self._cache_val = item_ids, vals
        return vals

    def ranked_items(self) -> list[str]:
        """Pop order: score desc, item_id asc (used as the fallback list)."""
        return sorted(self.scores, key=lambda i: (-self.scores[i], i))

    def save(self, directory: str | Path) -> Path:
        path = Path(directory) / POP_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"format": POP_FORMAT, "kind": self.kind, "scores": self.scores}
        text = json.dumps(payload, sort_keys=True, indent=1) + "\n"
        path.write_text(text, encoding="utf-8", newline="\n")
        return path

    @classmethod
    def load(cls, path: str | Path) -> PopModel:
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PopModelError(f"cannot read Pop model: {exc}") from exc
        if not isinstance(payload, dict) or payload.get("format") != POP_FORMAT:
            raise PopModelError("not a reviewpulse Pop model file")
        scores = payload.get("scores")
        if not isinstance(scores, dict) or not scores:
            raise PopModelError("Pop model has no scores")
        for item, s in scores.items():
            if not isinstance(item, str) or isinstance(s, bool) or not isinstance(s, int) or s < 0:
                raise PopModelError(f"invalid Pop score for {item!r}: {s!r}")
        return cls(scores)
