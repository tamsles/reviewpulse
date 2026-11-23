"""One-shot final test evaluation (spec 02/06, T14; C-02, OPS-T04).

Workflow:
1. `reviewpulse freeze-test --run-id A --run-id B` records the config/data hashes and
   the runs that will be evaluated in reports/frozen_config.json (committed to git).
2. `reviewpulse evaluate-test --run-id A --confirm-final` evaluates one run on the
   test window, once. It refuses to run without --confirm-final, on a dirty tree,
   when configs/data drifted from the frozen hashes, for runs that were not frozen,
   and for runs that already have a formal record in reports/test_runs.log.
Seen items for the test split still stop at train_end (EVAL-01.6).
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

import pandas as pd

from reviewpulse import paths, tracking
from reviewpulse.data.split import load_split_config
from reviewpulse.evaluation.evaluator import build_context, evaluate
from reviewpulse.evaluation.protocol import protocol_sha256
from reviewpulse.io_utils import sha256_file, utc_now_iso, write_json_atomic
from reviewpulse.keys import UserKeyer
from reviewpulse.models.bpr import BPRModel
from reviewpulse.models.pop import POP_FILE, PopModel

FROZEN_FILE = "reports/frozen_config.json"
TEST_LOG = "reports/test_runs.log"
TEST_EVAL_DIR = "reports/test_eval"
FROZEN_CONFIGS = ("source.yaml", "split.yaml", "train.yaml", "quality.yaml")


class FinalEvalError(RuntimeError):
    """Raised when a final test evaluation is refused."""


def _current_fingerprint(split_config: str) -> dict[str, Any]:
    cfg = load_split_config(paths.resolve(split_config))
    manifest_path = paths.resolve(cfg.split_dir) / "manifest.json"
    if not manifest_path.exists():
        raise FinalEvalError(f"split manifest not found: {manifest_path}")
    split_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return {
        "config_sha256": {
            name: sha256_file(paths.resolve("configs") / name) for name in FROZEN_CONFIGS
        },
        "split_sha256": split_manifest["split_sha256"],
        "normalized_sha256": split_manifest["normalized_sha256"],
        "protocol_sha256": protocol_sha256(cfg.evaluation),
        "boundaries": {k: split_manifest[k] for k in ("train_end", "valid_end", "test_end")},
    }


def freeze(run_ids: list[str], split_config: str = "configs/split.yaml") -> dict[str, Any]:
    path = paths.resolve(FROZEN_FILE)
    if path.exists():
        raise FinalEvalError(f"{FROZEN_FILE} already exists; the final configuration is frozen")
    if not run_ids or len(set(run_ids)) != len(run_ids):
        raise FinalEvalError("give each run to evaluate exactly once")
    git = tracking.git_info()
    if git["git_dirty"] == "true":
        raise FinalEvalError("working tree is dirty; commit before freezing")
    frozen = {
        "frozen_at_utc": utc_now_iso(),
        "git_commit": git["git_commit"],
        "run_ids": list(run_ids),
        **_current_fingerprint(split_config),
    }
    write_json_atomic(frozen, path)
    return frozen


CORRECTION_TYPE = "evaluation_sha256_correction"


def _append_log(record: dict[str, Any]) -> None:
    log_path = paths.resolve(TEST_LOG)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")


def _sha256_bytes(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


def expected_evaluation_sha256(run_id: str, records: list[dict[str, Any]]) -> str:
    """The formal hash of a run's evaluation file, after any recorded correction."""
    formal = [r for r in records if r.get("formal") and r.get("run_id") == run_id]
    if len(formal) != 1:
        raise FinalEvalError(f"run {run_id} has {len(formal)} formal records (expected 1)")
    sha = formal[0]["evaluation_sha256"]
    for r in records:
        if r.get("type") == CORRECTION_TYPE and r.get("run_id") == run_id:
            if r["original_evaluation_sha256"] != sha:
                raise FinalEvalError(f"correction for {run_id} does not chain to the record")
            sha = r["evaluation_sha256"]
    return sha


def verify_test_records() -> list[str]:
    """Check every formal test record against its committed evaluation file.

    Returns a list of problems (empty = consistent).
    """
    records = _formal_records()
    problems = []
    for rec in (r for r in records if r.get("formal")):
        f = paths.resolve(rec["evaluation_file"])
        if not f.is_file():
            problems.append(f"{rec['run_id']}: evaluation file missing")
            continue
        try:
            want = expected_evaluation_sha256(rec["run_id"], records)
        except FinalEvalError as exc:
            problems.append(str(exc))
            continue
        if sha256_file(f) != want:
            problems.append(f"{rec['run_id']}: evaluation file sha256 differs from the log")
        ev = json.loads(f.read_text(encoding="utf-8"))
        for key in ("metrics", "counts", "split_sha256", "ground_truth_sha256"):
            if ev.get(key) != rec.get(key):
                problems.append(f"{rec['run_id']}: evaluation file {key} differs from the log")
    return problems


