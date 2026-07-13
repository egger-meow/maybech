import pytest
from linebot.v3.webhooks import Event

from src.api.app import create_app
from src.api.line_webhook import UNAUTHORIZED_REPLY, LineWebhookHandler
from src.api.line_commands import HELP_TEXT
from src.daemon.service import DaemonRunner
from src.trading.account_risk import AccountRiskStore
from src.trading.audit_event_store import AuditEventStore
from src.trading.entry_control import EntryControlManager
from src.trading.logical_position_store import LogicalPositionStore
from src.trading.strategy_store import StrategyStore
from src.trading.trade_store import TradeStore

pytestmark = pytest.mark.asyncio


class FakeLineNotifier:
    def __init__(self, *, has_repeat_to_mute: bool = False) -> None:
        self.replies: list[tuple[str, str]] = []
        self._has_repeat_to_mute = has_repeat_to_mute
        self.muted = False

    def reply(self, reply_token: str, message: str) -> bool:
        self.replies.append((reply_token, message))
        return True

    def mute_last_repeat(self) -> bool:
        if not self._has_repeat_to_mute:
            return False
        self.muted = True
        return True


def _text_event(*, user_id: str, text: str, reply_token: str = "reply-token") -> Event:
    return Event.from_dict(
        {
            "type": "message",
            "message": {
                "type": "text",
                "id": "msg-1",
                "text": text,
                "quoteToken": "qt-1",
            },
            "timestamp": 1234567890,
            "source": {"type": "user", "userId": user_id},
            "replyToken": reply_token,
            "mode": "active",
            "webhookEventId": "wh-1",
            "deliveryContext": {"isRedelivery": False},
        }
    )


def _make_handler(tmp_path, monkeypatch, *, has_repeat_to_mute: bool = False):
    db_path = str(tmp_path / "trades.db")
    # app.py resolves each store with no args (settings.MAYBECH_DB_PATH default),
    # which would otherwise hit whatever real trades.db the developer has
    # configured locally. Redirect every store class app.py references to this
    # test's tmp_path so command execution is fully isolated and deterministic.
    monkeypatch.setattr("src.api.app.StrategyStore", lambda *a, **kw: StrategyStore(db_path))
    monkeypatch.setattr("src.api.app.TradeStore", lambda *a, **kw: TradeStore(db_path))
    monkeypatch.setattr("src.api.app.AuditEventStore", lambda *a, **kw: AuditEventStore(db_path))
    monkeypatch.setattr(
        "src.api.app.EntryControlManager",
        lambda: EntryControlManager(
            risk_store=AccountRiskStore(db_path),
            position_store=LogicalPositionStore(db_path),
            audit_store=AuditEventStore(db_path),
        ),
    )
    app = create_app(DaemonRunner(), api_token="")
    audit_store = AuditEventStore(db_path)
    line = FakeLineNotifier(has_repeat_to_mute=has_repeat_to_mute)
    handler = LineWebhookHandler(
        app=app,
        api_token="",
        authorized_user_id="U_AUTH",
        line=line,
        audit_store_factory=lambda: audit_store,
    )
    return handler, line, audit_store, db_path


async def test_unauthorized_sender_gets_generic_reply_and_is_audited(tmp_path, monkeypatch):
    handler, line, audit_store, _ = _make_handler(tmp_path, monkeypatch)

    await handler.handle_event(_text_event(user_id="U_STRANGER", text="status"))

    assert len(line.replies) == 1
    assert line.replies[0][1] == UNAUTHORIZED_REPLY
    events = audit_store.list(event_type="line_bot.unauthorized_attempt")
    assert len(events) == 1
    assert "U_STRANGER" not in str(events[0].payload)


async def test_unauthorized_sender_reply_is_rate_limited(tmp_path, monkeypatch):
    handler, line, audit_store, _ = _make_handler(tmp_path, monkeypatch)
    handler.unauthorized_reply_cooldown = 999

    await handler.handle_event(_text_event(user_id="U_STRANGER", text="status"))
    await handler.handle_event(_text_event(user_id="U_STRANGER", text="strategies"))

    assert len(line.replies) == 1
    assert len(audit_store.list(event_type="line_bot.unauthorized_attempt")) == 2


