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


def _cmd_package(args: argparse.Namespace) -> int:
    from reviewpulse.release.package import package

    manifest = package(args.run_id, split_config=args.split, allow_dirty=args.allow_dirty)
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


def _cmd_gate(args: argparse.Namespace) -> int:
    from reviewpulse.registry.gate import gate

    decision = gate(args.release_id)
    print(json.dumps(decision.to_dict(), indent=2, sort_keys=True))
    return 0 if decision.passed else 3


_EXIT = {"DRY_RUN": 0, "PROMOTED": 0, "ROLLED_BACK": 0, "REJECTED": 3, "CONFLICT": 4}


def _cmd_promote(args: argparse.Namespace) -> int:
    from reviewpulse.registry.champion import promote, rollback

    fn = promote if args.group == "promote" else rollback
    result = fn(args.release_id, args.expected_version, execute=args.execute, actor=args.actor)
    print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    return _EXIT[result.status]


def _cmd_champion_show(args: argparse.Namespace) -> int:
    from reviewpulse.registry.champion import history, show

    out = {"champion": show()}
    if args.history:
        out["history"] = history(args.history)
    print(json.dumps(out, indent=2, sort_keys=True))
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from reviewpulse.serving.app import create_app, load_runtime

    rt = load_runtime()
    host = args.host or rt["host"]
    port = args.port or rt["port"]
    uvicorn.run(create_app(background_load=True), host=host, port=port, workers=1)
    return 0


def _cmd_monitor_summary(args: argparse.Namespace) -> int:
    from reviewpulse.monitoring.events import read_events
    from reviewpulse.monitoring.summary import summarize
    from reviewpulse.serving.app import load_runtime

    min_events = args.min_events if args.min_events is not None else load_runtime()["min_events"]
    print(json.dumps(summarize(read_events(since=args.since), min_events), indent=2))
    return 0


def _cmd_freeze_test(args: argparse.Namespace) -> int:
    from reviewpulse.final_eval import freeze

    print(json.dumps(freeze(args.run_id, split_config=args.split), indent=2, sort_keys=True))
    return 0


def _cmd_evaluate_test(args: argparse.Namespace) -> int:
    from reviewpulse.final_eval import evaluate_test

    record = evaluate_test(args.run_id, confirm_final=args.confirm_final, split_config=args.split)
    print(json.dumps(record, indent=2, sort_keys=True))
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

    p_pkg = groups.add_parser("package", help="package an MLflow run into a release")
    p_pkg.add_argument("--run-id", required=True)
    p_pkg.add_argument("--split", default="configs/split.yaml")
    p_pkg.add_argument("--allow-dirty", action="store_true")
    p_pkg.set_defaults(handler=_cmd_package)

    p_gate = groups.add_parser("gate", help="run the quality gate for a release (read-only)")
    p_gate.add_argument("--release-id", required=True)
    p_gate.set_defaults(handler=_cmd_gate)

    for name in ("promote", "rollback"):
        p = groups.add_parser(name, help=f"{name} the champion pointer (dry-run by default)")
        p.add_argument("--release-id", required=True)
        p.add_argument("--expected-version", required=True, type=int)
        p.add_argument("--execute", action="store_true", help="actually move the pointer")
        p.add_argument("--actor", default="local-cli")
        p.set_defaults(handler=_cmd_promote)

    p_freeze = groups.add_parser("freeze-test", help="freeze configs and runs for the test eval")
    p_freeze.add_argument("--run-id", action="append", required=True)
    p_freeze.add_argument("--split", default="configs/split.yaml")
    p_freeze.set_defaults(handler=_cmd_freeze_test)

    p_test = groups.add_parser("evaluate-test", help="one-shot final test evaluation of a run")
    p_test.add_argument("--run-id", required=True)
    p_test.add_argument("--confirm-final", action="store_true")
    p_test.add_argument("--split", default="configs/split.yaml")
    p_test.set_defaults(handler=_cmd_evaluate_test)

    p_serve = groups.add_parser("serve", help="run the recommendation API (single worker)")
    p_serve.add_argument("--host", default=None)
    p_serve.add_argument("--port", type=int, default=None)
    p_serve.set_defaults(handler=_cmd_serve)

    mon = groups.add_parser("monitor", help="request monitoring")
    mon_cmds = mon.add_subparsers(dest="command", required=True)
    p_sum = mon_cmds.add_parser("summary", help="summarize request events")
    p_sum.add_argument("--since", default=None, help="UTC ISO time, e.g. 2026-11-23T00:00:00Z")
    p_sum.add_argument("--min-events", type=int, default=None)
    p_sum.set_defaults(handler=_cmd_monitor_summary)

    champ = groups.add_parser("champion", help="champion pointer")
    champ_cmds = champ.add_subparsers(dest="command", required=True)
    p_show = champ_cmds.add_parser("show", help="show the current champion")
    p_show.add_argument("--history", type=int, default=0, help="also show N log entries")
    p_show.set_defaults(handler=_cmd_champion_show)
    return parser


def main(argv: list[str] | None = None) -> int:
    from reviewpulse.data.contracts import DataContractError
    from reviewpulse.evaluation.evaluator import EvaluationError
    from reviewpulse.evaluation.protocol import ProtocolError
    from reviewpulse.final_eval import FinalEvalError
    from reviewpulse.keys import UserKeyError
    from reviewpulse.models.bpr import BPRError
    from reviewpulse.registry.champion import PromotionError
    from reviewpulse.registry.gate import QualityConfigError
    from reviewpulse.release.loader import BundleInvalid
    from reviewpulse.release.manifest import ReleaseIdError
    from reviewpulse.release.package import PackageError

    handled = (
        DataContractError,
        EvaluationError,
        ProtocolError,
        UserKeyError,
        BPRError,
        BundleInvalid,
        PackageError,
        QualityConfigError,
        ReleaseIdError,
        PromotionError,
        FinalEvalError,
    )

    parser = build_parser()
    args = parser.parse_args(argv)
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 2
    try:
        return int(handler(args) or 0)
    except handled as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
