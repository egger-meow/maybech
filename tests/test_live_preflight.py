from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

import src.exchange.client as client_module
import src.daemon.runtime as runtime_module
from src.api.app import create_app
from src.daemon.service import DaemonRunner
from src.exchange.client import arm_order_placement, disarm_order_placement
from src.runtime.live_preflight import LivePreflightError, run_live_preflight
from src.trading.account_risk import AccountRiskLimits, AccountRiskStore
from src.trading.logical_position_store import (
    LogicalPositionProtection,
    LogicalPositionRecord,
    LogicalPositionStore,
)
from src.trading.strategy_store import StrategyStore
from src.trading.trade_store import TradeStore


class FakePreflightClient:
    def __init__(
        self,
        *,
        position_mode="net_mode",
        account_level="2",
        account_uid="account-123",
        instruments=None,
        pending_algos=None,
        positions=None,
    ):
        self.flag = "1"
        self.position_mode = position_mode
        self.account_level = account_level
        self.account_uid = account_uid
        self.instruments = instruments or {}
        self.instrument_calls = []
        self.pending_algos = pending_algos or []
        self.positions = positions or []

    def get_account_config(self):
        return [
            {
                "acctLv": self.account_level,
                "posMode": self.position_mode,
                "uid": self.account_uid,
            }
        ]

    def get_instruments(self, *, inst_type, inst_id):
        self.instrument_calls.append((inst_type, inst_id))
        payload = self.instruments.get(inst_id)
        return [] if payload is None else [payload]

    def get_pending_algo_orders(self, *, inst_id, ord_type="conditional"):
        return [
            item
            for item in self.pending_algos
            if item["instId"] == inst_id and item["ordType"] == ord_type
        ]

    def get_positions(self, *, inst_type):
        assert inst_type == "SWAP"
        return self.positions


def _set_live_environment(monkeypatch):
    monkeypatch.setenv("DEMO_OKX_API_KEY", "key")
    monkeypatch.setenv("DEMO_OKX_API_SECRET", "secret")
    monkeypatch.setenv("DEMO_OKX_PASSPHRASE", "passphrase")
    monkeypatch.setenv("OKX_FLAG", "1")
    monkeypatch.setenv("MAYBECH_ARM_ORDERS", "1")


def _instrument(inst_id, *, state="live", minimum="0.1", lot="0.1"):
    return {
        "instId": inst_id,
        "state": state,
        "minSz": minimum,
        "lotSz": lot,
        "tickSz": "0.01",
        "ctVal": "0.01",
    }


def _valid_strategy(store):
    return store.create(
        id="strategy-a",
        name="Strategy A",
        enabled=True,
        target_instruments=["ETH-USDT-SWAP"],
        entry_signal={"type": "price_above", "symbol": "self", "value": 100},
        default_rules={
            "close_conditions": [
                {
                    "purpose": "stop_loss",
                    "expression": {
                        "type": "price_below",
                        "symbol": "self",
                        "value": 90,
                    },
                }
            ]
        },
        metadata={
            "position_side": "long",
            "order_size_contracts": {"ETH-USDT-SWAP": "0.2"},
            "max_entry_slippage_pct": "0.005",
        },
    )


def _valid_risk(db_path):
    return AccountRiskStore(db_path).save(
        AccountRiskLimits(
            enabled=True,
            max_order_notional_usd=Decimal("100"),
            max_total_exposure_usd=Decimal("1000"),
            max_leverage=Decimal("5"),
            allowed_instruments=("ETH-USDT-SWAP",),
        )
    )


def test_live_preflight_rejects_missing_local_safety_configuration(monkeypatch, tmp_path):
    for key in (
        "DEMO_OKX_API_KEY",
        "DEMO_OKX_API_SECRET",
        "DEMO_OKX_PASSPHRASE",
        "MAYBECH_ARM_ORDERS",
    ):
        monkeypatch.delenv(key, raising=False)
    store = StrategyStore(str(tmp_path / "trades.db"))

    with pytest.raises(LivePreflightError) as exc_info:
        run_live_preflight(
            client=FakePreflightClient(),
            strategy_store=store,
            position_store=LogicalPositionStore(store.db_path),
        )
    assert "DEMO_OKX_API_KEY is required" in exc_info.value.errors
    assert "MAYBECH_ARM_ORDERS must be exactly '1'" in str(exc_info.value)


