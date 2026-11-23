"""T01 data spike: inspect a raw review file and evaluate candidate split boundaries.

Usage: python scripts/data_spike.py --raw data/raw/All_Beauty.jsonl.gz
Prints aggregate statistics only (no raw user ids or review text).
"""

from __future__ import annotations

import argparse
import gzip
import itertools
import json
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

from reviewpulse.data.timeutil import infer_timestamp_unit

POS = 4.0


def read_head(path: Path, n: int) -> list[dict]:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return [json.loads(line) for line in itertools.islice(f, n)]


def read_all(path: Path) -> pd.DataFrame:
    rows = []
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            rows.append(
                (r.get("user_id"), r.get("parent_asin"), r.get("rating"), r.get("timestamp"))
            )
    return pd.DataFrame(rows, columns=["user_id", "item_id", "rating", "timestamp"])


def eval_boundaries(df: pd.DataFrame, train_end: str, valid_end: str, test_end: str) -> dict:
    t0, t1, t2 = (pd.Timestamp(x, tz="UTC") for x in (train_end, valid_end, test_end))
    tr = df[df.t < t0]
    va = df[(df.t >= t0) & (df.t < t1)]
    te = df[(df.t >= t1) & (df.t < t2)]
    total = len(df)
    eligible = set(tr.item_id)
    seen = set(zip(tr.user_id, tr.item_id, strict=True))
    train_users = set(tr.user_id)

    def valid_users(win: pd.DataFrame) -> int:
        pos = win[win.rating >= POS]
        pos = pos[pos.user_id.isin(train_users) & pos.item_id.isin(eligible)]
        keep = [(u, i) not in seen for u, i in zip(pos.user_id, pos.item_id, strict=True)]
        return pos[keep].user_id.nunique()

    return {
        "train_end": train_end,
        "valid_end": valid_end,
        "test_end": test_end,
        "train_n": len(tr),
        "valid_n": len(va),
        "test_n": len(te),
        "after_test_end_n": int((df.t >= t2).sum()),
        "valid_pct": round(100 * len(va) / total, 2),
        "test_pct": round(100 * len(te) / total, 2),
        "valid_warm_users": va[va.user_id.isin(train_users)].user_id.nunique(),
        "valid_users_eval": valid_users(va),
        "test_users_eval": valid_users(te),
        "eligible_items": len(eligible),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", required=True)
    args = parser.parse_args(argv)
    path = Path(args.raw)

    head = read_head(path, 1000)
    print("## fields (first 1000 lines)")
    print(dict(Counter(k for r in head for k in r)))
    print("value types:", {k: type(v).__name__ for k, v in head[0].items()})
    unit = infer_timestamp_unit([r["timestamp"] for r in head])
    print("inferred timestamp unit:", unit)
    for r in head[:5]:
        ts = r["timestamp"]
        print(" ", ts, "->", pd.to_datetime(ts, unit=unit, utc=True).isoformat())
    print("user_id lengths:", sorted(Counter(len(r["user_id"]) for r in head).items()))
    print("parent_asin lengths:", sorted(Counter(len(r["parent_asin"]) for r in head).items()))
    print("parent_asin == asin share:", sum(r["parent_asin"] == r.get("asin") for r in head) / 1000)

    df = read_all(path)
    print("\n## scale")
    print("reviews:", len(df))
    print(
        "null user/item/rating/timestamp:",
        df[["user_id", "item_id", "rating", "timestamp"]].isna().sum().to_dict(),
    )
    df["t"] = pd.to_datetime(df.timestamp, unit=unit, utc=True)
    print("users:", df.user_id.nunique(), "items:", df.item_id.nunique())
    print(
        "exact duplicates (user,item,timestamp,rating):",
        int(df.duplicated(["user_id", "item_id", "timestamp", "rating"]).sum()),
    )
    print("time range:", df.t.min().isoformat(), "..", df.t.max().isoformat())
    print("rating distribution:", df.rating.value_counts().sort_index().to_dict())
    print("positive share (>=4):", round(float((df.rating >= POS).mean()), 4))
    upc = df.groupby("user_id").size()
    print(
        "reviews per user: mean",
        round(float(upc.mean()), 3),
        "share with >=2:",
        round(float((upc >= 2).mean()), 4),
        "max",
        int(upc.max()),
    )

    print("\n## yearly counts")
    print(df.groupby(df.t.dt.year).size().to_string())
    print("\n## monthly counts since 2019-01")
    recent = df[df.t >= pd.Timestamp("2019-01-01", tz="UTC")]
    print(recent.groupby(recent.t.dt.strftime("%Y-%m")).size().to_string())

    q = df.t.quantile([0.8, 0.9]).tolist()
    print("\n## time quantiles: 80% =", q[0].isoformat(), " 90% =", q[1].isoformat())

    print("\n## candidate boundaries")
    end = (df.t.max() + pd.offsets.MonthBegin(1)).normalize().strftime("%Y-%m-%d")
    candidates = [
        (
            q[0].tz_localize(None).to_period("M").to_timestamp().strftime("%Y-%m-%d"),
            q[1].tz_localize(None).to_period("M").to_timestamp().strftime("%Y-%m-%d"),
            end,
        ),
        ("2021-01-01", "2021-07-01", "2022-01-01"),
        ("2021-07-01", "2022-01-01", "2022-07-01"),
        ("2022-01-01", "2022-07-01", "2023-01-01"),
    ]
    rows = [eval_boundaries(df, *c) for c in dict.fromkeys(candidates)]
    print(pd.DataFrame(rows).to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
