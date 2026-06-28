import pytest

from src.trading.order_protection import (
    ProtectionVerificationError,
    verify_attached_protection,
)


def test_verifier_accepts_matching_attached_market_stop_and_take_profit():
    result = verify_attached_protection(
        {
            "ordId": "order-a",
            "clOrdId": "client-a",
            "state": "live",
            "attachAlgoOrds": [
                {
                    "slTriggerPx": "1900.00",
                    "slOrdPx": "-1",
                    "tpTriggerPx": "2200",
                    "tpOrdPx": "-1",
                    "failCode": "",
                }
            ],
        },
        order_id="order-a",
        client_order_id="client-a",
        stop_loss="1900",
        take_profit="2200.0",
    )

    assert result["stop_loss"] == "1900"
    assert result["take_profit"] == "2200.0"


def test_verifier_rejects_attachment_fail_code_or_wrong_price():
    order = {
        "ordId": "order-a",
        "clOrdId": "client-a",
        "state": "live",
        "attachAlgoOrds": [
            {
                "slTriggerPx": "1950",
                "slOrdPx": "-1",
                "failCode": "51020",
            }
        ],
    }

    with pytest.raises(ProtectionVerificationError, match="does not match"):
        verify_attached_protection(
            order,
            order_id="order-a",
            client_order_id="client-a",
            stop_loss="1900",
        )
