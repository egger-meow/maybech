from src.config.strategy import StrategyConfig, ensure_default_strategy
from src.trading.strategy_store import StrategyStore


def test_strategy_runtime_config_is_loaded_from_sqlite(tmp_path):
    store = StrategyStore(str(tmp_path / "strategies.db"))
    strategy = ensure_default_strategy(store)
    store.update(
        strategy.id,
        target_instruments=["ETH-USDT-SWAP"],
        entry_signal={
            "type": "volume_price_gap",
            "timeframe": "5m",
            "k_long": 7,
            "k_short": 6,
            "gap_threshold": 4,
        },
        metadata={"order_size_contracts": {"ETH-USDT-SWAP": "3"}},
    )

    config = StrategyConfig.load(store.db_path)

    assert config.target_instruments == ["ETH-USDT-SWAP"]
    assert config.timeframe == "5m"
    assert config.momentum.k_long == 7
    assert config.order_size_contracts == {"ETH-USDT-SWAP": "3"}
