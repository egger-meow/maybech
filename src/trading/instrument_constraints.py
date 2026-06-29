"""Decimal-safe OKX instrument constraints for order submission."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP
from typing import Any


def _decimal(value: object, *, field: str) -> Decimal:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Invalid {field}: {value!r}") from exc
    if not number.is_finite() or number <= 0:
        raise ValueError(f"{field} must be positive")
    return number


def decimal_string(value: Decimal) -> str:
    rendered = format(value, "f")
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered


@dataclass(frozen=True)
class InstrumentConstraints:
    inst_id: str
    state: str
    minimum_size: Decimal
    lot_size: Decimal
    tick_size: Decimal
    contract_value: Decimal | None = None

    @classmethod
    def from_okx(cls, payload: dict[str, Any]) -> "InstrumentConstraints":
        inst_id = str(payload.get("instId") or "")
        if not inst_id:
            raise ValueError("OKX instrument metadata is missing instId")
        contract_value = payload.get("ctVal")
        return cls(
            inst_id=inst_id,
            state=str(payload.get("state") or ""),
            minimum_size=_decimal(payload.get("minSz"), field="minSz"),
            lot_size=_decimal(payload.get("lotSz"), field="lotSz"),
            tick_size=_decimal(payload.get("tickSz"), field="tickSz"),
            contract_value=(
                None
                if contract_value in (None, "")
                else _decimal(contract_value, field="ctVal")
            ),
        )

    def validate_tradable(self) -> None:
        if self.state != "live":
            raise ValueError(
                f"Instrument {self.inst_id} is not tradable (state={self.state or 'unknown'})"
            )

    def normalize_size(self, requested: object) -> str:
        self.validate_tradable()
        size = _decimal(requested, field="order size")
        if size < self.minimum_size:
            raise ValueError(
                f"Order size {decimal_string(size)} is below {self.inst_id} minSz "
                f"{decimal_string(self.minimum_size)}"
            )
        if size % self.lot_size != 0:
            raise ValueError(
                f"Order size {decimal_string(size)} is not a multiple of "
                f"{self.inst_id} lotSz {decimal_string(self.lot_size)}"
            )
        return decimal_string(size)

    def normalize_price(self, requested: object) -> str:
        self.validate_tradable()
        price = _decimal(requested, field="order price")
        ticks = (price / self.tick_size).to_integral_value(rounding=ROUND_HALF_UP)
        return decimal_string(ticks * self.tick_size)

    def normalize_entry_limit(self, requested: object, *, position_side: str) -> str:
        """Round toward the safe side of a maximum-slippage FOK boundary."""
        self.validate_tradable()
        price = _decimal(requested, field="entry limit price")
        side = position_side.lower()
        if side not in {"long", "short"}:
            raise ValueError("position_side must be 'long' or 'short'")
        rounding = ROUND_FLOOR if side == "long" else ROUND_CEILING
        ticks = (price / self.tick_size).to_integral_value(rounding=rounding)
        return decimal_string(ticks * self.tick_size)

    def normalize_break_even_price(
        self,
        requested: object,
        *,
        position_side: str,
    ) -> str:
        """Round without weakening the requested break-even protection."""
        self.validate_tradable()
        price = _decimal(requested, field="break-even price")
        side = position_side.lower()
        if side not in {"long", "short"}:
            raise ValueError("position_side must be 'long' or 'short'")
        rounding = ROUND_CEILING if side == "long" else ROUND_FLOOR
        ticks = (price / self.tick_size).to_integral_value(rounding=rounding)
        return decimal_string(ticks * self.tick_size)
