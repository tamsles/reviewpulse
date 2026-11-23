"""Deterministic hashing and atomic file writing helpers."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_lines(lines: Iterable[str]) -> str:
    """Hash newline-terminated lines; order matters, so callers sort first."""
    h = hashlib.sha256()
    for line in lines:
        h.update(line.encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()


def utc_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _tmp_path(path: Path) -> Path:
    return path.with_name(f".{path.name}.tmp")


def write_parquet_atomic(df: pd.DataFrame, path: str | Path, schema: pa.Schema) -> None:
    """Write a parquet file whose bytes depend only on the data and pyarrow version."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pandas(df, schema=schema, preserve_index=False)
    table = table.replace_schema_metadata(None)
    tmp = _tmp_path(path)
    pq.write_table(table, tmp, compression="zstd", write_statistics=True)
    os.replace(tmp, path)


def write_json_atomic(obj: Any, path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _tmp_path(path)
    tmp.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)
