"""Command line entry point: `reviewpulse <group> <command>`."""

from __future__ import annotations

import argparse
import json
import sys

from reviewpulse import paths


def _cmd_data_prepare(args: argparse.Namespace) -> int:
    from reviewpulse.data.contracts import load_source_config
    from reviewpulse.data.prepare import prepare

    cfg = load_source_config(paths.resolve(args.source))
    manifest = prepare(cfg)
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


def _cmd_data_split(args: argparse.Namespace) -> int:
    from reviewpulse.data.split import load_split_config, split

    cfg = load_split_config(paths.resolve(args.split))
    manifest = split(cfg)
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


def _cmd_train(args: argparse.Namespace) -> int:
    from reviewpulse.train import train

    results = train(
        args.model,
        split_config=args.split,
        train_config=args.train_config,
        grid=args.grid,
        log=lambda msg: print(msg, file=sys.stderr, flush=True),
    )
    rows = []
    for r in results:
        ev = r["evaluation"]
        rows.append(
            {
                "run_id": r["run_id"],
                "model_kind": r["model_kind"],
                "config": r.get("config"),
                "best_epoch": r.get("best_epoch"),
                "train_seconds": r.get("train_seconds"),
                "split_sha256": ev["split_sha256"],
                "metrics": ev["metrics"],
                "counts": ev["counts"],
            }
        )
    print(json.dumps(rows, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="reviewpulse")
    groups = parser.add_subparsers(dest="group", required=True)

    data = groups.add_parser("data", help="data preparation and split")
    data_cmds = data.add_subparsers(dest="command", required=True)
    p_prepare = data_cmds.add_parser("prepare", help="raw reviews -> normalized parquet")
    p_prepare.add_argument("--source", default="configs/source.yaml")
    p_prepare.set_defaults(handler=_cmd_data_prepare)
    p_split = data_cmds.add_parser("split", help="temporal split into train/valid/test")
    p_split.add_argument("--split", default="configs/split.yaml")
    p_split.set_defaults(handler=_cmd_data_split)

    p_train = groups.add_parser("train", help="train Pop or BPR and evaluate on validation")
    p_train.add_argument("--model", required=True, choices=["Pop", "BPR"])
    p_train.add_argument("--grid", action="store_true", help="BPR hyperparameter grid")
    p_train.add_argument("--split", default="configs/split.yaml")
    p_train.add_argument("--train-config", default="configs/train.yaml")
    p_train.set_defaults(handler=_cmd_train)
    return parser


def main(argv: list[str] | None = None) -> int:
    from reviewpulse.data.contracts import DataContractError
    from reviewpulse.evaluation.evaluator import EvaluationError
    from reviewpulse.evaluation.protocol import ProtocolError
    from reviewpulse.keys import UserKeyError
    from reviewpulse.models.bpr import BPRError

    parser = build_parser()
    args = parser.parse_args(argv)
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 2
    try:
        return int(handler(args) or 0)
    except (DataContractError, EvaluationError, ProtocolError, UserKeyError, BPRError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
