"""Run Maybech daemon services behind a localhost HTTP/WebSocket API."""

from __future__ import annotations

import argparse
import logging
from threading import Thread

import uvicorn

from src.api.app import create_app
from src.daemon.runtime import create_default_runner
from src.utils.logger import setup_logger


logger = setup_logger("api_server")
logging.getLogger("src.exchange.client").setLevel(logging.WARNING)
logging.getLogger("src.data.candles").setLevel(logging.WARNING)


def main() -> None:
    parser = argparse.ArgumentParser(description="Maybech Runtime API")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host")
    parser.add_argument("--port", type=int, default=8000, help="Bind port")
    parser.add_argument("--live", action="store_true", help="Disable dry-run for strategy")
    parser.add_argument(
        "--no-strategy",
        action="store_true",
        help="Run API with notificator only",
    )
    args = parser.parse_args()

    runner = create_default_runner(
        dry_run=not args.live,
        include_strategy=not args.no_strategy,
    )
    daemon_thread = Thread(target=runner.run_forever, daemon=True)
    daemon_thread.start()

    logger.info("Maybech API listening on http://%s:%s", args.host, args.port)
    uvicorn.run(create_app(runner), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
