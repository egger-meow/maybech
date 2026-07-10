"""HTTP-level coverage for POST /notifications/line/webhook.

Handler-level command behavior (parsing, authorization, dispatch) is covered by
tests/test_line_webhook.py; this file only exercises what's specific to the
actual route: signature verification, the "not configured" gate, and that the
route bypasses MAYBECH_API_TOKEN (LINE can't send our bearer token).
"""

import base64
import hashlib
import hmac
import json
from dataclasses import replace

from fastapi.testclient import TestClient

from src.api.app import create_app
from src.daemon.service import DaemonRunner
from src.trading.audit_event_store import AuditEventStore
from src.trading.strategy_store import StrategyStore

CHANNEL_SECRET = "test-channel-secret"
AUTHORIZED_USER_ID = "U_OPERATOR"


class RecordingLineNotifier:
    enabled = True
    last_error = ""

    def __init__(self) -> None:
        self.replies: list[tuple[str, str]] = []

    def reply(self, reply_token: str, message: str) -> bool:
        self.replies.append((reply_token, message))
        return True


def _configure_line_settings(monkeypatch, tmp_path, notifier: RecordingLineNotifier | None = None):
    from src.api.app import settings as api_settings

    monkeypatch.setattr(
        "src.api.app.settings",
        replace(
            api_settings,
            LINE_CHANNEL_ACCESS_TOKEN="test-access-token",
            LINE_CHANNEL_SECRET=CHANNEL_SECRET,
            LINE_USER_ID=AUTHORIZED_USER_ID,
        ),
    )
    if notifier is not None:
        monkeypatch.setattr("src.api.app.LineBotNotifier", lambda: notifier)
    # The webhook handler's own audit trail (line_bot.command_received /
    # unauthorized_attempt) otherwise falls back to whatever real trades.db
    # the developer has configured locally. Redirect it to a tmp path.
    db_path = str(tmp_path / "trades.db")
    audit_store = AuditEventStore(db_path)
    monkeypatch.setattr("src.api.line_webhook.AuditEventStore", lambda *a, **kw: audit_store)
    monkeypatch.setattr("src.api.app.AuditEventStore", lambda *a, **kw: audit_store)
    monkeypatch.setattr("src.api.app.StrategyStore", lambda *a, **kw: StrategyStore(db_path))


def _signed_body(payload: dict, *, secret: str = CHANNEL_SECRET) -> tuple[bytes, str]:
    body = json.dumps(payload).encode("utf-8")
    signature = base64.b64encode(
        hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    ).decode("utf-8")
    return body, signature


def _text_message_payload(*, user_id: str, text: str) -> dict:
    return {
        "destination": "dest",
        "events": [
            {
                "type": "message",
                "message": {"type": "text", "id": "1", "text": text, "quoteToken": "qt-1"},
                "timestamp": 1234567890,
                "source": {"type": "user", "userId": user_id},
                "replyToken": "reply-token-1",
                "mode": "active",
                "webhookEventId": "wh-1",
                "deliveryContext": {"isRedelivery": False},
            }
        ],
    }


def test_webhook_returns_503_when_line_not_configured(monkeypatch):
    from src.api.app import settings as api_settings

    monkeypatch.setattr(
        "src.api.app.settings",
        replace(
            api_settings,
            LINE_CHANNEL_ACCESS_TOKEN="",
            LINE_CHANNEL_SECRET="",
            LINE_USER_ID="",
        ),
    )
    client = TestClient(create_app(DaemonRunner()))

    response = client.post(
        "/notifications/line/webhook",
        content=b"{}",
        headers={"X-Line-Signature": "irrelevant"},
    )

    assert response.status_code == 503


def test_webhook_rejects_invalid_signature(monkeypatch, tmp_path):
    _configure_line_settings(monkeypatch, tmp_path, RecordingLineNotifier())
    client = TestClient(create_app(DaemonRunner()))
    body, _ = _signed_body(_text_message_payload(user_id=AUTHORIZED_USER_ID, text="status"))

    response = client.post(
        "/notifications/line/webhook",
        content=body,
        headers={"X-Line-Signature": "not-the-real-signature"},
    )

    assert response.status_code == 400


def test_webhook_bypasses_bearer_token_but_verifies_line_signature(monkeypatch, tmp_path):
    """LINE cannot send our Authorization header; the signature is the auth."""
    notifier = RecordingLineNotifier()
    _configure_line_settings(monkeypatch, tmp_path, notifier)
    client = TestClient(create_app(DaemonRunner(), api_token="unrelated-bearer-secret"))
    body, signature = _signed_body(
        _text_message_payload(user_id=AUTHORIZED_USER_ID, text="strategies")
    )

    response = client.post(
        "/notifications/line/webhook",
        content=body,
        headers={"X-Line-Signature": signature},
    )

    assert response.status_code == 200
    assert len(notifier.replies) == 1
    assert notifier.replies[0][0] == "reply-token-1"


def test_webhook_ignores_commands_from_unauthorized_sender(monkeypatch, tmp_path):
    notifier = RecordingLineNotifier()
    _configure_line_settings(monkeypatch, tmp_path, notifier)
    client = TestClient(create_app(DaemonRunner()))
    body, signature = _signed_body(
        _text_message_payload(user_id="U_STRANGER", text="enable some-strategy")
    )

    response = client.post(
        "/notifications/line/webhook",
        content=body,
        headers={"X-Line-Signature": signature},
    )

    assert response.status_code == 200
    assert len(notifier.replies) == 1
    assert "未授權" in notifier.replies[0][1]
