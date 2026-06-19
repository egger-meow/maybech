from src.daemon.position_intent_service import PositionIntentService
from src.trading.position_intent import PositionIntentPolicy


def test_position_intent_policy_holds_supportive_long_position():
    policy = PositionIntentPolicy()

    intent = policy.evaluate(
        position={
            "inst_id": "ETH-USDT-SWAP",
            "pos_side": "long",
            "position": "1",
            "avg_price": "100",
            "mark_price": "106",
            "leverage": "3",
            "liqPx": "80",
        },
        btc_regime={"direction": "bullish", "strength": "strong", "impulse": "up"},
    )

    assert intent.action == "hold"
    assert intent.unrealised_pnl_pct is not None


def test_position_intent_policy_reduces_against_btc_regime():
    policy = PositionIntentPolicy()

    intent = policy.evaluate(
        position={
            "inst_id": "ETH-USDT-SWAP",
            "pos_side": "long",
            "position": "1",
            "avg_price": "100",
            "mark_price": "98",
            "leverage": "4",
            "liqPx": "70",
        },
        btc_regime={"direction": "bearish", "strength": "strong", "impulse": "none"},
    )

    assert intent.action == "reduce"
    assert intent.btc_direction == "bearish"


def test_position_intent_policy_closes_when_risk_is_elevated():
    policy = PositionIntentPolicy()

    intent = policy.evaluate(
        position={
            "inst_id": "ETH-USDT-SWAP",
            "pos_side": "short",
            "position": "1",
            "avg_price": "100",
            "mark_price": "103",
            "leverage": "12",
            "liqPx": "105",
        },
        btc_regime={"direction": "bullish", "strength": "strong", "impulse": "up"},
    )

    assert intent.action == "close"
    assert intent.leverage == 12.0


def test_position_intent_service_publishes_snapshot():
    service = PositionIntentService()

    class RuntimeStub:
        def __init__(self):
            self.values = {}
            self.published = []

        def get_value(self, key):
            return self.values.get(key)

        def set_value(self, key, value):
            self.values[key] = value

        @property
        def events(self):
            class Bus:
                def __init__(self, parent):
                    self.parent = parent

                def publish(self, event_type, source, payload=None):
                    self.parent.published.append((event_type, source, payload or {}))

            return Bus(self)

    runtime = RuntimeStub()
    runtime.values["account.snapshot"] = {
        "positions": [
            {
                "inst_id": "ETH-USDT-SWAP",
                "pos_side": "long",
                "position": "1",
                "avg_price": "100",
                "mark_price": "98",
                "leverage": "4",
                "liqPx": "70",
            }
        ]
    }
    runtime.values["market.btc_regime"] = {
        "direction": "bearish",
        "strength": "strong",
        "impulse": "none",
    }
    service.runtime = runtime

    service.tick()

    assert runtime.values["position.intents"][0]["action"] == "reduce"
    assert runtime.published[-1][0] == "position.intents"
