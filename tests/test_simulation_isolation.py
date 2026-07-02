import json

from src.data.simulation_market import SimulationMarketClient
import src.daemon.account_service as account_module
import src.daemon.btc_regime_service as btc_module
import src.daemon.execution_fill_service as fills_module
import src.daemon.position_manager_service as position_module
import src.daemon.strategy_service as strategy_module
import src.daemon.runtime as runtime_module
from src.api.app import create_app
from src.daemon.service import DaemonRunner
from fastapi.testclient import TestClient
from src.trading.trade_store import TradeStore


def test_simulation_market_client_reads_only_local_replay(tmp_path):
    replay = tmp_path / "candles.json"
    rows = [["1000", "1", "2", "0.5", "1.5", "10", "1", "10", "1"]]
    replay.write_text(json.dumps({"BTC-USDT-SWAP:1m": rows}), encoding="utf-8")

    client = SimulationMarketClient(replay)

    assert client.flag == "simulation"
    assert client.get_candles("BTC-USDT-SWAP", "1m") == rows
    assert client.get_candles("ETH-USDT-SWAP", "1m") == []


def test_simulation_runner_never_constructs_okx_client(monkeypatch, tmp_path):
    store = TradeStore(str(tmp_path / "simulation.db"))
    monkeypatch.setattr(runtime_module, "TradeStore", lambda: store)

    def forbidden():
        raise AssertionError("Simulation must not construct an OKX client")

    for module in (account_module, btc_module, fills_module, position_module, strategy_module):
        monkeypatch.setattr(module, "OKXClient", forbidden)

    runner = runtime_module.create_default_runner(mode="simulation")
    try:
        runner.setup_services()
        assert runner.runtime.get_value("runtime.live_preflight")["exchange_enabled"] is False
    finally:
        runner.teardown_services()


def test_simulation_api_market_reads_and_exchange_mutations_never_construct_okx(
    monkeypatch, tmp_path
):
    replay = tmp_path / "candles.json"
    replay.write_text(json.dumps({}), encoding="utf-8")
    monkeypatch.setenv("MAYBECH_SIMULATION_CANDLES", str(replay))
    runner = DaemonRunner()
    runner.runtime.set_value(
        "runtime.live_preflight",
        {"execution_mode": "simulation", "armed": False},
    )
    monkeypatch.setattr(
        "src.api.app.OKXClient",
        lambda: (_ for _ in ()).throw(AssertionError("unexpected OKX client")),
    )
    client = TestClient(create_app(runner, api_token=""))

    candles = client.get("/market/candles", params={"inst_id": "BTC-USDT-SWAP"})
    refresh = client.post("/instruments/refresh")

    assert candles.status_code == 200
    assert candles.json()["candles"] == []
    assert refresh.status_code == 409
    assert refresh.json()["detail"] == "Simulation does not connect to OKX"


def test_live_safe_api_rejects_order_mutation_before_constructing_client(monkeypatch):
    runner = DaemonRunner()
    runner.runtime.set_value(
        "runtime.live_preflight",
        {"execution_mode": "live_safe", "armed": False},
    )
    monkeypatch.setattr(
        "src.api.app.OKXClient",
        lambda: (_ for _ in ()).throw(AssertionError("unexpected order client")),
    )

    response = TestClient(create_app(runner, api_token="")).post(
        "/positions/logical/missing/protection", json={"confirm": True}
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "Live Safe disables all order mutations"
