"""`reviewpulse train`: fit Pop or BPR on train, evaluate on validation, log to MLflow."""

from __future__ import annotations

import itertools
import json
import tempfile
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from reviewpulse import paths
from reviewpulse.data.contracts import DataContractError
from reviewpulse.data.prepare import MANIFEST_FILE
from reviewpulse.data.split import SplitConfig, load_split_config
from reviewpulse.evaluation.evaluator import EvalContext, build_context, evaluate
from reviewpulse.evaluation.protocol import protocol_sha256
from reviewpulse.keys import UserKeyer
from reviewpulse.models.bpr import ITEM_MAPPING_FILE, USER_MAPPING_FILE, BPRConfig, BPRModel
from reviewpulse.models.pop import PopModel

EVALUATION_FILE = "evaluation.json"
MAX_GRID = 8
MODEL_KINDS = ("Pop", "BPR")


@dataclass(frozen=True)
class TrainSettings:
    seed: int
    experiment: str
    bpr_default: dict[str, float]
    bpr_grid: dict[str, list]
    max_epochs: int
    patience: int
    batch_size: int


def load_train_settings(path: str | Path) -> TrainSettings:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    bpr = raw.get("bpr") or {}
    try:
        return TrainSettings(
            seed=int(raw["seed"]),
            experiment=str((raw.get("mlflow") or {}).get("experiment", "reviewpulse")),
            bpr_default=dict(bpr["default"]),
            bpr_grid={k: list(v) for k, v in bpr["grid"].items()},
            max_epochs=int(bpr["max_epochs"]),
            patience=int(bpr["patience"]),
            batch_size=int(bpr["batch_size"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise DataContractError(f"invalid train config: {exc}") from exc


def bpr_configs(settings: TrainSettings, grid: bool) -> list[BPRConfig]:
    common = {
        "max_epochs": settings.max_epochs,
        "patience": settings.patience,
        "batch_size": settings.batch_size,
        "seed": settings.seed,
    }
    if not grid:
        return [BPRConfig(**settings.bpr_default, **common)]
    keys = sorted(settings.bpr_grid)
    combos = list(itertools.product(*(settings.bpr_grid[k] for k in keys)))
    if len(combos) > MAX_GRID:
        raise DataContractError(f"BPR grid has {len(combos)} combinations (max {MAX_GRID})")
    return [BPRConfig(**dict(zip(keys, c, strict=True)), **common) for c in combos]


@dataclass
class Inputs:
    train: pd.DataFrame
    ctx: EvalContext
    split_manifest: dict[str, Any]
    normalized_sha256: str
    protocol_sha256: str
    keyer: UserKeyer


def _check_split_matches_config(manifest: dict, cfg: SplitConfig) -> None:
    for name in ("train_end", "valid_end", "test_end"):
        configured = getattr(cfg, name).strftime("%Y-%m-%dT%H:%M:%SZ")
        if manifest.get(name) != configured:
            raise DataContractError(
                f"split manifest {name}={manifest.get(name)} differs from split.yaml "
                f"({configured}); re-run `reviewpulse data split`"
            )


def load_inputs(split_cfg: SplitConfig, keyer: UserKeyer) -> Inputs:
    split_dir = paths.resolve(split_cfg.split_dir)
    manifest_path = split_dir / MANIFEST_FILE
    if not manifest_path.exists():
        raise DataContractError(f"split manifest not found: {manifest_path} (run data split)")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    _check_split_matches_config(manifest, split_cfg)
    train = pd.read_parquet(split_dir / "train.parquet")
    valid = pd.read_parquet(split_dir / "valid.parquet")
    ctx = build_context(train, valid, split_cfg.evaluation, key_fn=keyer)
    return Inputs(
        train=train,
        ctx=ctx,
        split_manifest=manifest,
        normalized_sha256=manifest["normalized_sha256"],
        protocol_sha256=protocol_sha256(split_cfg.evaluation),
        keyer=keyer,
    )


def _common_params(inp: Inputs, settings: TrainSettings, git: dict[str, str]) -> dict[str, Any]:
    return {
        "seed": settings.seed,
        "split_sha256": inp.split_manifest["split_sha256"],
        "protocol_sha256": inp.protocol_sha256,
        "normalized_sha256": inp.normalized_sha256,
        "train_end": inp.split_manifest["train_end"],
        "user_key_id": inp.keyer.key_id,
        **git,
    }


def _metrics(ev: dict[str, Any]) -> dict[str, float]:
    m = {f"rec/val/{k}": float(v) for k, v in ev["metrics"].items()}
    m.update({f"rec/val/count/{k}": float(v) for k, v in ev["counts"].items()})
    return m


def run_pop(inp: Inputs, settings: TrainSettings, git: dict[str, str]) -> dict[str, Any]:
    import mlflow

    with mlflow.start_run(run_name="Pop") as run:
        start = time.perf_counter()
        pop = PopModel.fit(inp.train)
        seconds = time.perf_counter() - start
        ev = evaluate(inp.ctx, pop, pop, run.info.run_id, "validation", _sha(inp))
        mlflow.log_params({"model_kind": "Pop", **_common_params(inp, settings, git)})
        mlflow.log_metrics({**_metrics(ev), "train_seconds": seconds})
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            pop.save(d)
            _write(d / ITEM_MAPPING_FILE, {i: n for n, i in enumerate(inp.ctx.space.items)})
            _write(d / USER_MAPPING_FILE, {})  # Pop scores every user identically
            _write(d / EVALUATION_FILE, ev)
            mlflow.log_artifacts(str(d))
        return {"run_id": run.info.run_id, "model_kind": "Pop", "evaluation": ev}


def run_bpr(
    inp: Inputs,
    settings: TrainSettings,
    git: dict[str, str],
    cfg: BPRConfig,
    log: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    import mlflow

    pop = PopModel.fit(inp.train)
    with mlflow.start_run(run_name=f"BPR-d{cfg.dim}-lr{cfg.lr}-reg{cfg.reg}") as run:
        run_id = run.info.run_id
        mlflow.log_params(
            {"model_kind": "BPR", **asdict(cfg), **_common_params(inp, settings, git)}
        )

        def eval_fn(model: BPRModel) -> float:
            return evaluate(inp.ctx, model, pop, run_id, "validation", _sha(inp))["metrics"][
                "ndcg_at_10"
            ]

        model, hist = BPRModel.fit(inp.train, inp.keyer, cfg, eval_fn=eval_fn, log=log)
        for step, (loss, ndcg) in enumerate(zip(hist.losses, hist.val_ndcg, strict=True), 1):
            mlflow.log_metrics({"train/loss": loss, "rec/val/epoch_ndcg_at_10": ndcg}, step=step)
        ev = evaluate(inp.ctx, model, pop, run_id, "validation", _sha(inp))
        mlflow.log_metrics(
            {
                **_metrics(ev),
                "train_seconds": hist.seconds,
                "best_epoch": float(hist.best_epoch),
                "epochs_run": float(len(hist.losses)),
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            model.save(d)
            pop.save(d)  # the fallback used during evaluation
            _write(d / EVALUATION_FILE, ev)
            mlflow.log_artifacts(str(d))
        return {
            "run_id": run_id,
            "model_kind": "BPR",
            "config": asdict(cfg),
            "best_epoch": hist.best_epoch,
            "epochs_run": len(hist.losses),
            "train_seconds": hist.seconds,
            "evaluation": ev,
        }


def _sha(inp: Inputs) -> str:
    return inp.split_manifest["split_sha256"]


def _write(path: Path, obj: Any) -> None:
    path.write_text(
        json.dumps(obj, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )


def train(
    model_kind: str,
    split_config: str | Path,
    train_config: str | Path,
    grid: bool = False,
    log: Callable[[str], None] | None = None,
) -> list[dict[str, Any]]:
    from reviewpulse import tracking

    if model_kind not in MODEL_KINDS:
        raise DataContractError(f"--model must be one of {MODEL_KINDS}")
    if grid and model_kind != "BPR":
        raise DataContractError("--grid only applies to BPR")
    settings = load_train_settings(paths.resolve(train_config))
    keyer = UserKeyer.from_env()
    configs = bpr_configs(settings, grid) if model_kind == "BPR" else []
    inp = load_inputs(load_split_config(paths.resolve(split_config)), keyer)
    tracking.configure(settings.experiment)
    git = tracking.git_info()
    if model_kind == "Pop":
        return [run_pop(inp, settings, git)]
    return [run_bpr(inp, settings, git, cfg, log) for cfg in configs]
