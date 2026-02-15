"""
OKX WebSocket subscription manager.

Manages real-time data streams: candles, tickers, positions, and account updates.
Reference: https://www.okx.com/docs-v5/zh/#trading-account-websocket-positions-channel
"""

from typing import Callable


class OKXWebSocket:
    """Manages WebSocket connections to OKX public & private channels."""

    def __init__(self) -> None:
        # TODO: initialise okx WebSocket connections
        self._callbacks: dict[str, list[Callable]] = {}

    def subscribe_candles(self, inst_id: str, bar: str, callback: Callable) -> None:
        """Subscribe to real-time candlestick updates."""
        raise NotImplementedError

    def subscribe_tickers(self, inst_id: str, callback: Callable) -> None:
        """Subscribe to real-time ticker updates."""
        raise NotImplementedError

    def subscribe_positions(self, callback: Callable) -> None:
        """Subscribe to private position updates (requires auth)."""
        raise NotImplementedError

    def subscribe_account(self, callback: Callable) -> None:
        """Subscribe to private account balance updates (requires auth)."""
        raise NotImplementedError

    async def start(self) -> None:
        """Start all WebSocket connections and begin receiving data."""
        raise NotImplementedError

    async def stop(self) -> None:
        """Gracefully close all WebSocket connections."""
        raise NotImplementedError
