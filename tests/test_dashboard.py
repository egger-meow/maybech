from src.monitor.dashboard import Dashboard


class BalanceClient:
    def __init__(self, payload):
        self.payload = payload

    def get_balance(self):
        return self.payload


def test_account_summary_uses_account_level_usd_valuation_when_available():
    summary = Dashboard(BalanceClient([{
        "totalEq": "1050.25",
        "availEq": "800.5",
        "upl": "-12.75",
        "details": [{
            "ccy": "USDT", "eq": "1051", "eqUsd": "1050.25",
            "availBal": "800.5", "availEq": "800.5", "upl": "-12.75",
        }],
    }])).get_account_summary()

    assert summary["total_equity"] == "1050.25"
    assert summary["total_equity_currency"] == "USD"
    assert summary["available_equity"] == "800.5"
    assert summary["available_equity_status"] == "account_valued"
    assert summary["unrealized_pnl"] == "-12.75"
    assert summary["unrealized_pnl_currency"] == "USD"


def test_account_summary_keeps_multi_currency_native_values_separate():
    summary = Dashboard(BalanceClient([{
        "totalEq": "2500",
        "availEq": "",
        "upl": "",
        "details": [
            {"ccy": "EUR", "eq": "500", "eqUsd": "540", "availBal": "480", "upl": "3"},
            {"ccy": "USDT", "eq": "1960", "eqUsd": "1960", "availBal": "1800", "upl": "-2"},
        ],
    }])).get_account_summary()

    assert summary["available_equity"] is None
    assert summary["available_equity_status"] == "per_currency_only"
    assert summary["unrealized_pnl"] is None
    assert summary["unrealized_pnl_status"] == "per_currency_only"
    assert summary["currencies"][0]["available_balance"] == "480"
    assert summary["currencies"][0]["native_currency"] == "EUR"
    assert summary["currencies"][1]["unrealized_pnl"] == "-2"


def test_account_summary_preserves_zero_and_marks_missing_values_unavailable():
    zero = Dashboard(BalanceClient([{
        "totalEq": "0", "availEq": "0", "upl": "0", "details": [],
    }])).get_account_summary()
    unavailable = Dashboard(BalanceClient([{
        "totalEq": "", "availEq": "", "upl": "", "details": [],
    }])).get_account_summary()

    assert zero["available_equity"] == "0"
    assert zero["unrealized_pnl"] == "0"
    assert unavailable["total_equity"] is None
    assert unavailable["available_equity_status"] == "unavailable"
    assert unavailable["unrealized_pnl_status"] == "unavailable"
