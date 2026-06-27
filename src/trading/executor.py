"""Validated OKX order submission for strategy entries and position closes."""

from __future__ import annotations

import logging
from typing import Any

from src.exchange.client import OKXClient
from src.trading.instrument_constraints import InstrumentConstraints

logger = logging.getLogger(__name__)


class Executor:
    """Submit orders only after instrument and precision validation."""

    def __init__(
        self,
        client: OKXClient,
        dry_run: bool = True,
    ) -> None:
        self.client = client
        self.dry_run = dry_run
        self._constraints: dict[str, InstrumentConstraints] = {}
        if self.dry_run:
            logger.warning("Executor initialized in DRY-RUN mode. No real orders will be placed.")

    def execute(
        self,
        *,
        inst_id: str,
        position_side: str,
        entry_price: float,
        requested_size: str,
        stop_loss_price: float,
        client_order_id: str,
        take_profit_price: float | None = None,
    ) -> dict[str, Any]:
        """Place a validated limit entry for a persisted strategy intent."""
        normalized_side = position_side.lower()
        if normalized_side not in {"long", "short"}:
            raise ValueError("position_side must be 'long' or 'short'")

        try:
            entry_value = float(entry_price)
            stop_value = float(stop_loss_price)
            take_profit_value = (
                float(take_profit_price) if take_profit_price is not None else None
            )
            if normalized_side == "long" and stop_value >= entry_value:
                raise ValueError("long stop loss must be below entry price")
            if normalized_side == "short" and stop_value <= entry_value:
                raise ValueError("short stop loss must be above entry price")
            if take_profit_value is not None:
                if normalized_side == "long" and take_profit_value <= entry_value:
                    raise ValueError("long take profit must be above entry price")
                if normalized_side == "short" and take_profit_value >= entry_value:
                    raise ValueError("short take profit must be below entry price")
            if float(requested_size) <= 0:
                raise ValueError("requested size must be positive")
            if self.dry_run:
                return {
                    "ordId": "mock_ord_123",
                    "clOrdId": client_order_id,
                    "tag": "dry_run",
                    "maybechRequestedSize": requested_size,
                }
            constraints = self._instrument_constraints(inst_id)
            size = constraints.normalize_size(requested_size)
            normalized_price = constraints.normalize_price(entry_price)
            stop_loss = constraints.normalize_price(stop_loss_price)
            take_profit = (
                constraints.normalize_price(take_profit_price)
                if take_profit_price is not None
                else ""
            )
            side = "buy" if normalized_side == "long" else "sell"
            response = self.client.place_limit_order(
                inst_id=inst_id,
                side=side,
                sz=size,
                px=normalized_price,
                td_mode="cross",
                sl_trigger_px=stop_loss,
                sl_ord_px="-1",
                tp_trigger_px=take_profit,
                tp_ord_px="-1" if take_profit else "",
                client_order_id=client_order_id,
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
        client_order_id: str,
        pos_side: str = "",
    ) -> dict:
        """Submit a precision-validated reduce-only market close."""
        if quantity <= 0:
            raise ValueError("close quantity must be positive")
        if self.dry_run:
            return {
                "ordId": f"mock_close_{inst_id}",
                "clOrdId": client_order_id,
                "tag": "dry_run",
            }
        try:
            size = self._instrument_constraints(inst_id).normalize_size(quantity)
            return self.client.place_reduce_market_order(
                inst_id=inst_id,
                position_side=position_side,
                sz=size,
                pos_side=pos_side,
                client_order_id=client_order_id,
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
