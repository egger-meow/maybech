import base64
import hashlib
import hmac
import json

import pytest

import src.exchange.websocket as websocket_module
from src.exchange.websocket import OKXPrivateOrderStream


class FakeSocket:
    def __init__(self, responses, *, on_last=None):
        self.responses = list(responses)
        self.on_last = on_last
        self.sent = []
        self.closed = False

    async def send(self, payload):
        self.sent.append(payload)

    async def recv(self):
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        if not self.responses and self.on_last is not None:
            self.on_last()
        return response

    async def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self, socket=None, error=None):
        self.socket = socket
        self.error = error

    async def __aenter__(self):
        if self.error is not None:
            raise self.error
        return self.socket

    async def __aexit__(self, exc_type, exc, traceback):
        return False


def _stream(**kwargs):
    return OKXPrivateOrderStream(
        api_key="api-key",
        api_secret="secret",
        passphrase="passphrase",
        flag="1",
        **kwargs,
    )


def test_private_order_stream_builds_official_login_signature(monkeypatch):
    monkeypatch.setattr(websocket_module.time, "time", lambda: 1_538_054_050)
    stream = _stream()

    message = stream._login_message()

    expected = base64.b64encode(
        hmac.new(
            b"secret",
            b"1538054050GET/users/self/verify",
            hashlib.sha256,
        ).digest()
    ).decode("ascii")
    assert message == {
        "op": "login",
        "args": [
            {
                "apiKey": "api-key",
                "passphrase": "passphrase",
                "timestamp": "1538054050",
                "sign": expected,
            }
        ],
    }
    assert stream.url == "wss://wspap.okx.com:8443/ws/v5/private"


@pytest.mark.asyncio
async def test_private_order_stream_logs_in_subscribes_and_queues_orders():
    stream = _stream()
    socket = FakeSocket(
        [
            json.dumps({"event": "login", "code": "0"}),
            json.dumps(
                {
                    "event": "subscribe",
                    "arg": {"channel": "orders", "instType": "SWAP"},
                }
            ),
            json.dumps(
                {
                    "arg": {"channel": "orders", "instType": "SWAP"},
                    "data": [{"ordId": "order-a", "state": "filled"}],
                }
            ),
        ],
        on_last=stream._stop.set,
    )
    stream._connect_factory = lambda *args, **kwargs: FakeConnection(socket)

    await stream._connect_once()

    assert stream.drain() == [{"ordId": "order-a", "state": "filled"}]
    assert json.loads(socket.sent[0])["op"] == "login"
    assert json.loads(socket.sent[1]) == {
        "id": "maybech-orders",
        "op": "subscribe",
        "args": [{"channel": "orders", "instType": "SWAP"}],
    }
    assert stream.status().events_received == 1


@pytest.mark.asyncio
async def test_private_order_stream_reconnects_after_established_connection_drops():
    stream = _stream()
    acknowledgement = json.dumps(
        {
            "event": "subscribe",
            "arg": {"channel": "orders", "instType": "SWAP"},
        }
    )
    sockets = [
        FakeSocket(
            [
                json.dumps({"event": "login", "code": "0"}),
                acknowledgement,
                OSError("connection dropped"),
            ]
        ),
        FakeSocket(
            [json.dumps({"event": "login", "code": "0"}), acknowledgement, "pong"],
            on_last=stream._stop.set,
        ),
    ]
    stream._connect_factory = lambda *args, **kwargs: FakeConnection(sockets.pop(0))
    stream._sleep_until_stopped = lambda seconds: websocket_module.asyncio.sleep(0)

    await stream._run()

    assert stream.status().reconnects == 1
    assert sockets == []


def test_private_order_stream_start_fails_before_subscription():
    stream = _stream(
        connect_factory=lambda *args, **kwargs: FakeConnection(
            error=OSError("connection refused")
        )
    )

    with pytest.raises(RuntimeError, match="startup failed: connection refused"):
        stream.start(timeout=1)

    assert stream.status().connected is False
