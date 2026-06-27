from src.trading.signal_engine import SignalExpressionEngine


def test_signal_engine_validates_composite_expression():
    result = SignalExpressionEngine().validate(
        {
            "op": "and",
            "conditions": [
                {"type": "price_above", "symbol": "BTC-USDT-SWAP", "value": 65000},
                {"type": "rapid_drop", "symbol": "ETH-USDT-SWAP", "window_seconds": 300, "change_pct": 2},
            ],
        }
    )

    assert result.valid is True
    assert result.normalized["conditions"][0]["value"] == 65000.0


def test_signal_engine_reports_nested_validation_errors():
    result = SignalExpressionEngine().validate(
        {
            "op": "and",
            "conditions": [
                {"type": "price_above", "value": "bad"},
                {"type": "unknown_signal"},
            ],
        }
    )

    assert result.valid is False
    assert "$.conditions[0].symbol" in result.errors[0]
    assert any("unsupported signal type" in error for error in result.errors)


def test_signal_engine_evaluates_price_and_rapid_change_conditions():
    result = SignalExpressionEngine().evaluate(
        {
            "op": "and",
            "conditions": [
                {"type": "price_above", "symbol": "BTC-USDT-SWAP", "value": 65000},
                {"type": "rapid_drop", "symbol": "ETH-USDT-SWAP", "window_seconds": 300, "change_pct": 2},
            ],
        },
        context={
            "prices": {"BTC-USDT-SWAP": 66000},
            "changes_pct": {"ETH-USDT-SWAP:300": -2.5},
        },
    )

    assert result.valid is True
    assert result.matched is True
    assert result.evidence["conditions"][1]["change_pct"] == -2.5


def test_signal_engine_evaluation_reports_missing_market_context():
    result = SignalExpressionEngine().evaluate(
        {"type": "price_below", "symbol": "BTC-USDT-SWAP", "value": 60000},
        context={"prices": {}},
    )

    assert result.valid is True
    assert result.matched is False
    assert result.evidence["missing"] == "price"


def test_signal_engine_accepts_runtime_momentum_signal_shape():
    result = SignalExpressionEngine().validate(
        {
            "type": "volume_price_gap",
            "timeframe": "1m",
            "k_long": 10,
            "k_short": 5,
            "gap_threshold": 3,
        }
    )

    assert result.valid is True