def test_live_preflight_rejects_unrepresented_exchange_exposure(monkeypatch, tmp_path):
    _set_live_environment(monkeypatch)
    db_path = str(tmp_path / "trades.db")
    strategy_store = StrategyStore(db_path)
    position_store = LogicalPositionStore(db_path)
    _valid_risk(db_path)
    client = FakePreflightClient(
        positions=[
            {
                "instId": "ETH-USDT-SWAP",
                "posSide": "net",
                "pos": "2",
                "avgPx": "3000",
                "markPx": "3100",
            }
        ]
    )

    with pytest.raises(LivePreflightError, match="does not reconcile"):
        run_live_preflight(
            client=client,
            strategy_store=strategy_store,
            position_store=position_store,
            risk_store=AccountRiskStore(db_path),
            include_strategy=False,
        )

def test_live_preflight_validates_strategies_sizes_and_active_positions(monkeypatch, tmp_path):
    _set_live_environment(monkeypatch)
    strategy_store = StrategyStore(str(tmp_path / "trades.db"))
    _valid_strategy(strategy_store)
    _valid_risk(strategy_store.db_path)
    position_store = LogicalPositionStore(strategy_store.db_path)
    position_store.save(
        LogicalPositionRecord(
            id="position-a",
            inst_id="BTC-USDT-SWAP",
            side="long",
            status="open",
            opened_quantity=1,
            remaining_quantity=1,
        )
    )

    position_store.save_protection(
        LogicalPositionProtection(
            position_id="position-a",
            kind="standalone_stop",
            algo_id="algo-position-a",
            algo_client_order_id="algo-client-position-a",
            quantity=1,
            stop_loss=90,
        )
    )
    client = FakePreflightClient(
        instruments={
            "BTC-USDT-SWAP": _instrument("BTC-USDT-SWAP"),
            "ETH-USDT-SWAP": _instrument("ETH-USDT-SWAP"),
        },
        pending_algos=[
            {
                "algoId": "algo-position-a",
                "algoClOrdId": "algo-client-position-a",
                "instId": "BTC-USDT-SWAP",
                "side": "sell",
                "ordType": "conditional",
                "state": "live",
                "posSide": "net",
                "reduceOnly": "true",
                "sz": "1",
                "slTriggerPx": "90",
                "slOrdPx": "-1",
            }
        ],
        positions=[
            {
                "instId": "BTC-USDT-SWAP",
                "posSide": "net",
                "pos": "1",
                "avgPx": "100",
                "markPx": "101",
            }
        ],
    )

    report = run_live_preflight(
        client=client,
        strategy_store=strategy_store,
        position_store=position_store,
    )

    assert report.passed is True
    assert report.armed is False
    assert report.execution_mode == "demo"
    assert report.position_mode == "net_mode"
    assert len(report.account_scope) == 24
    assert report.risk_limits_enabled is True
    assert report.instruments == ("BTC-USDT-SWAP", "ETH-USDT-SWAP")
    assert client.instrument_calls == [
        ("SWAP", "BTC-USDT-SWAP"),
        ("SWAP", "ETH-USDT-SWAP"),
    ]


def test_live_preflight_rejects_active_unit_without_owned_protection(
    monkeypatch,
    tmp_path,
):
    _set_live_environment(monkeypatch)
    strategy_store = StrategyStore(str(tmp_path / "trades.db"))
    _valid_risk(strategy_store.db_path)
    position_store = LogicalPositionStore(strategy_store.db_path)
    position_store.save(
        LogicalPositionRecord(
            id="unprotected-unit",
            inst_id="BTC-USDT-SWAP",
            side="long",
            status="open",
            opened_quantity=1,
            remaining_quantity=1,
        )
    )

    with pytest.raises(LivePreflightError, match="has no owned protection"):
        run_live_preflight(
            client=FakePreflightClient(
                instruments={"BTC-USDT-SWAP": _instrument("BTC-USDT-SWAP")}
            ),
            strategy_store=strategy_store,
            position_store=position_store,
        )


def test_live_preflight_rejects_strategy_target_outside_account_allowlist(
    monkeypatch,
    tmp_path,
):
    _set_live_environment(monkeypatch)
    strategy_store = StrategyStore(str(tmp_path / "trades.db"))
    _valid_strategy(strategy_store)
    AccountRiskStore(strategy_store.db_path).save(
        AccountRiskLimits(
            enabled=True,
            max_order_notional_usd=Decimal("100"),
            max_total_exposure_usd=Decimal("1000"),
            max_leverage=Decimal("5"),
            allowed_instruments=("BTC-USDT-SWAP",),
        )
    )

    with pytest.raises(LivePreflightError, match="outside the account risk allowlist"):
        run_live_preflight(
            client=FakePreflightClient(
                instruments={
                    "BTC-USDT-SWAP": _instrument("BTC-USDT-SWAP"),
                    "ETH-USDT-SWAP": _instrument("ETH-USDT-SWAP"),
                }
            ),
            strategy_store=strategy_store,
            position_store=LogicalPositionStore(strategy_store.db_path),
        )


