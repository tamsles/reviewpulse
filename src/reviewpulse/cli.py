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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="reviewpulse")
    groups = parser.add_subparsers(dest="group", required=True)

    data = groups.add_parser("data", help="data preparation and split")
    data_cmds = data.add_subparsers(dest="command", required=True)
    p_prepare = data_cmds.add_parser("prepare", help="raw reviews -> normalized parquet")
    p_prepare.add_argument("--source", default="configs/source.yaml")
    p_prepare.set_defaults(handler=_cmd_data_prepare)
    return parser


def main(argv: list[str] | None = None) -> int:
    from reviewpulse.data.contracts import DataContractError

    parser = build_parser()
    args = parser.parse_args(argv)
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 2
    try:
        return int(handler(args) or 0)
    except DataContractError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
