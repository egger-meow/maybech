"""Run Maybech daemon services behind a localhost HTTP/WebSocket API."""

from __future__ import annotations

import argparse
import ipaddress
import logging
from collections.abc import Sequence
from threading import Thread

import uvicorn

from src.api.app import create_app
from src.config.settings import settings
from src.daemon.runtime import create_default_runner
from src.daemon.service import DaemonRunner
from src.utils.logger import setup_logger


logger = setup_logger("api_server")
logging.getLogger("src.exchange.client").setLevel(logging.WARNING)
logging.getLogger("src.data.candles").setLevel(logging.WARNING)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Maybech Runtime API")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host")
    parser.add_argument("--port", type=int, default=8000, help="Bind port")
    parser.add_argument(
        "--allow-remote",
        action="store_true",
        help="Allow non-loopback binding when MAYBECH_API_TOKEN is configured",
    )
    parser.add_argument("--live", action="store_true", help="Disable dry-run for strategy")
    parser.add_argument(
        "--role",
        choices=("combined", "replica"),
        default="combined",
        help="Run the execution leader or a read-only API replica",
    )
    parser.add_argument(
        "--no-strategy",
        action="store_true",
        help="Run monitoring and position services without strategy execution",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)

    host = args.host.strip().strip("[]")
    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = host.lower() == "localhost"
    if not loopback and not args.allow_remote:
        raise SystemExit("non-loopback API binding requires --allow-remote")
    if not loopback and not settings.MAYBECH_API_TOKEN:
        raise SystemExit("non-loopback API binding requires MAYBECH_API_TOKEN")

    if args.role == "replica" and args.live:
        raise SystemExit("--live is not allowed for a read-only API replica")
    if args.role == "replica" and args.no_strategy:
        raise SystemExit("--no-strategy only applies to the combined runtime")

    if args.role == "combined":
        runner = create_default_runner(
            dry_run=not args.live,
            include_strategy=not args.no_strategy,
        )
        daemon_thread = Thread(target=runner.run_forever, daemon=True)
        daemon_thread.start()
    else:
        runner = DaemonRunner()

    logger.info(
        "Maybech %s API listening on http://%s:%s",
        args.role,
        args.host,
        args.port,
    )
    uvicorn.run(
        create_app(
            runner,
            runtime_role=args.role,
            api_token=settings.MAYBECH_API_TOKEN,
        ),
        host=args.host,
        port=args.port,
    )
