"""Verified release loading, shared by the gate and the service (spec 04, REL-02).

Any failure raises BundleInvalid(reason). Checks, in order:
manifest schema -> release_id -> files present -> sha256 -> dataset/model
consistency -> user key fingerprint -> model and mappings -> smoke inference.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import jsonschema

from reviewpulse.io_utils import sha256_file
from reviewpulse.keys import UserKeyer, UserKeyError
from reviewpulse.models.bpr import BPRError, BPRModel
from reviewpulse.models.pop import PopModel, PopModelError
from reviewpulse.ranking import ItemSpace, RankingError, RankResult, rank
from reviewpulse.release.manifest import (
    FILE_NAMES,
    MANIFEST_FILE,
    MODEL_FILE_NAMES,
    compute_release_id,
    release_manifest_schema,
    release_path,
)

SMOKE_UNKNOWN_USER_KEY = "reviewpulse-smoke-unknown-user"  # never a 64-hex HMAC key

REASONS = (
    "MANIFEST_INVALID",
    "RELEASE_ID_MISMATCH",
    "FILE_MISSING",
    "HASH_MISMATCH",
    "EVALUATION_MISMATCH",
    "USER_KEY_UNAVAILABLE",
    "MAPPING_MISMATCH",
    "SMOKE_FAILED",
)


class BundleInvalid(RuntimeError):
    def __init__(self, reason: str, detail: str):
        assert reason in REASONS, reason
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


@dataclass
class LoadedRelease:
    release_id: str
    path: Path
    manifest: dict[str, Any]
    evaluation: dict[str, Any]
    model: Any  # PopModel | BPRModel
    fallback: PopModel
    space: ItemSpace
    seen: dict[str, list[str]]
    keyer: UserKeyer

    @property
    def kind(self) -> str:
        return self.manifest["rec_model"]["kind"]

    def recommend_key(self, user_key: str, k: int = 10) -> RankResult:
        seen = self.seen.get(user_key, ())
        return rank(self.model, user_key, seen, self.space, self.fallback, k=k)

    def recommend(self, user_id: str, k: int = 10) -> RankResult:
        return self.recommend_key(self.keyer(user_id), k=k)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BundleInvalid("MANIFEST_INVALID", f"cannot read {path.name}: {exc}") from exc


def _check_manifest(path: Path) -> dict[str, Any]:
    if not (path / MANIFEST_FILE).is_file():
        raise BundleInvalid("FILE_MISSING", f"{MANIFEST_FILE} not found in {path.name}")
    manifest = _read_json(path / MANIFEST_FILE)
    try:
        jsonschema.validate(manifest, release_manifest_schema())
    except jsonschema.ValidationError as exc:
        raise BundleInvalid("MANIFEST_INVALID", exc.message) from exc
    kind = manifest["rec_model"]["kind"]
    expected_names = {"model": MODEL_FILE_NAMES[kind], **FILE_NAMES}
    for role, name in expected_names.items():
        if manifest["files"][role]["path"] != name:
            raise BundleInvalid("MANIFEST_INVALID", f"files.{role}.path must be {name}")
    if (
        manifest["rec_model"]["training_window_end_utc"]
        != manifest["dataset"]["history_cutoff_utc"]
    ):
        raise BundleInvalid("MANIFEST_INVALID", "training window end != history cutoff")
    rid = manifest["release_id"]
    if rid != path.name or rid != compute_release_id(manifest):
        raise BundleInvalid("RELEASE_ID_MISMATCH", f"{rid} does not match its content/dir")
    return manifest


def _check_files(path: Path, manifest: dict[str, Any]) -> None:
    for role, entry in manifest["files"].items():
        f = path / entry["path"]
        if not f.is_file():
            raise BundleInvalid("FILE_MISSING", f"{role} file {entry['path']} not found")
        if sha256_file(f) != entry["sha256"]:
            raise BundleInvalid("HASH_MISMATCH", f"{role} file {entry['path']} sha256 differs")


def _check_evaluation(manifest: dict[str, Any], evaluation: dict[str, Any]) -> None:
    rec, ds = manifest["rec_model"], manifest["dataset"]
    pairs = {
        "run_id": rec["run_id"],
        "model_kind": rec["kind"],
        "split_sha256": ds["split_sha256"],
        "protocol_sha256": ds["protocol_sha256"],
    }
    for key, value in pairs.items():
        if evaluation.get(key) != value:
            raise BundleInvalid("EVALUATION_MISMATCH", f"evaluation.{key} != manifest")


def _keyer(manifest: dict[str, Any], keyer: UserKeyer | None) -> UserKeyer:
    try:
        keyer = keyer or UserKeyer.from_env()
    except UserKeyError as exc:
        raise BundleInvalid("USER_KEY_UNAVAILABLE", str(exc)) from exc
    if keyer.key_id != manifest["user_key"]["key_id"]:
        raise BundleInvalid("USER_KEY_UNAVAILABLE", "RP_USER_KEY fingerprint does not match")
    return keyer


def _load_models(path: Path, manifest: dict[str, Any]):
    kind = manifest["rec_model"]["kind"]
    eligible = _read_json(path / FILE_NAMES["eligible_items"])
    fallback_rows = _read_json(path / FILE_NAMES["pop_fallback"])
    item_mapping = _read_json(path / FILE_NAMES["item_mapping"])
    try:
        fallback = PopModel({str(i): int(s) for i, s in fallback_rows})
        if [i for i, _ in fallback_rows] != fallback.ranked_items():
            raise BundleInvalid("MAPPING_MISMATCH", "pop_fallback is not in Pop order")
        space = ItemSpace.from_items(eligible)
        if list(space.items) != eligible or set(fallback.scores) != set(eligible):
            raise BundleInvalid("MAPPING_MISMATCH", "pop_fallback/eligible items disagree")
        if set(item_mapping) != set(eligible):
            raise BundleInvalid("MAPPING_MISMATCH", "item_mapping != eligible items")
        if kind == "Pop":
            model = PopModel.load(path / MODEL_FILE_NAMES["Pop"])
            if model.scores != fallback.scores:
                raise BundleInvalid("MAPPING_MISMATCH", "pop.json != pop_fallback")
        else:
            model = BPRModel.load(path)
    except (BPRError, PopModelError, RankingError, TypeError, ValueError) as exc:
        raise BundleInvalid("MAPPING_MISMATCH", str(exc)) from exc
    return model, fallback, space


def _smoke(release: LoadedRelease) -> None:
    if release.kind == "BPR":
        known = next(iter(release.model.user_index), None)
    else:
        known = next(iter(release.seen), None)
    if known is None:
        raise BundleInvalid("SMOKE_FAILED", "release has no known user")
    try:
        a = release.recommend_key(known)
        b = release.recommend_key(SMOKE_UNKNOWN_USER_KEY)
    except (RankingError, BPRError, KeyError) as exc:
        raise BundleInvalid("SMOKE_FAILED", str(exc)) from exc
    if not a.item_ids or not b.item_ids:
        raise BundleInvalid("SMOKE_FAILED", "smoke recommendation returned no items")
    if release.kind == "BPR" and (a.fallback or not b.fallback):
        raise BundleInvalid("SMOKE_FAILED", "BPR fallback flag is wrong in smoke test")


def load_release(
    release: str | Path, keyer: UserKeyer | None = None, smoke: bool = True
) -> LoadedRelease:
    """Load and verify a release by id (under RP_HOME/releases) or by directory path."""
    path = Path(release) if isinstance(release, Path) else release_path(release)
    if not path.is_dir():
        raise BundleInvalid("FILE_MISSING", f"release directory {path.name} not found")
    manifest = _check_manifest(path)
    _check_files(path, manifest)
    evaluation = _read_json(path / FILE_NAMES["evaluation"])
    _check_evaluation(manifest, evaluation)
    user_keyer = _keyer(manifest, keyer)
    model, fallback, space = _load_models(path, manifest)
    seen = _read_json(path / FILE_NAMES["seen_items"])
    if not isinstance(seen, dict):
        raise BundleInvalid("MAPPING_MISMATCH", "seen_items must be an object")
    loaded = LoadedRelease(
        release_id=manifest["release_id"],
        path=path,
        manifest=manifest,
        evaluation=evaluation,
        model=model,
        fallback=fallback,
        space=space,
        seen=seen,
        keyer=user_keyer,
    )
    if smoke:
        _smoke(loaded)
    return loaded
