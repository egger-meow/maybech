"""Validate the protective configuration echoed by OKX order details."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any


class ProtectionVerificationError(RuntimeError):
    """Raised when OKX does not confirm the requested attached protection."""


def verify_attached_protection(
    order: dict[str, Any],
    *,
    order_id: str,
    client_order_id: str,
    stop_loss: str,
    take_profit: str = "",
) -> dict[str, Any]:
    if str(order.get("ordId") or "") != order_id:
        raise ProtectionVerificationError("OKX order detail ID does not match submission")
    if str(order.get("clOrdId") or "") != client_order_id:
        raise ProtectionVerificationError("OKX order detail client ID does not match intent")
    state = str(order.get("state") or "").lower()
    if state not in {"live", "partially_filled", "filled"}:
        raise ProtectionVerificationError(f"OKX order is not active or filled: {state or 'missing'}")

    attachments = order.get("attachAlgoOrds")
    candidates = (
        [item for item in attachments if isinstance(item, dict)]
        if isinstance(attachments, list)
        else []
    )
    if not candidates and any(
        order.get(key) not in (None, "")
        for key in ("slTriggerPx", "slOrdPx", "tpTriggerPx", "tpOrdPx")
    ):
        candidates = [order]
    if not candidates:
        raise ProtectionVerificationError("OKX order detail has no attached protection")

    expected_sl = _decimal(stop_loss, field="requested stop loss")
    expected_tp = _decimal(take_profit, field="requested take profit") if take_profit else None
    for attachment in candidates:
        fail_code = str(attachment.get("failCode") or "")
        if fail_code not in {"", "0"}:
            continue
        try:
            sl_matches = (
                _decimal(attachment.get("slTriggerPx"), field="OKX stop loss")
                == expected_sl
                and str(attachment.get("slOrdPx") or "") == "-1"
            )
            tp_matches = expected_tp is None or (
                _decimal(attachment.get("tpTriggerPx"), field="OKX take profit")
                == expected_tp
                and str(attachment.get("tpOrdPx") or "") == "-1"
            )
        except ProtectionVerificationError:
            continue
        if sl_matches and tp_matches:
            return {
                "order_id": order_id,
                "state": state,
                "stop_loss": str(expected_sl),
                "take_profit": "" if expected_tp is None else str(expected_tp),
                "attach_algo_id": str(attachment.get("attachAlgoId") or ""),
            }
    raise ProtectionVerificationError(
        "OKX order detail does not match the requested stop loss/take profit"
    )


def _decimal(value: object, *, field: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ProtectionVerificationError(f"{field} is missing or invalid") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise ProtectionVerificationError(f"{field} must be positive")
    return parsed