def correct_evaluation_hash(run_id: str, reason: str) -> dict[str, Any] | None:
    """Append a non-formal record fixing a hash that differs only by CRLF line endings.

    Refuses if the file content differs in any other way. Returns None if the
    recorded hash already matches. Never edits or removes existing records.
    """
    records = _formal_records()
    current_expected = expected_evaluation_sha256(run_id, records)
    formal = next(r for r in records if r.get("formal") and r.get("run_id") == run_id)
    data = paths.resolve(formal["evaluation_file"]).read_bytes()
    actual = _sha256_bytes(data)
    if actual == current_expected:
        return None
    if b"\r" in data:
        raise FinalEvalError("evaluation file still has CRLF line endings; check it out as LF")
    if _sha256_bytes(data.replace(b"\n", b"\r\n")) != current_expected:
        raise FinalEvalError(f"{run_id}: content differs beyond line endings; refusing to correct")
    correction = {
        "formal": False,
        "type": CORRECTION_TYPE,
        "run_id": run_id,
        "corrected_at_utc": utc_now_iso(),
        "original_evaluation_sha256": current_expected,
        "evaluation_sha256": actual,
        "line_endings": {"original": "CRLF", "corrected": "LF"},
        "reason": reason,
    }
    _append_log(correction)
    return correction


def _formal_records() -> list[dict[str, Any]]:
    """All log records (formal and corrections), in order."""
    path = paths.resolve(TEST_LOG)
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(x) for x in lines if x.strip()]


def _load_run(run_id: str, dst: Path):
    import mlflow

    run = mlflow.get_run(run_id)
    art = Path(mlflow.artifacts.download_artifacts(run_id=run_id, dst_path=str(dst)))
    params = dict(run.data.params)
    pop = PopModel.load(art / POP_FILE)
    kind = params.get("model_kind")
    if kind == "Pop":
        return params, pop, pop
    if kind == "BPR":
        return params, BPRModel.load(art), pop
    raise FinalEvalError(f"run {run_id} has unknown model_kind {kind!r}")


def evaluate_test(
    run_id: str,
    confirm_final: bool = False,
    split_config: str = "configs/split.yaml",
    experiment: str = "reviewpulse",
) -> dict[str, Any]:
    if not confirm_final:
        raise FinalEvalError("the test split is evaluated once; pass --confirm-final")
    git = tracking.git_info()
    if git["git_dirty"] == "true":
        raise FinalEvalError("working tree is dirty; the final evaluation needs a clean commit")
    frozen_path = paths.resolve(FROZEN_FILE)
    if not frozen_path.exists():
        raise FinalEvalError(f"{FROZEN_FILE} not found; run freeze-test first")
    frozen = json.loads(frozen_path.read_text(encoding="utf-8"))
    if run_id not in frozen["run_ids"]:
        raise FinalEvalError(f"run {run_id} is not in the frozen list")
    current = _current_fingerprint(split_config)
    for key, value in current.items():
        if frozen.get(key) != value:
            raise FinalEvalError(f"{key} changed since freezing; test evaluation refused")
    if any(r.get("formal") and r.get("run_id") == run_id for r in _formal_records()):
        raise FinalEvalError(f"run {run_id} already has a formal test record")

    keyer = UserKeyer.from_env()
    cfg = load_split_config(paths.resolve(split_config))
    split_dir = paths.resolve(cfg.split_dir)
    train = pd.read_parquet(split_dir / "train.parquet")
    test = pd.read_parquet(split_dir / "test.parquet")

    tracking.configure(experiment)
    with tempfile.TemporaryDirectory() as tmp:
        params, model, pop = _load_run(run_id, Path(tmp))
    expected = {
        "split_sha256": current["split_sha256"],
        "protocol_sha256": current["protocol_sha256"],
        "normalized_sha256": current["normalized_sha256"],
        "user_key_id": keyer.key_id,
    }
    for key, value in expected.items():
        if params.get(key) != value:
            raise FinalEvalError(f"run param {key} does not match the frozen data/key")
    if pop.scores != PopModel.fit(train).scores:
        raise FinalEvalError("run Pop fallback differs from the current train window")

    ctx = build_context(train, test, cfg.evaluation, key_fn=keyer)
    ev = evaluate(ctx, model, pop, run_id, "test", current["split_sha256"])

    eval_path = paths.resolve(TEST_EVAL_DIR) / f"{run_id}.json"
    write_json_atomic(ev, eval_path)
    record = {
        "formal": True,
        "run_id": run_id,
        "model_kind": ev["model_kind"],
        "evaluated_at_utc": ev["created_at_utc"],
        "git_commit": git["git_commit"],
        "split_sha256": ev["split_sha256"],
        "protocol_sha256": ev["protocol_sha256"],
        "ground_truth_sha256": ev["ground_truth_sha256"],
        "metrics": ev["metrics"],
        "counts": ev["counts"],
        "evaluation_file": f"{TEST_EVAL_DIR}/{run_id}.json",
        "evaluation_sha256": sha256_file(eval_path),
    }
    _append_log(record)
    return record
