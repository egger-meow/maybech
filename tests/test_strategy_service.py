from types import SimpleNamespace

from src.strategies.base import Signal, TradeSetup
from src.trading.audit_event_store import AuditEventStore
from src.trading.trade_store import TradeStore
from src.daemon.strategy_service import StrategyService


class FakeExecutor:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def execute(self, pair, setup):
        self.calls.append((pair, setup))
        return self.result


class FailingAuditStore:
    def create(self, **kwargs):
        raise OSError("database unavailable")


def _setup() -> TradeSetup:
    return TradeSetup(
        signal=Signal.LONG,
        entry_price=2000.0,
        stop_loss=1900.0,
        take_profit=2200.0,
        reason="test setup",
    )


def _service(tmp_path, *, dry_run=True, audit_store=None):
    trade_store = TradeStore(str(tmp_path / "trades.db"))
    service = StrategyService(
        dry_run=dry_run,
        trade_store=trade_store,
        audit_store=audit_store,
    )
    service.strategy = SimpleNamespace(name="momentum_swap")
    return service


def test_strategy_service_persists_correlated_dry_run_lifecycle(tmp_path):
    service = _service(tmp_path)
    service.executor = FakeExecutor(
        {"ordId": "mock-order", "maybechRequestedSize": "1"}
    )

    signal = service._process_setup(
        pair="ETH-USDT-SWAP",
        setup=_setup(),
        btc_regime={"direction": "bullish", "strength": "strong", "price": 100000},
        observed_at="2026-06-27T12:00:00+08:00",
    )

    decisions = service.audit_store.list_strategy_decisions(strategy_id="momentum_swap")
    decision = decisions[0]
    lifecycle = service.audit_store.list(
        event_type="strategy.execution_result",
        correlation_id=decision.correlation_id,
    )
    trades = service.trade_store.get_open_trades()

    assert signal["result"] == "simulated"
    assert decision.payload["execution_status"] == "simulated"
    assert decision.payload["order_id"] == "mock-order"
    assert decision.payload["trade_id"] == trades[0].id
    assert decision.payload["position_id"] == trades[0].id
    assert service.position_store.get(trades[0].id).opened_quantity == 1.0
    assert len(lifecycle) == 1
    assert lifecycle[0].trade_id == trades[0].id


def test_strategy_service_persists_blocked_decision_without_execution(tmp_path):
    service = _service(tmp_path)
    executor = FakeExecutor({"ordId": "must-not-run"})
    service.executor = executor

    result = service._process_setup(
        pair="ETH-USDT-SWAP",
        setup=_setup(),
        btc_regime={"direction": "bearish", "strength": "strong"},
        observed_at="2026-06-27T12:00:00+08:00",
    )

    decisions = service.audit_store.list_strategy_decisions(strategy_id="momentum_swap")
    assert result is None
    assert executor.calls == []
    assert decisions[0].payload["execution_status"] == "blocked"
    assert service.trade_store.get_open_trades() == []


def test_live_strategy_fails_closed_when_pre_execution_audit_fails(tmp_path):
    service = _service(tmp_path, dry_run=False, audit_store=FailingAuditStore())
    executor = FakeExecutor({"ordId": "must-not-run"})
    service.executor = executor

    result = service._process_setup(
        pair="ETH-USDT-SWAP",
        setup=_setup(),
        btc_regime={"direction": "bullish", "strength": "strong"},
        observed_at="2026-06-27T12:00:00+08:00",
    )

    assert result is None
    assert executor.calls == []
    assert service.decisions_history[0]["execution_status"] == "audit_failed"
    assert service.decisions_history[0]["allowed"] is False


def test_live_submission_creates_pending_unit_without_assuming_fill(tmp_path):
    service = _service(tmp_path, dry_run=False)
    service.executor = FakeExecutor(
        {"data": [{"ordId": "live-order"}], "maybechRequestedSize": "2"}
    )

    signal = service._process_setup(
        pair="ETH-USDT-SWAP",
        setup=_setup(),
        btc_regime={"direction": "bullish", "strength": "strong"},
        observed_at="2026-06-27T12:00:00+08:00",
    )

    pending = service.trade_store.get_trade_history(status="pending_open")
    position = service.position_store.get(pending[0].id)
    assert signal["result"] == "submitted"
    assert position.status == "pending_open"
    assert position.opened_quantity == 0.0
    assert position.remaining_quantity == 0.0
    assert service.position_store.list_allocations(position.id) == []