@pytest.mark.parametrize(
    ("position_mode", "instrument", "expected"),
    [
        ("long_short_mode", _instrument("ETH-USDT-SWAP"), "must be net_mode"),
        ("net_mode", _instrument("ETH-USDT-SWAP", state="suspend"), "not tradable"),
        ("net_mode", _instrument("ETH-USDT-SWAP", lot="0.3"), "not a multiple"),
    ],
)
def test_live_preflight_rejects_incompatible_exchange_state(
    monkeypatch,
    tmp_path,
    position_mode,
    instrument,
    expected,
):
    _set_live_environment(monkeypatch)
    strategy_store = StrategyStore(str(tmp_path / "trades.db"))
    _valid_strategy(strategy_store)
    _valid_risk(strategy_store.db_path)

    with pytest.raises(LivePreflightError, match=expected):
        run_live_preflight(
            client=FakePreflightClient(
                position_mode=position_mode,
                instruments={"ETH-USDT-SWAP": instrument},
            ),
            strategy_store=strategy_store,
            position_store=LogicalPositionStore(strategy_store.db_path),
        )


def test_order_arming_requires_preflight_result():
    disarm_order_placement()
    with pytest.raises(PermissionError, match="after live preflight"):
        arm_order_placement()
    assert client_module._ORDER_PLACEMENT_ARMED is False

    arm_order_placement(preflight_passed=True)
    assert client_module._ORDER_PLACEMENT_ARMED is True
    disarm_order_placement()


def test_runtime_preflight_endpoint_exposes_verified_state():
    runner = DaemonRunner()
    runner.runtime.set_value(
        "runtime.live_preflight",
        {
            "passed": True,
            "armed": True,
            "execution_mode": "real",
            "account_level": "2",
            "position_mode": "net_mode",
            "enabled_strategies": 1,
            "instruments": ["ETH-USDT-SWAP"],
            "checked_at": "2026-06-28T00:00:00+00:00",
        },
    )

    response = TestClient(create_app(runner)).get("/runtime/preflight")

    assert response.status_code == 200
    assert response.json()["armed"] is True
    assert response.json()["position_mode"] == "net_mode"


def test_live_preflight_skips_strategy_checks_when_service_is_disabled(monkeypatch, tmp_path):
    _set_live_environment(monkeypatch)
    strategy_store = StrategyStore(str(tmp_path / "trades.db"))
    strategy_store.create(id="invalid", name="Invalid", enabled=True)
    _valid_risk(strategy_store.db_path)

    report = run_live_preflight(
        client=FakePreflightClient(
            instruments={"ETH-USDT-SWAP": _instrument("ETH-USDT-SWAP")}
        ),
        strategy_store=strategy_store,
        position_store=LogicalPositionStore(strategy_store.db_path),
        include_strategy=False,
    )

    assert report.passed is True
    assert report.enabled_strategies == 0


def test_live_preflight_requires_enabled_persisted_risk_limits(monkeypatch, tmp_path):
    _set_live_environment(monkeypatch)
    strategy_store = StrategyStore(str(tmp_path / "trades.db"))

    with pytest.raises(LivePreflightError, match="risk limits are not configured"):
        run_live_preflight(
            client=FakePreflightClient(),
            strategy_store=strategy_store,
            position_store=LogicalPositionStore(strategy_store.db_path),
            include_strategy=False,
        )


def test_live_preflight_requires_authenticated_account_uid(monkeypatch, tmp_path):
    _set_live_environment(monkeypatch)
    strategy_store = StrategyStore(str(tmp_path / "trades.db"))
    _valid_risk(strategy_store.db_path)

    with pytest.raises(LivePreflightError, match="missing uid"):
        run_live_preflight(
            client=FakePreflightClient(account_uid=""),
            strategy_store=strategy_store,
            position_store=LogicalPositionStore(strategy_store.db_path),
            include_strategy=False,
        )


