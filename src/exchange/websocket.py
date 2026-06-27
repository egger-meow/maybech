"""Authenticated OKX private order stream with bounded reconnect."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import queue
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from websockets.asyncio.client import connect


_PRODUCTION_URL = "wss://ws.okx.com:8443/ws/v5/private"
_DEMO_URL = "wss://wspap.okx.com:8443/ws/v5/private"


@dataclass(frozen=True)
class PrivateOrderStreamStatus:
    enabled: bool
    connected: bool
    events_received: int
    reconnects: int
    dropped_events: int
    last_message_at: str
    last_error: str


class OKXPrivateOrderStream:
    """Own one authenticated ``orders/SWAP`` connection in a worker thread."""

    def __init__(
        self,
        *,
        api_key: str,
        api_secret: str,
        passphrase: str,
        flag: str,
        connect_factory: Callable[..., Any] = connect,
        queue_size: int = 10_000,
    ) -> None:
        if not api_key or not api_secret or not passphrase:
            raise ValueError("Private order stream requires OKX credentials")
        if flag not in {"0", "1"}:
            raise ValueError("Private order stream flag must be '0' or '1'")
        self.api_key = api_key
        self.api_secret = api_secret
        self.passphrase = passphrase
        self.url = _DEMO_URL if flag == "1" else _PRODUCTION_URL
        self._connect_factory = connect_factory
        self._events: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=queue_size)
        self._stop = threading.Event()
        self._startup_complete = threading.Event()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._socket: Any = None
        self._lock = threading.Lock()
        self._connected = False
        self._ever_connected = False
        self._events_received = 0
        self._reconnects = 0
        self._dropped_events = 0
        self._last_message_at = ""
        self._last_error = ""
        self._startup_error = ""

    def start(self, *, timeout: float = 10.0) -> None:
        """Start and require login/subscription acknowledgement before returning."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._startup_complete.clear()
        self._startup_error = ""
        self._thread = threading.Thread(
            target=self._thread_main,
            name="okx-private-orders",
            daemon=True,
        )
        self._thread.start()
        if not self._startup_complete.wait(timeout):
            self.stop()
            raise TimeoutError("Timed out subscribing to OKX private order events")
        if self._startup_error:
            self.stop()
            raise RuntimeError(self._startup_error)

    def stop(self) -> None:
        self._stop.set()
        loop = self._loop
        socket = self._socket
        if loop is not None and socket is not None and loop.is_running():
            try:
                asyncio.run_coroutine_threadsafe(socket.close(), loop)
            except RuntimeError:
                pass
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=5.0)
        self._thread = None
        self._set_connected(False)

    def drain(self, *, limit: int = 1000) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for _ in range(max(0, limit)):
            try:
                events.append(self._events.get_nowait())
            except queue.Empty:
                break
        return events

    def status(self) -> PrivateOrderStreamStatus:
        with self._lock:
            return PrivateOrderStreamStatus(
                enabled=True,
                connected=self._connected,
                events_received=self._events_received,
                reconnects=self._reconnects,
                dropped_events=self._dropped_events,
                last_message_at=self._last_message_at,
                last_error=self._last_error,
            )

    def status_dict(self) -> dict[str, Any]:
        return asdict(self.status())

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._run())
        except Exception as exc:
            self._record_error(str(exc))
            if not self._ever_connected:
                self._startup_error = f"OKX private order stream startup failed: {exc}"
                self._startup_complete.set()

    async def _run(self) -> None:
        self._loop = asyncio.get_running_loop()
        delay = 1.0
        while not self._stop.is_set():
            try:
                await self._connect_once()
                delay = 1.0
            except Exception as exc:
                self._set_connected(False)
                self._record_error(str(exc))
                if not self._ever_connected:
                    self._startup_error = (
                        f"OKX private order stream startup failed: {exc}"
                    )
                    self._startup_complete.set()
                    return
                with self._lock:
                    self._reconnects += 1
                await self._sleep_until_stopped(delay)
                delay = min(delay * 2, 30.0)

    async def _connect_once(self) -> None:
        async with self._connect_factory(
            self.url,
            open_timeout=10,
            close_timeout=5,
            ping_interval=None,
            max_queue=128,
        ) as websocket:
            self._socket = websocket
            await websocket.send(json.dumps(self._login_message(), separators=(",", ":")))
            login = await self._receive_json(websocket, timeout=10)
            if login.get("event") != "login" or str(login.get("code")) != "0":
                raise RuntimeError(
                    f"OKX WebSocket login rejected: {login.get('code')} {login.get('msg')}"
                )

            await websocket.send(
                json.dumps(
                    {
                        "id": "maybech-orders",
                        "op": "subscribe",
                        "args": [{"channel": "orders", "instType": "SWAP"}],
                    },
                    separators=(",", ":"),
                )
            )
            subscribed = await self._await_subscription(websocket)
            if not subscribed:
                raise RuntimeError("OKX orders/SWAP subscription was not acknowledged")

            self._set_connected(True)
            self._ever_connected = True
            self._startup_complete.set()
            self._record_error("")
            await self._receive_events(websocket)
        self._socket = None
        self._set_connected(False)

    async def _await_subscription(self, websocket: Any) -> bool:
        deadline = asyncio.get_running_loop().time() + 10
        while asyncio.get_running_loop().time() < deadline:
            message = await self._receive_json(websocket, timeout=10)
            event = str(message.get("event") or "")
            if event == "error":
                raise RuntimeError(
                    f"OKX WebSocket subscription rejected: "
                    f"{message.get('code')} {message.get('msg')}"
                )
            arg = message.get("arg") if isinstance(message.get("arg"), dict) else {}
            if (
                event == "subscribe"
                and arg.get("channel") == "orders"
                and arg.get("instType") == "SWAP"
            ):
                return True
        return False

    async def _receive_events(self, websocket: Any) -> None:
        while not self._stop.is_set():
            try:
                raw = await asyncio.wait_for(websocket.recv(), timeout=25)
            except TimeoutError:
                await websocket.send("ping")
                raw = await asyncio.wait_for(websocket.recv(), timeout=10)
            if raw == "pong":
                continue
            message = self._parse_message(raw)
            if message.get("event") == "error":
                raise RuntimeError(
                    f"OKX WebSocket error: {message.get('code')} {message.get('msg')}"
                )
            arg = message.get("arg") if isinstance(message.get("arg"), dict) else {}
            data = message.get("data")
            if arg.get("channel") != "orders" or not isinstance(data, list):
                continue
            for item in data:
                if isinstance(item, dict):
                    self._enqueue(item)

    async def _receive_json(self, websocket: Any, *, timeout: float) -> dict[str, Any]:
        raw = await asyncio.wait_for(websocket.recv(), timeout=timeout)
        if raw == "pong":
            return {}
        return self._parse_message(raw)

    @staticmethod
    def _parse_message(raw: Any) -> dict[str, Any]:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            message = json.loads(raw)
        except (TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("OKX WebSocket sent invalid JSON") from exc
        if not isinstance(message, dict):
            raise ValueError("OKX WebSocket message must be an object")
        return message

    def _enqueue(self, event: dict[str, Any]) -> None:
        with self._lock:
            self._events_received += 1
            self._last_message_at = datetime.now(timezone.utc).isoformat()
        try:
            self._events.put_nowait(event)
        except queue.Full:
            with self._lock:
                self._dropped_events += 1

    def _login_message(self) -> dict[str, Any]:
        timestamp = str(int(time.time()))
        payload = timestamp + "GET" + "/users/self/verify"
        signature = base64.b64encode(
            hmac.new(
                self.api_secret.encode("utf-8"),
                payload.encode("utf-8"),
                hashlib.sha256,
            ).digest()
        ).decode("ascii")
        return {
            "op": "login",
            "args": [
                {
                    "apiKey": self.api_key,
                    "passphrase": self.passphrase,
                    "timestamp": timestamp,
                    "sign": signature,
                }
            ],
        }

    async def _sleep_until_stopped(self, seconds: float) -> None:
        deadline = asyncio.get_running_loop().time() + seconds
        while not self._stop.is_set() and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.1)

    def _set_connected(self, value: bool) -> None:
        with self._lock:
            self._connected = value

    def _record_error(self, value: str) -> None:
        with self._lock:
            self._last_error = value
