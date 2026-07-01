"""Convert operator-facing base quantity to OKX contract size without guessing."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

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
