"""Normalize OKX fill payloads into exchange-independent confirmed fills."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.trading.execution_allocation import ConfirmedExecutionFill


def normalize_okx_fill(payload: dict[str, Any]) -> ConfirmedExecutionFill:
    fill_id = str(payload.get("tradeId") or payload.get("fillId") or "")
    order_id = str(payload.get("ordId") or "")
    client_order_id = str(payload.get("clOrdId") or "")
    if not fill_id:
        raise ValueError("OKX fill is missing tradeId")
    if not order_id:
        raise ValueError(f"OKX fill {fill_id!r} is missing ordId")

    quantity = _positive_float(payload.get("fillSz"), field="fillSz")
    price = _positive_float(payload.get("fillPx"), field="fillPx")
    fee = _optional_float(
        (
            payload.get("fee")
            if payload.get("fee") not in (None, "")
            else payload.get("fillFee")
        ),
        field="fee",
    )
    occurred_at = _timestamp_ms(payload.get("fillTime") or payload.get("ts"))
    return ConfirmedExecutionFill(
        fill_id=fill_id,
        exchange_order_id=order_id,
        client_order_id=client_order_id,
        quantity=quantity,
        price=price,
        fee=fee,
        confirmation_source="okx_fill",
        occurred_at=occurred_at,
        reason="confirmed OKX execution fill",
        metadata={
            "inst_id": str(payload.get("instId") or ""),
            "side": str(payload.get("side") or ""),
            "position_side": str(payload.get("posSide") or ""),
            "client_order_id": client_order_id,
            "fee_currency": str(
                payload.get("feeCcy") or payload.get("fillFeeCcy") or ""
            ),
            "execution_type": str(payload.get("execType") or ""),
            "exchange_realized_pnl": _optional_float(
                payload.get("fillPnl"), field="fillPnl"
            ),
            "exchange_realized_pnl_currency": str(
                payload.get("fillPnlCcy") or payload.get("settleCcy") or ""
            ),
        },
    )


def _positive_float(value: object, *, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"OKX fill has invalid {field}") from exc
    if number <= 0:
        raise ValueError(f"OKX fill {field} must be positive")
    return number


def _optional_float(value: object, *, field: str) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"OKX fill has invalid {field}") from exc


def _timestamp_ms(value: object) -> str:
    if value in (None, ""):
        return datetime.now(timezone.utc).isoformat()
    try:
        return datetime.fromtimestamp(
            int(str(value)) / 1000,
            tz=timezone.utc,
        ).isoformat()
    except (TypeError, ValueError, OSError) as exc:
        raise ValueError("OKX fill has invalid ts/fillTime") from exc
