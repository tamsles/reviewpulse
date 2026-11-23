"""Release manifest constants, schema and release_id computation (spec 04, REL-01)."""

from __future__ import annotations

import hashlib
import json
import re
from functools import cache
from pathlib import Path
from typing import Any

from reviewpulse import paths
from reviewpulse.evaluation.protocol import canonical_json

SCHEMA_DIR = Path(__file__).resolve().parents[1] / "schemas"
RELEASES_DIR = "releases"
MANIFEST_FILE = "manifest.json"

# Fixed published file names per manifest `files` entry.
FILE_NAMES = {
    "user_mapping": "user_mapping.json",
    "item_mapping": "item_mapping.json",
    "seen_items": "seen_items.json",
    "eligible_items": "eligible_items.json",
    "pop_fallback": "pop_fallback.json",
    "evaluation": "evaluation.json",
}
MODEL_FILE_NAMES = {"Pop": "pop.json", "BPR": "model.pt"}
RELEASE_ID_RE = re.compile(r"^rel-[a-f0-9]{12}$")


class ReleaseIdError(ValueError):
    """Raised for a malformed release id (also prevents path traversal)."""


@cache
def load_schema(name: str) -> dict[str, Any]:
    return json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))


def release_manifest_schema() -> dict[str, Any]:
    return load_schema("release-manifest.v1.2.schema.json")


def compute_release_id(manifest: dict[str, Any]) -> str:
    body = {k: v for k, v in manifest.items() if k not in ("release_id", "created_at_utc")}
    return "rel-" + hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()[:12]


def releases_dir() -> Path:
    return paths.resolve(RELEASES_DIR)


def check_release_id(release_id: str) -> str:
    if not isinstance(release_id, str) or not RELEASE_ID_RE.match(release_id):
        raise ReleaseIdError(f"invalid release id {release_id!r} (expected rel-<12 hex>)")
    return release_id


def release_path(release_id: str) -> Path:
    return releases_dir() / check_release_id(release_id)
