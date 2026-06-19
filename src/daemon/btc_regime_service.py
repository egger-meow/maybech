"""Daemon service that publishes the BTC-led market regime."""

from __future__ import annotations

from src.config.settings import settings
from src.daemon.service import DaemonService
from src.data.candles import CandleManager
from src.exchange.client import OKXClient
from src.market.btc_regime import BTCRegimeAnalyzer
from src.utils.logger import setup_logger


logger = setup_logger(__name__)


class BTCRegimeService(DaemonService):
    """Tracks BTC state for downstream position and strategy decisions."""

    name = "btc_regime"
    interval = 30.0

    def __init__(self, symbol: str = "BTC-USDT-SWAP") -> None:
        super().__init__()
        self.symbol = symbol
        self.client = None
        self.candle_manager = None
        self.analyzer = BTCRegimeAnalyzer()
        self.latest_regime = None

    def setup(self) -> None:
        self.client = OKXClient()
        self.candle_manager = CandleManager(self.client)
        logger.info("BTCRegimeService setup complete for %s", self.symbol)

    def tick(self) -> None:
        if self.candle_manager is None:
            raise RuntimeError("BTCRegimeService is not set up")

        df = self.candle_manager.fetch(
            self.symbol,
            settings.CANDLE_INTERVAL,
            limit=120,
        )
        regime = self.analyzer.analyze(df, symbol=self.symbol)
        payload = regime.to_dict()
        self.latest_regime = payload

        if self.runtime is not None:
            self.runtime.set_value("market.btc_regime", payload)
        self.publish_event("market.btc_regime", payload)

    def teardown(self) -> None:
        logger.info("BTCRegimeService shutting down.")
