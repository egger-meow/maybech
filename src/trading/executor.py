"""Validated OKX order submission for strategy entries and position closes."""

from __future__ import annotations

import logging
from typing import Any

from src.exchange.client import OKXClient
from src.strategies.base import Signal, TradeSetup
from src.trading.instrument_constraints import InstrumentConstraints

logger = logging.getLogger(__name__)


class Executor:
    """Submit orders only after instrument and precision validation."""

    def __init__(
        self,
        client: OKXClient,
        dry_run: bool = True,
        *,
        order_sizes: dict[str, str] | None = None,
    ) -> None:
        self.client = client
        self.dry_run = dry_run
        self.order_sizes = dict(order_sizes or {})
        self._constraints: dict[str, InstrumentConstraints] = {}
        if self.dry_run:
            logger.warning("Executor initialized in DRY-RUN mode. No real orders will be placed.")

    def configure_order_sizes(self, order_sizes: dict[str, str]) -> None:
        self.order_sizes = dict(order_sizes)

    def execute(self, inst_id: str, setup: TradeSetup) -> dict[str, Any]:
        """Place a validated limit entry with attached stop and take profit."""
        requested_size = self.order_sizes.get(inst_id)
        if self.dry_run:
            size = requested_size or "1"
            return {
                "ordId": "mock_ord_123",
                "tag": "dry_run",
                "maybechRequestedSize": size,
            }
        if requested_size is None:
            logger.error(
                "Live entry blocked: strategy has no order_size_contracts value for %s",
                inst_id,
            )
            return {}

        try:
            constraints = self._instrument_constraints(inst_id)
            size = constraints.normalize_size(requested_size)
            entry_price = constraints.normalize_price(setup.entry_price)
            stop_loss = constraints.normalize_price(setup.stop_loss)
            take_profit = constraints.normalize_price(setup.take_profit)
            side = "buy" if setup.signal == Signal.LONG else "sell"
            response = self.client.place_limit_order(
                inst_id=inst_id,
                side=side,
                sz=size,
                px=entry_price,
                td_mode="cross",
                sl_trigger_px=stop_loss,
                sl_ord_px="-1",
                tp_trigger_px=take_profit,
                tp_ord_px="-1",
                confirm=True,
            )
            if not response:
                logger.error("Order placement failed for %s (empty response)", inst_id)
                return {}
            return {**response, "maybechRequestedSize": size}
        except Exception as exc:
            logger.error("Entry submission blocked for %s: %s", inst_id, exc)
            return {}

    def check_exits(self) -> list[dict]:
        return []

    def close_position(
        self,
        *,
        inst_id: str,
        position_side: str,
        quantity: float,
        pos_side: str = "",
    ) -> dict:
        """Submit a precision-validated reduce-only market close."""
        if quantity <= 0:
            raise ValueError("close quantity must be positive")
        if self.dry_run:
            return {"ordId": f"mock_close_{inst_id}", "tag": "dry_run"}
        try:
            size = self._instrument_constraints(inst_id).normalize_size(quantity)
            return self.client.place_reduce_market_order(
                inst_id=inst_id,
                position_side=position_side,
                sz=size,
                pos_side=pos_side,
                confirm=True,
            )
        except Exception as exc:
            logger.error("Close submission blocked for %s: %s", inst_id, exc)
            return {}

    def _instrument_constraints(self, inst_id: str) -> InstrumentConstraints:
        cached = self._constraints.get(inst_id)
        if cached is not None:
            return cached
        payloads = self.client.get_instruments(inst_type="SWAP", inst_id=inst_id)
        if len(payloads) != 1:
            raise ValueError(f"Expected one OKX instrument for {inst_id}, got {len(payloads)}")
        constraints = InstrumentConstraints.from_okx(payloads[0])
        if constraints.inst_id != inst_id:
            raise ValueError(
                f"OKX instrument mismatch: requested {inst_id}, got {constraints.inst_id}"
            )
        constraints.validate_tradable()
        self._constraints[inst_id] = constraints
        return constraints
