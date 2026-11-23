"""Raw reviews -> normalized interaction parquet + data manifest (spec 01, DATA-03/04)."""

from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa

from reviewpulse import paths
from reviewpulse.data.contracts import (
    NORMALIZED_COLUMNS,
    REQUIRED_RAW_FIELDS,
    SOURCE_NAME,
    DataContractError,
    SourceConfig,
    validate_raw_records,
)
from reviewpulse.io_utils import sha256_file, sha256_lines, utc_now_iso, write_json_atomic
from reviewpulse.io_utils import write_parquet_atomic

INTERACTIONS_FILE = "interactions.parquet"
MANIFEST_FILE = "manifest.json"
DEDUP_KEY = ["user_id", "item_id", "event_time_utc", "rating"]

NORMALIZED_SCHEMA = pa.schema(
    [
        ("interaction_id", pa.string()),
        ("user_id", pa.string()),
        ("item_id", pa.string()),
        ("event_time_utc", pa.timestamp("ms", tz="UTC")),
        ("rating", pa.float64()),
        ("is_positive", pa.bool_()),
        ("source", pa.string()),
        ("source_snapshot_id", pa.string()),
    ]
)


def read_raw_records(path: str | Path, cfg: SourceConfig) -> list[dict[str, Any]]:
    """Read jsonl / jsonl.gz, keeping only the mapped required fields."""
    path = Path(path)
    keep = [cfg.field_mapping[f] for f in REQUIRED_RAW_FIELDS]
    opener = gzip.open if path.suffix == ".gz" else open
    records = []
    with opener(path, "rt", encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as exc:
                raise DataContractError(f"line {lineno}: invalid JSON") from exc
            if not isinstance(rec, dict):
                raise DataContractError(f"line {lineno}: not a JSON object")
            records.append({k: rec[k] for k in keep if k in rec})
    return records


def _event_ms(df: pd.DataFrame) -> pd.Series:
    return df["event_time_utc"].astype("int64").astype(str)


def _rating_str(df: pd.DataFrame) -> pd.Series:
    # repr of a builtin float, e.g. "5.0" (numpy scalars repr differently)
    return df["rating"].map(lambda r: repr(float(r)))


def interaction_ids(df: pd.DataFrame) -> pd.Series:
    """sha256(user_id|item_id|event_time_ms|rating) truncated to 16 hex chars."""
    keys = df["user_id"] + "|" + df["item_id"] + "|" + _event_ms(df) + "|" + _rating_str(df)
    return keys.map(lambda k: hashlib.sha256(k.encode("utf-8")).hexdigest()[:16])


def normalized_content_hash(df: pd.DataFrame) -> str:
    """Hash of the sorted table content (all columns except source_snapshot_id)."""
    lines = (
        df["interaction_id"]
        + "|"
        + df["user_id"]
        + "|"
        + df["item_id"]
        + "|"
        + _event_ms(df)
        + "|"
        + _rating_str(df)
        + "|"
        + df["is_positive"].map(lambda b: "1" if b else "0")
        + "|"
        + df["source"]
    )
    return sha256_lines(lines.tolist())


def normalize(records: list[dict[str, Any]], cfg: SourceConfig) -> tuple[pd.DataFrame, dict]:
    """Validate, deduplicate, sort. Returns (frame, record_counts)."""
    df = validate_raw_records(records, cfg)
    raw_n = len(df)
    df = df.drop_duplicates(subset=DEDUP_KEY, keep="first")
    df = df.sort_values(DEDUP_KEY, kind="mergesort").reset_index(drop=True)
    df["interaction_id"] = interaction_ids(df)
    if df["interaction_id"].duplicated().any():
        raise DataContractError("interaction_id collision after deduplication")
    df["is_positive"] = df["rating"] >= cfg.positive_rating_threshold
    df["source"] = SOURCE_NAME
    counts = {
        "raw": raw_n,
        "rejected": 0,  # any invalid record fails the batch, so nothing is silently dropped
        "duplicates_removed": raw_n - len(df),
        "final": len(df),
    }
    return df, counts


def prepare(cfg: SourceConfig, out_dir: str | Path | None = None) -> dict:
    """Run the prepare step and return the written manifest."""
    raw_path = paths.resolve(cfg.raw_path)
    out = Path(out_dir) if out_dir is not None else paths.resolve(cfg.processed_dir)
    records = read_raw_records(raw_path, cfg)
    df, counts = normalize(records, cfg)

    normalized_sha = normalized_content_hash(df)
    df["source_snapshot_id"] = normalized_sha[:16]
    df = df[list(NORMALIZED_COLUMNS)]

    manifest = {
        "category": cfg.category,
        "source_url": cfg.source_url,
        "source_sha256": sha256_file(raw_path),
        "normalized_sha256": normalized_sha,
        "timestamp_unit": cfg.timestamp_unit,
        "positive_rating_threshold": cfg.positive_rating_threshold,
        "record_counts": counts,
        "license_review_status": cfg.license_review_status,
        "created_at_utc": utc_now_iso(),
        "normalized_file": INTERACTIONS_FILE,
    }
    write_parquet_atomic(df, out / INTERACTIONS_FILE, NORMALIZED_SCHEMA)
    write_json_atomic(manifest, out / MANIFEST_FILE)
    return manifest
