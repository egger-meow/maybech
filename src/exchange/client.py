"""
OKX REST API client wrapper.

Wraps the `python-okx` SDK so the rest of the codebase never imports OKX
directly — makes it easy to mock in tests and swap exchanges later.
"""

from src.config.settings import settings


class OKXClient:
    """Thin wrapper around the python-okx REST client."""

    def __init__(self) -> None:
        # TODO: initialise okx.Trade, okx.Account, okx.MarketData, okx.PublicData
        self.api_key = settings.OKX_API_KEY
        self.flag = settings.OKX_FLAG  # "0" live, "1" demo

    # -- Account ----------------------------------------------------------

    def get_balance(self) -> dict:
        """Fetch account balance across all currencies."""
        raise NotImplementedError

    def get_positions(self) -> list[dict]:
        """Fetch all open positions."""
        raise NotImplementedError

    # -- Market Data ------------------------------------------------------

    def get_candles(
        self,
        inst_id: str,
        bar: str = "15m",
        limit: int = 100,
    ) -> list[list]:
        """Fetch historical candlestick data.

        Returns list of [ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm].
        """
        raise NotImplementedError

    # -- Trading ----------------------------------------------------------

    def place_order(
        self,
        inst_id: str,
        side: str,
        size: str,
        order_type: str = "market",
        **kwargs,
    ) -> dict:
        """Place a new order. Returns order response dict."""
        raise NotImplementedError

    def cancel_order(self, inst_id: str, order_id: str) -> dict:
        """Cancel an existing order."""
        raise NotImplementedError
