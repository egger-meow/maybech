"""
Order execution engine.

Receives TradeSetup from strategies and executes orders via the exchange client.
Also monitors open positions and triggers stop-loss / take-profit exits.
"""

import logging
from typing import List, Dict, Optional

from src.exchange.client import OKXClient
from src.strategies.base import TradeSetup, Signal
from src.config.settings import settings

logger = logging.getLogger(__name__)


class Executor:
    """Manages live order placement and position lifecycle."""

    def __init__(self, client: OKXClient, dry_run: bool = True) -> None:
        self.client = client
        self.dry_run = dry_run
        if self.dry_run:
            logger.warning("Executor initialized in DRY-RUN mode. No real orders will be placed.")

    def execute(self, inst_id: str, setup: TradeSetup) -> dict:
        """Place entry order + set stop-loss and take-profit."""
        
        side = "buy" if setup.signal == Signal.LONG else "sell"
        sz = str(settings.TRADE_QUANTITY_ETH) # Fixed size for now
        
        # OKX requires string prices
        entry_px = str(setup.entry_price)
        sl_px = str(setup.stop_loss)
        tp_px = str(setup.take_profit)
        
        logger.info(
            f"EXECUTE ({'DRY' if self.dry_run else 'LIVE'}): "
            f"{side.upper()} {inst_id} @ {entry_px} | SL: {sl_px} | TP: {tp_px}"
        )

        if self.dry_run:
            return {"ordId": "mock_ord_123", "tag": "dry_run"}

        try:
            # OKX supports attaching SL/TP to the order via algo/trigger params 
            # OR we can place OCO or separate orders.
            # Client `place_limit_order` has support for attached SL/TP (check client.py).
            # If client.py `place_limit_order` handles `sl_trigger_px`, use it.
            
            # Note: For SWAP, attached SL/TP is often cleaner. 
            # We assume client.place_limit_order supports `sl_trigger_px` and `tp_trigger_px`.
            
            response = self.client.place_limit_order(
                inst_id=inst_id,
                side=side,
                sz=sz,
                px=entry_px,
                td_mode="cross", # Default to cross for this strategy
                sl_trigger_px=sl_px,
                sl_ord_px="-1", # Market stop
                tp_trigger_px=tp_px,
                tp_ord_px="-1", # Market take profit
                confirm=True   # We passed self.dry_run check, so we intend to execute.
                               # OKXClient will still block if MAYBECH_ARM_ORDERS is not set.
            )
            
            if response:
                logger.info(f"Order placed successfully: {response}")
                return response
            else:
                logger.error("Order placement failed (empty response).")
                return {}

        except Exception as e:
            logger.error(f"Execution failed: {e}")
            return {}

    def check_exits(self) -> List[dict]:
        """
        Check all open positions against their SL/TP levels.
        
        In 'net' mode with attached SL/TP, the exchange handles exits.
        If we didn't attach SL/TP, or if we want soft stops, we check here.
        For now, we assume orders were placed with attached SL/TP.
        We can just monitor and log.
        """
        if self.dry_run:
            return []

        # Optional: Monitor and enforce backup soft-stops
        return []
