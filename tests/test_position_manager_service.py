from src.daemon.events import RuntimeState
from src.daemon.position_manager_service import PositionManagerService
from src.trading.rules import PositionRule, RuleGroup
from src.trading.trade_store import TradeRecord, TradeStore


def _service_with_triggered_rule(tmp_path, *, dry_run: bool) -> tuple[PositionManagerService, TradeStore, str]:
    store = TradeStore(str(tmp_path / "trades.db"))
    trade = TradeRecord(
        id="trade-1",
        strategy_id="momentum",
        inst_id="ETH-USDT-SWAP",
        side="long",
        entry_price=100.0,
    )
    store.save_trade(trade)
    store.attach_rule_group(
        trade.id,
        RuleGroup(
            id="take-profit",
            name="take profit",
            operator="and",
            rules=[
                PositionRule(
                    target="self",
                    metric="price",
                    operator="greater_than",
                    value=110.0,
                )
            ],
        ),
    )

    service = PositionManagerService(store, dry_run=dry_run)
    service.runtime = RuntimeState()
    service.runtime.set_value("market.btc_regime", {"price": "65000"})
    service.runtime.set_value(
        "account.snapshot",
        {
            "positions": [
                {"inst_id": "ETH-USDT-SWAP", "mark_price": "120"}
            ]
        },
    )
    return service, store, trade.id


def test_position_manager_closes_triggered_trade_in_dry_run(tmp_path):
    service, store, trade_id = _service_with_triggered_rule(tmp_path, dry_run=True)

    service.tick()

    trade = store.get_trade(trade_id)
    assert trade is not None
    assert trade.status == "closed"
    assert trade.exit_price == 120.0
    assert service.runtime.get_value("position_manager.intents")[0]["action"] == "closed"


def test_position_manager_does_not_close_live_trade_without_executor(tmp_path):
    service, store, trade_id = _service_with_triggered_rule(tmp_path, dry_run=False)

    service.tick()

    trade = store.get_trade(trade_id)
    assert trade is not None
    assert trade.status == "open"

    intents = service.runtime.get_value("position_manager.intents")
    assert intents[0]["action"] == "manual_close_required"

    events = service.runtime.events.recent(event_type="position.close_blocked")
    assert len(events) == 1
    assert events[0].payload["trade_id"] == trade_id
