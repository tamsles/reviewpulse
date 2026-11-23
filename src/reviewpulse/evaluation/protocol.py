"""Evaluation protocol (split.yaml `evaluation` section) validation and hashing."""

from __future__ import annotations

import hashlib
import json
from typing import Any

# The evaluator implements exactly these policies (ADR-0007 / spec 02).
# A config that claims anything else is rejected instead of silently ignored.
IMPLEMENTED_POLICIES = {
    "task": "positive_review_interaction_ranking",
    "ranking_mode": "full",
    "eligible_item_policy": "any_review_before_cutoff",
    "seen_item_policy": "any_rating_before_cutoff",
    "observed_history_policy": "train_only",
    "ground_truth_policy": "positive_minus_seen_intersect_eligible",
    "recall_denominator": "num_relevant",
    "pop_score": "distinct_positive_users_before_cutoff",
    "tie_break": "item_id_ascending",
}


class ProtocolError(ValueError):
    """Raised when the evaluation protocol config is invalid or unsupported."""


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def protocol_sha256(protocol: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(protocol).encode("utf-8")).hexdigest()


def validate_protocol(protocol: dict[str, Any]) -> int:
    """Check the protocol is one this evaluator implements; return top-k."""
    for key, expected in IMPLEMENTED_POLICIES.items():
        if protocol.get(key) != expected:
            raise ProtocolError(
                f"evaluation.{key}={protocol.get(key)!r} is not implemented (expected {expected!r})"
            )
    if not protocol.get("protocol_version"):
        raise ProtocolError("evaluation.protocol_version is required")
    topk = protocol.get("topk")
    if topk != 10:
        raise ProtocolError("evaluation.topk must be 10 (metrics are reported @10)")
    return topk
