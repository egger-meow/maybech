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
    attach_client_order_id: str = "",
    expected_order_type: str = "",
    expected_filled_size: str = "",
) -> dict[str, Any]:
    if str(order.get("ordId") or "") != order_id:
        raise ProtectionVerificationError("OKX order detail ID does not match submission")
    if str(order.get("clOrdId") or "") != client_order_id:
        raise ProtectionVerificationError("OKX order detail client ID does not match intent")
    state = str(order.get("state") or "").lower()
    if state not in {"live", "partially_filled", "filled"}:
        raise ProtectionVerificationError(f"OKX order is not active or filled: {state or 'missing'}")
    if expected_order_type and str(order.get("ordType") or "").lower() != expected_order_type:
        raise ProtectionVerificationError("OKX order type does not match submission")
    if expected_filled_size:
        if state != "filled":
            raise ProtectionVerificationError("OKX FOK entry did not fill completely")
        if _decimal(order.get("accFillSz"), field="OKX accumulated fill size") != _decimal(
            expected_filled_size, field="requested fill size"
        ):
            raise ProtectionVerificationError("OKX FOK filled size does not match request")

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
            attachment_id_matches = not attach_client_order_id or (
                str(attachment.get("attachAlgoClOrdId") or "")
                == attach_client_order_id
            )
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
        if attachment_id_matches and sl_matches and tp_matches:
            return {
                "order_id": order_id,
                "state": state,
                "stop_loss": str(expected_sl),
                "take_profit": "" if expected_tp is None else str(expected_tp),
                "attach_algo_id": str(attachment.get("attachAlgoId") or ""),
                "attach_algo_client_id": str(
                    attachment.get("attachAlgoClOrdId") or ""
                ),
            }
    raise ProtectionVerificationError(
        "OKX order detail does not match the requested stop loss/take profit"
    )


def verify_active_attached_protection(
    orders: list[dict[str, Any]],
    *,
    inst_id: str,
    attach_client_order_id: str,
    quantity: str,
    stop_loss: str,
    take_profit: str = "",
) -> dict[str, Any]:
    matches = [
        order
        for order in orders
        if str(order.get("algoClOrdId") or "") == attach_client_order_id
    ]
    if len(matches) != 1:
        raise ProtectionVerificationError(
            "OKX does not report exactly one active attached protection order"
        )
    order = matches[0]
    checks = {
        "instrument": str(order.get("instId") or "") == inst_id,
        "state": str(order.get("state") or "").lower() == "live",
        "size": _decimal(order.get("sz"), field="OKX protection size")
        == _decimal(quantity, field="requested protection size"),
        "stop trigger": _decimal(order.get("slTriggerPx"), field="OKX active stop")
        == _decimal(stop_loss, field="requested active stop"),
        "stop order price": str(order.get("slOrdPx") or "") == "-1",
    }
    if take_profit:
        checks.update(
            {
                "take-profit trigger": _decimal(
                    order.get("tpTriggerPx"), field="OKX active take profit"
                )
                == _decimal(take_profit, field="requested active take profit"),
                "take-profit order price": str(order.get("tpOrdPx") or "") == "-1",
            }
        )
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise ProtectionVerificationError(
            "OKX active attached protection mismatch: " + ", ".join(failed)
        )
    algo_id = str(order.get("algoId") or "")
    if not algo_id:
        raise ProtectionVerificationError("OKX active protection is missing algoId")
    return {
        "algo_id": algo_id,
        "algo_client_order_id": attach_client_order_id,
        "state": "live",
        "quantity": quantity,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
    }


def _decimal(value: object, *, field: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ProtectionVerificationError(f"{field} is missing or invalid") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise ProtectionVerificationError(f"{field} must be positive")
    return parsed
