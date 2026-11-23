"""`reviewpulse monitor summary` (spec 05, MON-02).

Groups events by (loaded_release_id, source). Groups with fewer than `min_events`
events report INSUFFICIENT_DATA instead of rates and percentiles. Online
recommendation quality is always UNAVAILABLE: there are no real impressions or
clicks, and offline NDCG must not be presented as an online metric (C-12).
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np

INSUFFICIENT = "INSUFFICIENT_DATA"
ONLINE_QUALITY = {
    "status": "UNAVAILABLE",
    "why": "no real impressions/clicks are collected; offline NDCG is not an online metric",
}


def _rate(num: int, den: int) -> float | None:
    return None if den == 0 else num / den


def summarize_group(events: list[dict[str, Any]], min_events: int) -> dict[str, Any]:
    n = len(events)
    ok = [e for e in events if e["http_status"] == 200]
    out: dict[str, Any] = {
        "requests": n,
        "http_2xx": len(ok),
        "http_4xx": sum(1 for e in events if 400 <= e["http_status"] < 500),
        "http_5xx": sum(1 for e in events if e["http_status"] >= 500),
    }
    if n < min_events:
        out["status"] = INSUFFICIENT
        for key in ("latency_p50_ms", "latency_p95_ms", "fallback_rate", "empty_rate"):
            out[key] = INSUFFICIENT
        return out
    lat = np.array([e["latency_ms"] for e in events], dtype=float)
    out.update(
        status="OK",
        latency_p50_ms=float(np.percentile(lat, 50)),
        latency_p95_ms=float(np.percentile(lat, 95)),
        fallback_rate=_rate(sum(1 for e in ok if e["fallback_used"]), len(ok)),
        empty_rate=_rate(sum(1 for e in ok if e["returned_item_count"] == 0), len(ok)),
    )
    return out


def summarize(events: list[dict[str, Any]], min_events: int = 20) -> dict[str, Any]:
    groups: dict[tuple, list[dict[str, Any]]] = defaultdict(list)
    for e in events:
        groups[(e["loaded_release_id"], e["source"])].append(e)
    rows = []
    for (release_id, source), evs in sorted(
        groups.items(), key=lambda kv: (str(kv[0][0]), kv[0][1])
    ):
        rows.append(
            {"loaded_release_id": release_id, "source": source, **summarize_group(evs, min_events)}
        )
    return {
        "total_events": len(events),
        "min_events": min_events,
        "percentile_method": "numpy linear interpolation",
        "rates_denominator": "HTTP 200 responses",
        "groups": rows,
        "online_recommendation_quality": ONLINE_QUALITY,
    }