def test_default_runner_arms_only_after_successful_preflight(monkeypatch, tmp_path):
    store = TradeStore(str(tmp_path / "trades.db"))
    monkeypatch.setattr(runtime_module, "TradeStore", lambda: store)
    real_lease = runtime_module.RuntimeLease
    monkeypatch.setattr(
        runtime_module,
        "RuntimeLease",
        lambda **kwargs: real_lease(**kwargs, lock_root=tmp_path / "locks"),
    )
    monkeypatch.setattr(
        runtime_module.ExecutionFillService,
        "setup",
        lambda self: None,
    )
    monkeypatch.setattr(
        runtime_module.AccountSnapshotService,
        "setup",
        lambda self: None,
    )
    preflight_calls = []

    class Report:
        passed = True
        account_scope = "scope-a"

        @staticmethod
        def to_dict(*, armed):
            return {
                "passed": True,
                "armed": armed,
                "execution_mode": "demo",
                "account_level": "2",
                "position_mode": "net_mode",
                "account_scope": "scope-a",
                "enabled_strategies": 0,
                "instruments": [],
                "checked_at": "2026-06-28T00:00:00+00:00",
            }

    def successful_preflight(**kwargs):
        preflight_calls.append(kwargs)
        return Report()

    monkeypatch.setattr(runtime_module, "run_live_preflight", successful_preflight)

    risk_store = AccountRiskStore(store.db_path)
    risk_store.set_entries_enabled(True)
    runner = runtime_module.create_default_runner(dry_run=False, include_strategy=False)

    assert client_module._ORDER_PLACEMENT_ARMED is True
    assert client_module._ENTRY_ORDER_PLACEMENT_ENABLED is False
    assert risk_store.entries_enabled() is False
    assert runner.runtime.get_value("runtime.live_preflight")["armed"] is True
    assert preflight_calls[0]["include_strategy"] is False
    assert runner.services["execution_fills"].enable_private_stream is True
    assert runner.runtime.get_value("runtime.lease")["held"] is True
    runner.teardown_services()
    assert client_module._ORDER_PLACEMENT_ARMED is False
    assert runner.runtime.get_value("runtime.lease")["held"] is False


def test_failed_runner_preflight_leaves_orders_disarmed(monkeypatch, tmp_path):
    store = TradeStore(str(tmp_path / "trades.db"))
    monkeypatch.setattr(runtime_module, "TradeStore", lambda: store)
    arm_order_placement(preflight_passed=True)

    def failed_preflight(**kwargs):
        raise LivePreflightError(["wrong account mode"])

    monkeypatch.setattr(runtime_module, "run_live_preflight", failed_preflight)

    with pytest.raises(LivePreflightError, match="wrong account mode"):
        runtime_module.create_default_runner(dry_run=False)

    assert client_module._ORDER_PLACEMENT_ARMED is False


def test_live_safe_uses_production_reads_without_registering_execution_services(
    monkeypatch, tmp_path
):
    store = TradeStore(str(tmp_path / "safe.db"))
    monkeypatch.setattr(runtime_module, "TradeStore", lambda: store)
    real_lease = runtime_module.RuntimeLease
    monkeypatch.setattr(
        runtime_module,
        "RuntimeLease",
        lambda **kwargs: real_lease(**kwargs, lock_root=tmp_path / "locks"),
    )
    for service in (
        runtime_module.AccountSnapshotService,
        runtime_module.BTCRegimeService,
        runtime_module.ExecutionFillService,
    ):
        monkeypatch.setattr(service, "setup", lambda self: None)

    class Report:
        passed = True
        account_scope = "production-scope"

        @staticmethod
        def to_dict(*, armed):
            return {
                "passed": True,
                "armed": armed,
                "execution_mode": "live_safe",
                "exchange_enabled": True,
                "order_submission_enabled": False,
                "account_scope": "production-scope",
                "checked_at": "2026-07-02T00:00:00+00:00",
            }

    calls = []
    monkeypatch.setattr(
        runtime_module,
        "run_live_preflight",
        lambda **kwargs: calls.append(kwargs) or Report(),
    )
    monkeypatch.setattr(
        runtime_module,
        "arm_order_placement",
        lambda **kwargs: pytest.fail("Live Safe must never arm orders"),
    )

    runner = runtime_module.create_default_runner(mode="live_safe")
    try:
        assert calls[0]["runtime_mode"] is runtime_module.RuntimeMode.LIVE_SAFE
        assert calls[0]["include_strategy"] is False
        assert "account" in runner.services
        assert "execution_fills" in runner.services
        assert runner.services["execution_fills"].allow_order_mutations is False
        assert "position_manager" not in runner.services
        assert "strategy" not in runner.services
        assert runner.runtime.get_value("runtime.live_preflight")["armed"] is False
    finally:
        runner.teardown_services()


def test_default_dry_runner_exclusively_owns_its_database(monkeypatch, tmp_path):
    store = TradeStore(str(tmp_path / "trades.db"))
    monkeypatch.setattr(runtime_module, "TradeStore", lambda: store)
    real_lease = runtime_module.RuntimeLease
    monkeypatch.setattr(
        runtime_module,
        "RuntimeLease",
        lambda **kwargs: real_lease(**kwargs, lock_root=tmp_path / "locks"),
    )
    first = runtime_module.create_default_runner(dry_run=True, include_strategy=False)

    with pytest.raises(RuntimeError, match="already leased"):
        runtime_module.create_default_runner(dry_run=True, include_strategy=False)

    first.teardown_services()
    replacement = runtime_module.create_default_runner(
        dry_run=True,
        include_strategy=False,
    )
    replacement.teardown_services()
