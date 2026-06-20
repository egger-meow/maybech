"""Run Maybech daemon services without a UI."""

from __future__ import annotations

import argparse

from src.daemon.runtime import create_default_runner
from src.utils.logger import setup_logger


logger = setup_logger("service_runner")


def main() -> None:
    parser = argparse.ArgumentParser(description="Maybech daemon service runner")
    parser.add_argument("--live", action="store_true", help="Disable dry-run for strategy")
    parser.add_argument(
        "--no-strategy",
        action="store_true",
        help="Run background services without strategy execution",
    )
    args = parser.parse_args()

    runner = create_default_runner(
        dry_run=not args.live,
        include_strategy=not args.no_strategy,
    )
    logger.info("Running Maybech services without a UI.")
    runner.run_forever()


if __name__ == "__main__":
    main()
