import threading

import pandas as pd

from src.daemon.service import DaemonRunner
from src.daemon.strategy_service import StrategyService
from src.trading.audit_event_store import AuditEventStore
from src.trading.account_risk import AccountRiskStore
from src.trading.entry_control import EntryControlManager
from src.trading.signal_engine import SignalEvaluationResult
from src.trading.strategy_store import StrategyStore
from src.trading.trade_store import TradeStore


class FakeExecutor:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def approve_entry(self, **kwargs):
        return FakeRiskApproval(kwargs)

    def execute(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


class FakeRiskApproval:
    def __init__(self, payload):
        self.payload = payload

    def to_dict(self):
        return self.payload


class BlockingRiskExecutor(FakeExecutor):
    def approve_entry(self, **kwargs):
        raise RuntimeError("projected exposure exceeds limit")


class BlockingSubmissionExecutor(FakeExecutor):
    def __init__(self):
        super().__init__({"ordId": "race-order", "maybechRequestedSize": "1"})
        self.entered = threading.Event()
        self.release = threading.Event()

    def execute(self, **kwargs):
        self.entered.set()
        self.release.wait(timeout=2)
        return super().execute(**kwargs)


class FakeCandleManager:
    def fetch(self, inst_id, bar, limit):
        return pd.DataFrame(
            {
                "timestamp": pd.to_datetime(["2026-06-27T00:00:00Z", "2026-06-27T00:01:00Z"]),
                "open": [100, 110],
                "high": [101, 111],
                "low": [99, 109],
                "close": [100, 110],
                "volume": [10, 20],
            }
        )


class FailingAuditStore:
    def create(self, **kwargs):
        raise OSError("database unavailable")


def _strategy(store: StrategyStore, *, enabled=True):
    return store.create(
        id="breakout-long",
        name="Breakout Long",
        kind="signal",
        enabled=enabled,
        target_instruments=["ETH-USDT-SWAP"],
        entry_signal={"type": "price_above", "symbol": "self", "value": 105},
        default_rules={
            "close_conditions": [
                {
                    "purpose": "stop_loss",
                    "expression": {"type": "price_below", "symbol": "self", "value": 95},
                    "enabled": True,
                }
            ]
        },
        metadata={
            "position_side": "long",
            "candle_bar": "1m",
            "order_size_contracts": {"ETH-USDT-SWAP": "1"},
        },
    )


def _service(tmp_path, *, dry_run=True, audit_store=None):
    trade_store = TradeStore(str(tmp_path / "trades.db"))
    strategy_store = StrategyStore(trade_store.db_path)
    strategy = _strategy(strategy_store)
    service = StrategyService(
        dry_run=dry_run,
        trade_store=trade_store,
        audit_store=audit_store,
        strategy_store=strategy_store,
    )
    if not dry_run:
        runner = DaemonRunner()
        runner.register(service)
        runner.runtime.set_value(
            "execution.fills.status",
            {"caught_up": True, "websocket_connected": True},
        )
    return service, strategy


def _evaluation():
    return SignalEvaluationResult(
        matched=True,
        valid=True,
        evidence={"type": "price_above", "price": 110, "threshold": 105},
    )


def _process(service, strategy, *, btc_regime=None):
    return service._process_match(
        strategy=strategy,
        pair="ETH-USDT-SWAP",
        side="long",
        entry_price=110,
        requested_size="1",
        evaluation=_evaluation(),
        btc_regime=btc_regime or {
            "direction": "bullish",
            "strength": "normal",
            "impulse": "up",
            "price": 100000,
        },
        observed_at="2026-06-27T12:00:00+08:00",
    )


def test_strategy_service_persists_generic_dry_run_and_default_close_conditions(tmp_path):
    service, strategy = _service(tmp_path)
    service.executor = FakeExecutor({"ordId": "mock-order", "maybechRequestedSize": "1"})

    signal = _process(service, strategy)

    decisions = service.audit_store.list_strategy_decisions(strategy_id=strategy.id)
    trades = service.trade_store.get_open_trades()
    position = service.position_store.get(trades[0].id)
    conditions = service.position_store.list_close_conditions(position.id)
    assert signal["result"] == "simulated"
    assert decisions[0].payload["execution_status"] == "simulated"
    assert position.opened_quantity == 1.0
    assert conditions[0].purpose == "stop_loss"
    assert conditions[0].expression["symbol"] == "ETH-USDT-SWAP"
    assert conditions[0].metadata["source_strategy_id"] == strategy.id


def test_strategy_service_persists_blocked_decision_without_execution(tmp_path):
    service, strategy = _service(tmp_path)
    executor = FakeExecutor({"ordId": "must-not-run"})
    service.executor = executor

    result = _process(
        service,
        strategy,
        btc_regime={"direction": "bearish", "strength": "strong", "impulse": "down"},
    )

    assert result is None
    assert executor.calls == []
    assert service.audit_store.list_strategy_decisions(strategy_id=strategy.id)[0].payload[
        "execution_status"
    ] == "blocked"


def test_live_strategy_fails_closed_when_pre_execution_audit_fails(tmp_path):
    service, strategy = _service(tmp_path, dry_run=False, audit_store=FailingAuditStore())
    executor = FakeExecutor({"ordId": "must-not-run"})
    service.executor = executor

    result = _process(service, strategy)

    assert result is None
    assert executor.calls == []
    assert service.decisions_history[0]["execution_status"] == "audit_failed"


def test_live_strategy_blocks_before_persisting_position_when_risk_fails(tmp_path):
    service, strategy = _service(tmp_path, dry_run=False)
    executor = BlockingRiskExecutor({"ordId": "must-not-run"})
    service.executor = executor

    result = _process(service, strategy)

    assert result is None
    assert executor.calls == []
    assert service.trade_store.get_trade_history(status="pending_open") == []
    decision = service.audit_store.list_strategy_decisions(strategy_id=strategy.id)[0]
    assert decision.payload["execution_status"] == "risk_blocked"
    assert "projected exposure exceeds limit" in decision.payload["reason"]


def test_live_submission_creates_pending_unit_without_assuming_fill(tmp_path):
    service, strategy = _service(tmp_path, dry_run=False)
    service.executor = FakeExecutor({"ordId": "live-order", "maybechRequestedSize": "2"})

    signal = _process(service, strategy)

    pending = service.trade_store.get_trade_history(status="pending_open")
    position = service.position_store.get(pending[0].id)
    assert signal["result"] == "submitted"
    assert position.status == "pending_open"
    assert position.opened_quantity == 0.0
    assert position.client_order_id == service.executor.calls[0]["client_order_id"]
    assert position.exchange_order_id == "live-order"
    assert service.position_store.list_allocations(position.id) == []
    assert len(service.position_store.list_close_conditions(position.id)) == 1


def test_strategy_service_executes_one_persisted_false_to_true_edge(tmp_path):
    service, strategy = _service(tmp_path)
    executor = FakeExecutor({"ordId": "mock-order", "maybechRequestedSize": "1"})
    service.executor = executor
    service.candle_manager = FakeCandleManager()
    runner = DaemonRunner()
    runner.register(service)
    runner.runtime.set_value(
        "market.btc_regime",
        {"direction": "bullish", "strength": "normal", "impulse": "up"},
    )

    service.tick()
    service.tick()

    assert len(executor.calls) == 1
    assert executor.calls[0]["position_side"] == "long"
    assert executor.calls[0]["stop_loss_price"] == 95
    assert len(service.trade_store.get_open_trades()) == 1


def test_live_strategy_does_not_consume_signal_edge_before_ingestion_is_ready(tmp_path):
    service, strategy = _service(tmp_path, dry_run=False)
    executor = FakeExecutor({"ordId": "must-wait"})
    service.executor = executor
    service.candle_manager = FakeCandleManager()
    service.runtime.set_value(
        "execution.fills.status",
        {"caught_up": False, "websocket_connected": True},
    )
    service.runtime.set_value(
        "market.btc_regime",
        {"direction": "bullish", "strength": "normal", "impulse": "up"},
    )

    service.tick()
    service.runtime.set_value(
        "execution.fills.status",
        {"caught_up": True, "websocket_connected": True},
    )
    service.tick()

    assert len(executor.calls) == 1
    assert service.strategy_store.record_evaluation(
        strategy.id,
        "ETH-USDT-SWAP",
        matched=True,
    ) is False


def test_entry_kill_waits_for_submission_link_then_cancels_it(tmp_path):
    service, strategy = _service(tmp_path, dry_run=False)
    executor = BlockingSubmissionExecutor()
    service.executor = executor

    class CancelClient:
        def __init__(self):
            self.calls = []

        def cancel_order(self, inst_id, order_id):
            self.calls.append((inst_id, order_id))
            return {"ordId": order_id, "sCode": "0"}

    cancel_client = CancelClient()
    manager = EntryControlManager(
        client=cancel_client,
        risk_store=AccountRiskStore(service.trade_store.db_path),
        position_store=service.position_store,
        audit_store=service.audit_store,
    )
    strategy_thread = threading.Thread(target=lambda: _process(service, strategy))
    kill_result = []
    kill_thread = threading.Thread(target=lambda: kill_result.append(manager.kill_entries()))

    strategy_thread.start()
    assert executor.entered.wait(timeout=1)
    kill_thread.start()
    kill_thread.join(timeout=0.05)
    assert kill_thread.is_alive()
    executor.release.set()
    strategy_thread.join(timeout=2)
    kill_thread.join(timeout=2)

    assert cancel_client.calls == [("ETH-USDT-SWAP", "race-order")]
    assert kill_result[0].cancellations_requested == 1
