"""Maybech runtime command-line interface."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from src.runtime import api_server, services


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Maybech runtime")
    subparsers = parser.add_subparsers(dest="mode", metavar="{api,services}")
    subparsers.add_parser(
        "api",
        help="Run daemon services behind the FastAPI/WebSocket API",
    )
    subparsers.add_parser(
        "services",
        help="Run daemon services without the API or dashboard",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {"-h", "--help"}:
        build_parser().parse_args(args)
        return

    mode, mode_args = args[0], args[1:]
    if mode == "api":
        api_server.main(mode_args)
        return
    if mode == "services":
        services.main(mode_args)
        return

    build_parser().parse_args(args)
