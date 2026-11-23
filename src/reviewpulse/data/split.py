"""Global temporal split into train / valid / test windows (spec 01, SPLIT-01..03).

Window files deliberately omit `source_snapshot_id`: adding a record outside the
train window changes the dataset hash, but must not change train window bytes
(TE-T01). Lineage is recorded in the split manifest via `normalized_sha256`.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import yaml

from reviewpulse import paths
from reviewpulse.data.contracts import DataContractError
from reviewpulse.data.prepare import INTERACTIONS_FILE, MANIFEST_FILE, NORMALIZED_SCHEMA
from reviewpulse.io_utils import sha256_lines, utc_now_iso, write_json_atomic, write_parquet_atomic

WINDOWS = ("train", "valid", "test")
WINDOW_COLUMNS = [n for n in NORMALIZED_SCHEMA.names if n != "source_snapshot_id"]
WINDOW_SCHEMA = pa.schema([NORMALIZED_SCHEMA.field(n) for n in WINDOW_COLUMNS])


@dataclass(frozen=True)
class SplitConfig:
    train_end: pd.Timestamp
    valid_end: pd.Timestamp
    test_end: pd.Timestamp
    evaluation: dict[str, Any] = field(default_factory=dict)
    processed_dir: str = "data/processed"
    split_dir: str = "data/split"


def _parse_boundary(name: str, value: Any) -> pd.Timestamp:
    if value is None:
        raise DataContractError(f"temporal_split.{name} is required")
    if isinstance(value, dt.date) and not isinstance(value, dt.datetime):
        raise DataContractError(f"temporal_split.{name} must include time and 'Z' offset")
    try:
        ts = pd.Timestamp(value)
    except (TypeError, ValueError) as exc:
        raise DataContractError(f"temporal_split.{name} is not a timestamp: {value!r}") from exc
    if ts.tzinfo is None or ts.utcoffset() != dt.timedelta(0):
        raise DataContractError(f"temporal_split.{name} must be an explicit UTC time ('...Z')")
    return ts.tz_convert("UTC")


def parse_split_config(raw: dict[str, Any]) -> SplitConfig:
    ts = raw.get("temporal_split") if isinstance(raw, dict) else None
    if not isinstance(ts, dict):
        raise DataContractError("missing 'temporal_split' section")
    if ts.get("timezone") != "UTC":
        raise DataContractError("temporal_split.timezone must be 'UTC'")
    if ts.get("chronological") is not True:
        raise DataContractError("temporal_split.chronological must be true")
    t0, t1, t2 = (_parse_boundary(n, ts.get(n)) for n in ("train_end", "valid_end", "test_end"))
    if not t0 < t1 < t2:
        raise DataContractError("boundaries must satisfy train_end < valid_end < test_end")
    io = raw.get("io") or {}
    return SplitConfig(
        train_end=t0,
        valid_end=t1,
        test_end=t2,
        evaluation=dict(raw.get("evaluation") or {}),
        processed_dir=str(io.get("processed_dir", "data/processed")),
        split_dir=str(io.get("split_dir", "data/split")),
    )


def load_split_config(path: str | Path) -> SplitConfig:
    with Path(path).open(encoding="utf-8") as f:
        return parse_split_config(yaml.safe_load(f) or {})


def assign_windows(df: pd.DataFrame, cfg: SplitConfig) -> dict[str, pd.DataFrame]:
    """Half-open windows: train < train_end <= valid < valid_end <= test < test_end."""
    t = df["event_time_utc"]
    masks = {
        "train": t < cfg.train_end,
        "valid": (t >= cfg.train_end) & (t < cfg.valid_end),
        "test": (t >= cfg.valid_end) & (t < cfg.test_end),
    }
    return {name: df.loc[m, WINDOW_COLUMNS].reset_index(drop=True) for name, m in masks.items()}


def split_hash(windows: dict[str, pd.DataFrame]) -> str:
    lines = []
    for name in WINDOWS:
        lines.extend(f"{name}\t{i}" for i in sorted(windows[name]["interaction_id"]))
    return sha256_lines(lines)


def _iso(ts: pd.Timestamp) -> str:
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")


def window_stats(windows: dict[str, pd.DataFrame]) -> dict[str, dict[str, int]]:
    # warm user = has any interaction before train_end (i.e. in the train window)
    train_users = set(windows["train"]["user_id"])
    stats = {}
    for name in WINDOWS:
        w = windows[name]
        users = set(w["user_id"])
        s = {"interactions": len(w), "users": len(users), "items": int(w["item_id"].nunique())}
        if name != "train":
            warm = len(users & train_users)
            s["warm_users"] = warm
            s["cold_users"] = len(users) - warm
        stats[name] = s
    return stats


def load_normalized(processed_dir: Path) -> tuple[pd.DataFrame, dict]:
    import json

    manifest_path = processed_dir / MANIFEST_FILE
    if not manifest_path.exists():
        raise DataContractError(f"data manifest not found: {manifest_path} (run data prepare)")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    df = pd.read_parquet(processed_dir / INTERACTIONS_FILE)
    expected = manifest["normalized_sha256"][:16]
    if len(df) and not (df["source_snapshot_id"] == expected).all():
        raise DataContractError("normalized parquet does not match its manifest")
    return df, manifest


def split(cfg: SplitConfig, processed_dir: str | Path | None = None, out_dir=None) -> dict:
    src = Path(processed_dir) if processed_dir is not None else paths.resolve(cfg.processed_dir)
    out = Path(out_dir) if out_dir is not None else paths.resolve(cfg.split_dir)
    df, data_manifest = load_normalized(src)
    windows = assign_windows(df, cfg)
    stats = window_stats(windows)
    if stats["train"]["interactions"] == 0:
        raise DataContractError("train window is empty; check train_end")

    manifest = {
        "train_end": _iso(cfg.train_end),
        "valid_end": _iso(cfg.valid_end),
        "test_end": _iso(cfg.test_end),
        "windows": stats,
        "dropped_at_or_after_test_end": int((df["event_time_utc"] >= cfg.test_end).sum()),
        "split_sha256": split_hash(windows),
        "normalized_sha256": data_manifest["normalized_sha256"],
        "warm_user_definition": "any interaction with event_time_utc < train_end",
        "created_at_utc": utc_now_iso(),
    }
    for name in WINDOWS:
        write_parquet_atomic(windows[name], out / f"{name}.parquet", WINDOW_SCHEMA)
    write_json_atomic(manifest, out / MANIFEST_FILE)
    return manifest
