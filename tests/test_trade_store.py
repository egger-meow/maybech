from src.trading.rules import PositionRule, RuleGroup
from src.trading.logical_position_store import LogicalPositionStore
from src.trading.trade_store import TradeRecord, TradeStore


def test_trade_store_records_schema_version(tmp_path):
    store = TradeStore(str(tmp_path / "trades.db"))

    assert store.applied_schema_versions() == [1]


def test_trade_and_logical_position_stores_share_schema_ledger(tmp_path):
    db_path = str(tmp_path / "trades.db")
    trade_store = TradeStore(db_path)
    position_store = LogicalPositionStore(db_path)

    assert trade_store.applied_schema_versions() == [1]
    assert position_store.applied_schema_versions() == [2, 3, 4, 5, 6, 7]


def test_trade_store_saves_closes_and_removes_rules(tmp_path):
    store = TradeStore(str(tmp_path / "trades.db"))
    trade = TradeRecord(
        id="trade-a",
        strategy_id="strategy-a",
        inst_id="ETH-USDT-SWAP",
        side="long",
        entry_price=100,
    )
    store.save_trade(trade)
    store.attach_rule_group(
        trade.id,
        RuleGroup(
            id="rule-a",
            name="take profit",
            rules=[PositionRule(target="self", metric="price", operator="greater_than", value=110)],
        ),
    )

    closed = store.close_trade(trade.id, exit_price=120, exit_reason="target")

    assert closed.status == "closed"
    assert closed.pnl == 20
    assert closed.pnl_pct == 20
    assert store.get_trade_rules(trade.id) == []


def test_trade_store_rule_deletion_is_trade_scoped(tmp_path):
    store = TradeStore(str(tmp_path / "trades.db"))
    first = TradeRecord(id="trade-a", inst_id="ETH-USDT-SWAP", side="long", entry_price=100)
    second = TradeRecord(id="trade-b", inst_id="SOL-USDT-SWAP", side="long", entry_price=50)
    store.save_trade(first)
    store.save_trade(second)
    rule = RuleGroup(
        id="shared-looking-id",
        name="stop",
        rules=[PositionRule(target="self", metric="price", operator="less_than", value=45)],
    )
    store.attach_rule_group(second.id, rule)

    removed = store.remove_trade_rule_group(first.id, rule.id)

    assert removed is False
    assert store.get_trade_rules(second.id)[0][0].id == rule.id


def test_trade_store_parent_save_preserves_rule_groups(tmp_path):
    store = TradeStore(str(tmp_path / "trades.db"))
    trade = TradeRecord(id="trade-a", inst_id="ETH-USDT-SWAP", side="long", entry_price=100)
    store.save_trade(trade)
    rule = RuleGroup(
        id="rule-a",
        name="stop",
        rules=[PositionRule(target="self", metric="price", operator="less_than", value=90)],
    )
    store.attach_rule_group(trade.id, rule)

    trade.entry_price = 101
    store.save_trade(trade)

    rules = store.get_trade_rules(trade.id)
    assert len(rules) == 1
    assert rules[0][0].id == "rule-a"
