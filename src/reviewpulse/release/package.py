"""`reviewpulse package --run-id`: build an immutable release bundle (spec 04, REL-01).

Everything the service needs is packaged together (C-04): model, mappings, seen
items (only up to train_end), eligible items, Pop fallback and the validation
evaluation. User ids only appear as HMAC user keys (C-09).
"""

from __future__ import annotations

import json
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any

import pandas as pd

from reviewpulse import paths, tracking
from reviewpulse.data.contracts import DataContractError
from reviewpulse.data.split import load_split_config
from reviewpulse.evaluation.protocol import protocol_sha256
from reviewpulse.io_utils import sha256_file, utc_now_iso
from reviewpulse.keys import UserKeyer
from reviewpulse.models.pop import POP_FILE, PopModel
from reviewpulse.release.manifest import (
    FILE_NAMES,
    MANIFEST_FILE,
    MODEL_FILE_NAMES,
    compute_release_id,
    releases_dir,
)


class PackageError(RuntimeError):
    """Raised when a run cannot be packaged into a consistent release."""


def _dump(obj: Any, path: Path) -> None:
    path.write_text(json.dumps(obj, sort_keys=True, separators=(",", ":")) + "\n", "utf-8")


def pop_fallback_payload(pop: PopModel) -> list[list]:
    return [[i, pop.scores[i]] for i in pop.ranked_items()]


def seen_items_payload(train: pd.DataFrame, keyer: UserKeyer) -> dict[str, list[str]]:
    """user_key -> sorted items reviewed before train_end (any rating)."""
    pairs = train[["user_id", "item_id"]].drop_duplicates()
    seen: dict[str, list[str]] = {}
    for user, items in pairs.groupby("user_id", sort=True)["item_id"]:
        seen[keyer(user)] = sorted(items)
    return seen


def _download_run(run_id: str, dst: Path) -> tuple[dict[str, str], Path]:
    import mlflow

    try:
        run = mlflow.get_run(run_id)
    except Exception as exc:  # mlflow raises its own exception types
        raise PackageError(f"MLflow run {run_id} not found: {exc}") from exc
    local = Path(mlflow.artifacts.download_artifacts(run_id=run_id, dst_path=str(dst)))
    return dict(run.data.params), local


def _check_run(params: dict[str, str], evaluation: dict, expected: dict[str, str]) -> None:
    for key, value in expected.items():
        if params.get(key) != value:
            raise PackageError(
                f"run param {key}={params.get(key)!r} does not match current data ({value!r})"
            )
    if evaluation.get("eval_split") != "validation":
        raise PackageError("run evaluation is not a validation evaluation")
    for key in ("split_sha256", "protocol_sha256"):
        if evaluation.get(key) != expected[key]:
            raise PackageError(f"run evaluation {key} does not match current data")


def package(
    run_id: str,
    split_config: str | Path = "configs/split.yaml",
    experiment: str = "reviewpulse",
    allow_dirty: bool = False,
) -> dict[str, Any]:
    """Package an MLflow run into releases/<release_id>/ and return the manifest."""
    keyer = UserKeyer.from_env()
    split_cfg = load_split_config(paths.resolve(split_config))
    split_dir = paths.resolve(split_cfg.split_dir)
    manifest_path = split_dir / MANIFEST_FILE
    if not manifest_path.exists():
        raise DataContractError(f"split manifest not found: {manifest_path}")
    split_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected = {
        "split_sha256": split_manifest["split_sha256"],
        "protocol_sha256": protocol_sha256(split_cfg.evaluation),
        "normalized_sha256": split_manifest["normalized_sha256"],
        "train_end": split_manifest["train_end"],
        "user_key_id": keyer.key_id,
    }

    current_git = tracking.git_info()
    if current_git["git_dirty"] == "true" and not allow_dirty:
        raise PackageError("working tree is dirty; commit first or pass --allow-dirty")

    tracking.configure(experiment)
    releases = releases_dir()
    releases.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        params, art = _download_run(run_id, Path(tmp))
        kind = params.get("model_kind")
        if kind not in MODEL_FILE_NAMES:
            raise PackageError(f"run {run_id} has unknown model_kind {kind!r}")
        evaluation = json.loads((art / FILE_NAMES["evaluation"]).read_text(encoding="utf-8"))
        if evaluation.get("run_id") != run_id or evaluation.get("model_kind") != kind:
            raise PackageError("run evaluation.json does not belong to this run")
        _check_run(params, evaluation, expected)

        train = pd.read_parquet(split_dir / "train.parquet")
        pop = PopModel.fit(train)
        if PopModel.load(art / POP_FILE).scores != pop.scores:
            raise PackageError("Pop scores in the run differ from the current train window")
        eligible = sorted(pop.scores)
        item_mapping = json.loads((art / FILE_NAMES["item_mapping"]).read_text("utf-8"))
        if set(item_mapping) != set(eligible):
            raise PackageError("run item mapping does not cover exactly the eligible items")

        staging = releases / f".staging-{uuid.uuid4().hex}"
        staging.mkdir()
        try:
            model_name = MODEL_FILE_NAMES[kind]
            shutil.copyfile(art / model_name, staging / model_name)
            for name in ("user_mapping", "item_mapping", "evaluation"):
                shutil.copyfile(art / FILE_NAMES[name], staging / FILE_NAMES[name])
            _dump(seen_items_payload(train, keyer), staging / FILE_NAMES["seen_items"])
            _dump(eligible, staging / FILE_NAMES["eligible_items"])
            _dump(pop_fallback_payload(pop), staging / FILE_NAMES["pop_fallback"])

            files = {"model": model_name, **FILE_NAMES}
            manifest = {
                "schema_version": "1.2",
                "release_id": "",
                "rec_model": {
                    "kind": kind,
                    "run_id": run_id,
                    "training_window_end_utc": split_manifest["train_end"],
                },
                "dataset": {
                    "normalized_sha256": expected["normalized_sha256"],
                    "split_sha256": expected["split_sha256"],
                    "protocol_sha256": expected["protocol_sha256"],
                    "history_cutoff_utc": split_manifest["train_end"],
                },
                "user_key": {"scheme": "hmac-sha256", "key_id": keyer.key_id},
                "files": {
                    role: {"path": name, "sha256": sha256_file(staging / name)}
                    for role, name in files.items()
                },
                "build": {
                    "git_commit": params.get("git_commit", "unknown"),
                    "git_dirty": params.get("git_dirty") != "false"
                    or current_git["git_dirty"] == "true",
                },
                "created_at_utc": utc_now_iso(),
            }
            manifest["release_id"] = compute_release_id(manifest)
            (staging / MANIFEST_FILE).write_text(
                json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            final = releases / manifest["release_id"]
            if final.exists():
                # identical content -> identical release_id: keep the existing bundle
                shutil.rmtree(staging)
                return json.loads((final / MANIFEST_FILE).read_text(encoding="utf-8"))
            staging.rename(final)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
    return manifest
