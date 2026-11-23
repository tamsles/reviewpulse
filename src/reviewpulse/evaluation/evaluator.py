"""Offline evaluator (spec 02, EVAL-01..03).

Definitions (C = train_end, W = evaluation window):
- seen(u): items u reviewed before C with any rating
- eligible: items with any review before C
- rel(u, W): items u rated positively in W, minus seen(u), intersected with eligible
- valid user: reviewed anything before C and |rel| >= 1
Counting choices:
- excluded_* counts are distinct (user, item) positive pairs in W, over all W users
- no_relevance_user_count covers warm users active in W whose rel is empty
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pandas as pd

from reviewpulse.evaluation.protocol import protocol_sha256, validate_protocol
from reviewpulse.io_utils import sha256_lines, utc_now_iso
from reviewpulse.models.base import Recommender
from reviewpulse.ranking import ItemSpace, rank

EVAL_SPLITS = ("validation", "test")


class EvaluationError(ValueError):
    """Raised when an evaluation cannot produce trustworthy metrics."""


@dataclass(frozen=True)
class EvalUser:
    user_id: str  # offline only; never written to evaluation.json
    user_key: str
    relevant: frozenset[str]


@dataclass(frozen=True)
class EvalContext:
    space: ItemSpace
    seen: dict[str, frozenset[str]]  # user_id -> seen items (warm users active in W)
    users: list[EvalUser]  # valid users, sorted by user_id
    protocol: dict[str, Any]
    topk: int
    no_relevance_user_count: int
    excluded_seen_relevance_count: int
    excluded_cold_relevance_count: int
    ground_truth_sha256: str


def build_context(
    train: pd.DataFrame,
    window: pd.DataFrame,
    protocol: dict[str, Any],
    key_fn: Callable[[str], str],
) -> EvalContext:
    topk = validate_protocol(protocol)
    space = ItemSpace.from_items(train["item_id"])

    window_users = set(window["user_id"])
    warm_train = train[train["user_id"].isin(window_users)]
    seen = {u: frozenset(g) for u, g in warm_train.groupby("user_id", sort=True)["item_id"]}

    pos = window.loc[window["is_positive"], ["user_id", "item_id"]].drop_duplicates()
    rel: dict[str, set[str]] = {u: set() for u in seen}
    excluded_seen = excluded_cold = 0
    for user, item in zip(pos["user_id"], pos["item_id"], strict=True):
        user_seen = seen.get(user)
        if user_seen is not None and item in user_seen:
            excluded_seen += 1
        elif item not in space.index:
            excluded_cold += 1
        elif user_seen is not None:
            rel[user].add(item)

    users = [
        EvalUser(user_id=u, user_key=key_fn(u), relevant=frozenset(items))
        for u, items in sorted(rel.items())
        if items
    ]
    gt_lines = sorted(f"{u.user_id}\t{i}" for u in users for i in u.relevant)
    return EvalContext(
        space=space,
        seen=seen,
        users=users,
        protocol=dict(protocol),
        topk=topk,
        no_relevance_user_count=sum(1 for items in rel.values() if not items),
        excluded_seen_relevance_count=excluded_seen,
        excluded_cold_relevance_count=excluded_cold,
        ground_truth_sha256=sha256_lines(gt_lines),
    )


def _dcg_weight(rank_pos: int) -> float:
    return 1.0 / math.log2(rank_pos + 1)


def _ndcg(hits: list[int], n_rel: int, k: int) -> float:
    dcg = sum(_dcg_weight(r) for r in hits)
    idcg = sum(_dcg_weight(r) for r in range(1, min(n_rel, k) + 1))
    return dcg / idcg


def evaluate(
    ctx: EvalContext,
    model: Recommender,
    fallback: Recommender,
    run_id: str,
    eval_split: str,
    split_sha256: str,
) -> dict[str, Any]:
    """Evaluate `model` and return an evaluation manifest (schema v1.2)."""
    if eval_split not in EVAL_SPLITS:
        raise EvaluationError(f"eval_split must be one of {EVAL_SPLITS}")
    if not ctx.users:
        raise EvaluationError("no valid users in the evaluation window")

    k = ctx.topk
    ndcg_sum = recall_sum = 0.0
    fallback_users = 0
    covered: set[str] = set()
    for user in ctx.users:
        res = rank(model, user.user_key, ctx.seen[user.user_id], ctx.space, fallback, k=k)
        fallback_users += res.fallback
        covered.update(res.item_ids)
        hits = [r for r, i in zip(res.ranks, res.item_ids, strict=True) if i in user.relevant]
        ndcg_sum += _ndcg(hits, len(user.relevant), k)
        recall_sum += len(hits) / len(user.relevant)

    n = len(ctx.users)
    metrics = {
        "ndcg_at_10": ndcg_sum / n,
        "recall_at_10": recall_sum / n,
        "coverage_at_10": len(covered) / len(ctx.space),
    }
    for name, value in metrics.items():
        if not math.isfinite(value) or not 0.0 <= value <= 1.0:
            raise EvaluationError(f"metric {name}={value!r} is not finite or out of [0, 1]")

    return {
        "schema_version": "1.2",
        "run_id": run_id,
        "model_kind": model.kind,
        "eval_split": eval_split,
        "split_sha256": split_sha256,
        "protocol_sha256": protocol_sha256(ctx.protocol),
        "ground_truth_sha256": ctx.ground_truth_sha256,
        "protocol_version": str(ctx.protocol["protocol_version"]),
        "metrics": metrics,
        "counts": {
            "valid_user_count": n,
            "no_relevance_user_count": ctx.no_relevance_user_count,
            "fallback_user_count": fallback_users,
            "eligible_item_count": len(ctx.space),
            "excluded_seen_relevance_count": ctx.excluded_seen_relevance_count,
            "excluded_cold_relevance_count": ctx.excluded_cold_relevance_count,
        },
        "created_at_utc": utc_now_iso(),
    }
