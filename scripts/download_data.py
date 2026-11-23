"""Download one Amazon Reviews'23 review file into data/raw/ and print its sha256.

Usage: python scripts/download_data.py --category All_Beauty
Raw data is never committed (data/ is git-ignored).
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
import urllib.request
from pathlib import Path

BASE_URL = "https://mcauleylab.ucsd.edu/public_datasets/data/amazon_2023/raw/review_categories/"
ROOT = Path(__file__).resolve().parents[1]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--category", required=True)
    parser.add_argument("--out-dir", default=str(ROOT / "data" / "raw"))
    args = parser.parse_args(argv)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    name = f"{args.category}.jsonl.gz"
    url = BASE_URL + name
    dest = out_dir / name
    tmp = dest.with_suffix(dest.suffix + ".part")

    if not dest.exists():
        print(f"downloading {url}")
        with urllib.request.urlopen(url, timeout=60) as resp, tmp.open("wb") as f:
            shutil.copyfileobj(resp, f, length=1 << 20)
        tmp.replace(dest)
    print(f"path={dest}")
    print(f"bytes={dest.stat().st_size}")
    print(f"sha256={sha256_file(dest)}")
    print(f"url={url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
