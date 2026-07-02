"""Convert operator-facing base quantity to OKX contract size without guessing."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR

from src.trading.instrument_metadata import InstrumentMetadata


def _positive(value: object, *, field: str) -> Decimal:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Invalid {field}: {value!r}") from exc
    if not number.is_finite() or number <= 0:
        raise ValueError(f"{field} must be positive")
    return number


def _render(value: Decimal) -> str:
    rendered = format(value, "f")
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered


@dataclass(frozen=True)
class SizeQuote:
    inst_id: str
    display_quantity: Decimal
    display_currency: str
    api_quantity_contracts: Decimal
    estimated_notional_usdt: Decimal
    entry_price: Decimal
    estimated_pnl_usdt: Decimal | None = None

    def to_dict(self) -> dict[str, str | None]:
        return {
            "inst_id": self.inst_id,
            "display_quantity": _render(self.display_quantity),
            "display_currency": self.display_currency,
            "api_quantity_contracts": _render(self.api_quantity_contracts),
            "estimated_notional_usdt": _render(self.estimated_notional_usdt),
            "entry_price": _render(self.entry_price),
            "estimated_pnl_usdt": (
                _render(self.estimated_pnl_usdt)
                if self.estimated_pnl_usdt is not None
                else None
            ),
        }


@dataclass(frozen=True)
class RiskSizeQuote:
    mode: str
    size: SizeQuote
    allowed_loss_usdt: Decimal
    stop_price: Decimal
    stop_distance_pct: Decimal
    estimated_loss_usdt: Decimal
    unused_risk_usdt: Decimal
    stop_expression: dict
    evidence: dict

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            **self.size.to_dict(),
            "allowed_loss_usdt": _render(self.allowed_loss_usdt),
            "stop_price": _render(self.stop_price),
            "stop_distance_pct": _render(self.stop_distance_pct),
            "estimated_loss_usdt": _render(self.estimated_loss_usdt),
            "unused_risk_usdt": _render(self.unused_risk_usdt),
            "stop_expression": self.stop_expression,
            "evidence": self.evidence,
        }


class InstrumentSizer:
    def __init__(self, metadata: InstrumentMetadata) -> None:
        if metadata.state != "live":
            raise ValueError(f"{metadata.inst_id} is not currently tradable")
        self.metadata = metadata
        self.contract_value = _positive(metadata.contract_value, field="ctVal")
        self.contract_multiplier = _positive(
            metadata.contract_multiplier or "1",
            field="ctMult",
        )
        self.lot_size = _positive(metadata.lot_size, field="lotSz")
        self.min_size = _positive(metadata.min_size, field="minSz")

    @property
    def base_currency(self) -> str:
        return self.metadata.base_ccy or self.metadata.inst_id.split("-")[0]

    def quote(
        self,
        *,
        display_quantity: object,
        entry_price: object,
        side: str,
        rule_price: object | None = None,
    ) -> SizeQuote:
        display = _positive(display_quantity, field="display_quantity")
        price = _positive(entry_price, field="entry_price")
        contract_unit = self.contract_value * self.contract_multiplier
        contract_ccy = self.metadata.contract_currency.upper()
        base_ccy = self.base_currency.upper()
        quote_currencies = {
            self.metadata.quote_ccy.upper(),
            self.metadata.settle_ccy.upper(),
            "USDT",
            "USDC",
            "USD",
        }
        if contract_ccy == base_ccy:
            contracts = display / contract_unit
            notional = display * price
        elif contract_ccy and contract_ccy in quote_currencies:
            notional = display * price
            contracts = notional / contract_unit
        else:
            raise ValueError(
                f"{self.metadata.inst_id} ctValCcy={contract_ccy or 'missing'} "
                "cannot be mapped safely to operator-facing base quantity"
            )
        if contracts < self.min_size:
            raise ValueError(
                f"derived OKX size {_render(contracts)} is below minSz "
                f"{_render(self.min_size)}"
            )
        if contracts % self.lot_size != 0:
            raise ValueError(
                f"derived OKX size {_render(contracts)} is not a multiple of "
                f"lotSz {_render(self.lot_size)}"
            )
        normalized_side = side.lower()
        if normalized_side not in {"long", "short"}:
            raise ValueError("side must be long or short")
        estimated_pnl = None
        if rule_price is not None:
            target = _positive(rule_price, field="rule_price")
            direction = Decimal("1") if normalized_side == "long" else Decimal("-1")
            estimated_pnl = (target - price) * display * direction
        return SizeQuote(
            inst_id=self.metadata.inst_id,
            display_quantity=display,
            display_currency=base_ccy,
            api_quantity_contracts=contracts,
            estimated_notional_usdt=notional,
            entry_price=price,
            estimated_pnl_usdt=estimated_pnl,
        )

    def quote_contracts(
        self,
        *,
        api_quantity_contracts: object,
        entry_price: object,
        side: str,
        rule_price: object | None = None,
    ) -> SizeQuote:
        contracts = _positive(api_quantity_contracts, field="api_quantity_contracts")
        price = _positive(entry_price, field="entry_price")
        if contracts < self.min_size or contracts % self.lot_size != 0:
            raise ValueError("OKX contract quantity is below minSz or not lotSz-aligned")
        contract_unit = self.contract_value * self.contract_multiplier
        contract_ccy = self.metadata.contract_currency.upper()
        base_ccy = self.base_currency.upper()
        quote_currencies = {
            self.metadata.quote_ccy.upper(),
            self.metadata.settle_ccy.upper(),
            "USDT",
            "USDC",
            "USD",
        }
        if contract_ccy == base_ccy:
            display = contracts * contract_unit
        elif contract_ccy and contract_ccy in quote_currencies:
            display = contracts * contract_unit / price
        else:
            raise ValueError(
                f"{self.metadata.inst_id} ctValCcy={contract_ccy or 'missing'} "
                "cannot be mapped safely to operator-facing base quantity"
            )
        return self.quote(
            display_quantity=display,
            entry_price=price,
            side=side,
            rule_price=rule_price,
        )

    def quote_risk(
        self,
        *,
        mode: str,
        entry_price: object,
        side: str,
        allowed_loss_usdt: object,
        position_notional_usdt: object | None = None,
        stop_price: object | None = None,
        timeframe: str | None = None,
        evidence: dict | None = None,
    ) -> RiskSizeQuote:
        """Derive a lot-aligned size/stop without exceeding allowed loss."""
        entry = _positive(entry_price, field="entry_price")
        allowed_loss = _positive(allowed_loss_usdt, field="allowed_loss_usdt")
        normalized_side = side.lower()
        if normalized_side not in {"long", "short"}:
            raise ValueError("side must be long or short")
        tick = _positive(self.metadata.tick_size, field="tickSz")

        if mode == "fixed_loss":
            if position_notional_usdt is None:
                raise ValueError("position_notional_usdt is required for fixed_loss mode")
            requested_notional = _positive(
                position_notional_usdt, field="position_notional_usdt"
            )
            if allowed_loss >= requested_notional:
                raise ValueError("allowed loss must be smaller than position notional")
            fraction = allowed_loss / requested_notional
            raw_stop = entry * (
                Decimal("1") - fraction
                if normalized_side == "long"
                else Decimal("1") + fraction
            )
            rounding = ROUND_CEILING if normalized_side == "long" else ROUND_FLOOR
            normalized_stop = (raw_stop / tick).to_integral_value(rounding=rounding) * tick
            target_notional = requested_notional
        elif mode == "chart_anchored":
            if stop_price is None:
                raise ValueError("stop_price is required for chart_anchored mode")
            raw_stop = _positive(stop_price, field="stop_price")
            if normalized_side == "long" and raw_stop >= entry:
                raise ValueError("long stop price must be below entry price")
            if normalized_side == "short" and raw_stop <= entry:
                raise ValueError("short stop price must be above entry price")
            rounding = ROUND_FLOOR if normalized_side == "long" else ROUND_CEILING
            normalized_stop = (raw_stop / tick).to_integral_value(rounding=rounding) * tick
            distance_fraction = abs(entry - normalized_stop) / entry
            target_notional = allowed_loss / distance_fraction
        else:
            raise ValueError("mode must be fixed_loss or chart_anchored")

        if normalized_stop <= 0:
            raise ValueError("derived stop price must be positive")
        distance_fraction = abs(entry - normalized_stop) / entry
        if distance_fraction <= 0:
            raise ValueError("stop price must differ from entry price")
        display = target_notional / entry
        contracts = self._contracts_for(display=display, notional=target_notional)
        contracts = (contracts / self.lot_size).to_integral_value(rounding=ROUND_FLOOR) * self.lot_size
        if contracts < self.min_size:
            raise ValueError(
                "allowed loss and stop distance derive a size below the instrument minimum"
            )
        size = self.quote_contracts(
            api_quantity_contracts=contracts,
            entry_price=entry,
            side=normalized_side,
            rule_price=normalized_stop,
        )
        estimated_loss = abs(size.estimated_pnl_usdt or Decimal("0"))
        if estimated_loss > allowed_loss:
            raise ValueError("lot normalization would exceed allowed loss")
        expression_type = "price_below" if normalized_side == "long" else "price_above"
        structured_evidence = {
            "source": "operator_fixed_loss" if mode == "fixed_loss" else "operator_chart_anchor",
            "timeframe": timeframe,
            "allowed_loss_usdt": _render(allowed_loss),
            "requested_stop_price": _render(raw_stop),
            **(evidence or {}),
        }
        return RiskSizeQuote(
            mode=mode,
            size=size,
            allowed_loss_usdt=allowed_loss,
            stop_price=normalized_stop,
            stop_distance_pct=distance_fraction,
            estimated_loss_usdt=estimated_loss,
            unused_risk_usdt=allowed_loss - estimated_loss,
            stop_expression={
                "type": expression_type,
                "symbol": "self",
                "value": float(normalized_stop),
            },
            evidence=structured_evidence,
        )

    def _contracts_for(self, *, display: Decimal, notional: Decimal) -> Decimal:
        contract_unit = self.contract_value * self.contract_multiplier
        contract_ccy = self.metadata.contract_currency.upper()
        if contract_ccy == self.base_currency.upper():
            return display / contract_unit
        if contract_ccy in {
            self.metadata.quote_ccy.upper(),
            self.metadata.settle_ccy.upper(),
            "USDT",
            "USDC",
            "USD",
        }:
            return notional / contract_unit
        raise ValueError(
            f"{self.metadata.inst_id} contract currency cannot be mapped safely"
        )
