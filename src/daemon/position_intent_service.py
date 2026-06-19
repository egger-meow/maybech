"""Daemon service that publishes structured position-management intents."""

from __future__ import annotations

from src.daemon.service import DaemonService
from src.trading.position_intent import PositionIntentPolicy
from src.utils.logger import setup_logger


logger = setup_logger(__name__)


class PositionIntentService(DaemonService):
    """Reads account state and emits safe position-management guidance."""

    name = "position_intent"
    interval = 15.0

    def __init__(self) -> None:
        super().__init__()
        self.policy = PositionIntentPolicy()
        self.latest_intents: list[dict] = []

    def setup(self) -> None:
        logger.info("PositionIntentService setup complete.")

    def tick(self) -> None:
        if self.runtime is None:
            raise RuntimeError("PositionIntentService requires runtime state")

        snapshot = self.runtime.get_value("account.snapshot") or {}
        btc_regime = self.runtime.get_value("market.btc_regime")
        positions = snapshot.get("positions", [])

        intents = [
            self.policy.evaluate(position=position, btc_regime=btc_regime).to_dict()
            for position in positions
        ]

        self.latest_intents = intents
        self.runtime.set_value("position.intents", intents)
        self.publish_event(
            "position.intents",
            {
                "count": len(intents),
                "actions": [intent["action"] for intent in intents],
                "intents": intents,
            },
        )

    def teardown(self) -> None:
        logger.info("PositionIntentService shutting down.")