async def test_unrecognized_command_replies_with_help(tmp_path, monkeypatch):
    handler, line, _, _ = _make_handler(tmp_path, monkeypatch)

    await handler.handle_event(_text_event(user_id="U_AUTH", text="do a backflip"))

    assert line.replies == [("reply-token", HELP_TEXT)]


async def test_status_command_summarizes_entries_and_positions(tmp_path, monkeypatch):
    handler, line, audit_store, _ = _make_handler(tmp_path, monkeypatch)

    await handler.handle_event(_text_event(user_id="U_AUTH", text="status"))

    assert len(line.replies) == 1
    reply_text = line.replies[0][1]
    assert "進場狀態：已停止（Kill Switch）" in reply_text
    assert "未平倉部位：0 筆" in reply_text
    assert len(audit_store.list(event_type="line_bot.command_received")) == 1


async def test_strategies_command_lists_created_strategy(tmp_path, monkeypatch):
    handler, line, _, db_path = _make_handler(tmp_path, monkeypatch)
    store = StrategyStore(db_path)
    store.create(
        id="strategy-a",
        name="Trend Follower",
        kind="signal",
        enabled=False,
        target_instruments=["BTC-USDT-SWAP"],
        entry_signal={},
        default_rules={},
        metadata={},
        execution_delay_seconds=0,
    )

    await handler.handle_event(_text_event(user_id="U_AUTH", text="strategies"))

    reply_text = line.replies[0][1]
    assert "strategy-a" in reply_text
    assert "Trend Follower" in reply_text
    assert "停用" in reply_text


async def test_enable_command_enables_strategy_via_real_endpoint(tmp_path, monkeypatch):
    handler, line, audit_store, db_path = _make_handler(tmp_path, monkeypatch)
    store = StrategyStore(db_path)
    store.create(
        id="strategy-a",
        name="Trend Follower",
        kind="signal",
        enabled=False,
        target_instruments=["ETH-USDT-SWAP"],
        entry_signal={"type": "price_above", "symbol": "self", "value": 100},
        default_rules={"close_conditions": [{
            "purpose": "stop_loss",
            "expression": {"type": "price_below", "symbol": "self", "value": 90},
        }]},
        metadata={
            "position_side": "long",
            "order_size_contracts": {"ETH-USDT-SWAP": "1"},
            "max_entry_slippage_pct": "0.005",
        },
        execution_delay_seconds=0,
    )

    await handler.handle_event(_text_event(user_id="U_AUTH", text="enable strategy-a"))

    reply_text = line.replies[0][1]
    assert "已啟用" in reply_text
    assert store.get("strategy-a").enabled is True
    # The real /strategies/{id}/enable handler records its own strategy.enabled
    # audit event in addition to the bot's own command-received event.
    assert audit_store.list(event_type="strategy.enabled")


async def test_enable_command_reports_missing_strategy(tmp_path, monkeypatch):
    handler, line, _, _ = _make_handler(tmp_path, monkeypatch)

    await handler.handle_event(_text_event(user_id="U_AUTH", text="enable does-not-exist"))

    assert "找不到策略" in line.replies[0][1]


async def test_enable_command_without_argument_asks_for_id(tmp_path, monkeypatch):
    handler, line, _, _ = _make_handler(tmp_path, monkeypatch)

    await handler.handle_event(_text_event(user_id="U_AUTH", text="enable"))

    assert "請提供策略 ID" in line.replies[0][1]


async def test_mute_command_mutes_the_last_repeat_and_is_audited(tmp_path, monkeypatch):
    handler, line, audit_store, _ = _make_handler(tmp_path, monkeypatch, has_repeat_to_mute=True)

    await handler.handle_event(_text_event(user_id="U_AUTH", text="幹你娘閉嘴"))

    assert line.muted is True
    assert "已停止" in line.replies[0][1]
    assert len(audit_store.list(event_type="line_bot.repeat_muted")) == 1


async def test_mute_command_with_nothing_to_mute_replies_accordingly(tmp_path, monkeypatch):
    handler, line, audit_store, _ = _make_handler(tmp_path, monkeypatch, has_repeat_to_mute=False)

    await handler.handle_event(_text_event(user_id="U_AUTH", text="mute"))

    assert line.muted is False
    assert "目前沒有重複通知" in line.replies[0][1]
    assert audit_store.list(event_type="line_bot.repeat_muted") == []
