"""Data contracts for raw reviews and the normalized interaction table (spec 01)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

SOURCE_NAME = "amazon_review"
TIMESTAMP_UNITS = ("ms", "s")
REQUIRED_RAW_FIELDS = ("user_id", "item_id", "rating", "timestamp")
NORMALIZED_COLUMNS = (
    "interaction_id",
    "user_id",
    "item_id",
    "event_time_utc",
    "rating",
    "is_positive",
    "source",
    "source_snapshot_id",
)
# Converted times outside this window almost always mean a wrong timestamp unit.
MIN_EVENT_TIME = pd.Timestamp("1995-01-01", tz="UTC")
MAX_EVENT_TIME = pd.Timestamp("2100-01-01", tz="UTC")
RATING_MIN = 1.0
RATING_MAX = 5.0


class DataContractError(ValueError):
    """Raised when raw data or configuration violates the data contract."""


@dataclass(frozen=True)
class SourceConfig:
    category: str
    source_url: str
    raw_path: str
    field_mapping: dict[str, str]
    timestamp_unit: str
    positive_rating_threshold: float
    license_review_status: str
    processed_dir: str = "data/processed"


def _require(mapping: dict[str, Any], key: str, where: str) -> Any:
    if not isinstance(mapping, dict) or key not in mapping or mapping[key] in (None, ""):
        raise DataContractError(f"missing required config key '{where}.{key}'")
    return mapping[key]


def parse_source_config(raw: dict[str, Any]) -> SourceConfig:
    src = _require(raw, "source", "<root>")
    unit = src.get("timestamp_unit")
    if unit is None:
        raise DataContractError("source.timestamp_unit must be declared explicitly (ms or s)")
    if unit not in TIMESTAMP_UNITS:
        raise DataContractError(f"source.timestamp_unit must be one of {TIMESTAMP_UNITS}")
    mapping = _require(src, "raw_field_mapping", "source")
    for field in REQUIRED_RAW_FIELDS:
        _require(mapping, field, "source.raw_field_mapping")
    threshold = float(_require(src, "positive_rating_threshold", "source"))
    if not RATING_MIN <= threshold <= RATING_MAX:
        raise DataContractError("positive_rating_threshold must be within [1, 5]")
    output = raw.get("output") or {}
    return SourceConfig(
        category=str(_require(src, "category", "source")),
        source_url=str(_require(src, "source_url", "source")),
        raw_path=str(_require(src, "raw_path", "source")),
        field_mapping={k: str(v) for k, v in mapping.items()},
        timestamp_unit=unit,
        positive_rating_threshold=threshold,
        license_review_status=str(_require(src, "license_review_status", "source")),
        processed_dir=str(output.get("processed_dir", "data/processed")),
    )


def load_source_config(path: str | Path) -> SourceConfig:
    with Path(path).open(encoding="utf-8") as f:
        return parse_source_config(yaml.safe_load(f) or {})


def _is_valid_rating(v: Any) -> bool:
    if isinstance(v, bool) or not isinstance(v, int | float):
        return False
    return math.isfinite(v) and RATING_MIN <= v <= RATING_MAX


def _is_valid_timestamp(v: Any) -> bool:
    if isinstance(v, bool):
        return False
    if isinstance(v, int):
        return True
    return isinstance(v, float) and math.isfinite(v) and v.is_integer()


def validate_raw_records(records: list[dict[str, Any]], cfg: SourceConfig) -> pd.DataFrame:
    """Validate mapped raw records and return a typed frame.

    Any invalid row fails the whole batch (no partial output).
    Returned columns: user_id, item_id, rating, event_time_utc.
    """
    m = cfg.field_mapping
    users, items, ratings, stamps = [], [], [], []
    for lineno, rec in enumerate(records, start=1):
        for field in REQUIRED_RAW_FIELDS:
            if m[field] not in rec:
                raise DataContractError(f"record {lineno}: missing field '{m[field]}'")
        user = rec[m["user_id"]]
        item = rec[m["item_id"]]
        if not isinstance(user, str) or not user.strip():
            raise DataContractError(f"record {lineno}: empty or non-string user_id")
        if not isinstance(item, str) or not item.strip():
            raise DataContractError(f"record {lineno}: empty or non-string item_id")
        rating = rec[m["rating"]]
        if not _is_valid_rating(rating):
            raise DataContractError(f"record {lineno}: invalid rating {rating!r}")
        ts = rec[m["timestamp"]]
        if not _is_valid_timestamp(ts):
            raise DataContractError(f"record {lineno}: invalid timestamp {ts!r}")
        users.append(user.strip())
        items.append(item.strip())
        ratings.append(float(rating))
        stamps.append(int(ts))

    if not records:
        raise DataContractError("no records")
    try:
        times = pd.to_datetime(pd.Series(stamps, dtype="int64"), unit=cfg.timestamp_unit, utc=True)
    except (OverflowError, ValueError, pd.errors.OutOfBoundsDatetime) as exc:
        raise DataContractError(f"timestamp conversion failed: {exc}") from exc
    bad = (times < MIN_EVENT_TIME) | (times >= MAX_EVENT_TIME)
    if bad.any():
        first = int(bad.to_numpy().argmax()) + 1
        raise DataContractError(
            f"record {first}: timestamp {stamps[first - 1]} outside plausible range "
            f"for unit '{cfg.timestamp_unit}'"
        )
    return pd.DataFrame(
        {
            "user_id": pd.Series(users, dtype="string"),
            "item_id": pd.Series(items, dtype="string"),
            "rating": pd.Series(ratings, dtype="float64"),
            "event_time_utc": times.astype("datetime64[ms, UTC]"),
        }
    )
