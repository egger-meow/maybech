"""FastAPI adapter for the Maybech daemon runtime."""

from __future__ import annotations

import asyncio
from asyncio import QueueEmpty
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect

from src.daemon.events import RuntimeEvent
from src.daemon.service import DaemonRunner


def _serialize_event(event: RuntimeEvent) -> dict:
    return {
        "id": event.id,
        "type": event.type,
        "source": event.source,
        "created_at": event.created_at.isoformat(),
        "payload": event.payload,
    }


def _serialize_status(status: Optional[dict]) -> Optional[dict]:
    if status is None:
        return None
    serialized = dict(status)
    for key, value in list(serialized.items()):
        if isinstance(value, datetime):
            serialized[key] = value.isoformat()
    return serialized


def create_app(runner: DaemonRunner) -> FastAPI:
    """Create an API app bound to a daemon runner."""
    app = FastAPI(title="Maybech Runtime API", version="0.1.0")
    app.state.runner = runner

    @app.get("/health")
    def health() -> dict:
        return {"ok": True, "running": runner.running}

    @app.get("/services")
    def list_services() -> dict:
        return {
            name: _serialize_status(runner.get_service_status(name))
            for name in runner.services
        }

    @app.get("/services/{name}")
    def get_service(name: str) -> dict:
        status = runner.get_service_status(name)
        if status is None:
            raise HTTPException(status_code=404, detail=f"Unknown service: {name}")
        return _serialize_status(status)

    @app.post("/services/{name}/enable")
    def enable_service(name: str) -> dict:
        if not runner.enable_service(name):
            raise HTTPException(status_code=404, detail=f"Unknown service: {name}")
        return _serialize_status(runner.get_service_status(name))

    @app.post("/services/{name}/disable")
    def disable_service(name: str) -> dict:
        if not runner.disable_service(name):
            raise HTTPException(status_code=404, detail=f"Unknown service: {name}")
        return _serialize_status(runner.get_service_status(name))

    @app.get("/events")
    def recent_events(limit: int = 100, event_type: Optional[str] = None) -> list[dict]:
        events = runner.runtime.events.recent(limit=limit, event_type=event_type)
        return [_serialize_event(event) for event in events]

    @app.get("/market/btc-regime")
    def get_btc_regime() -> dict:
        regime = runner.runtime.get_value("market.btc_regime")
        if regime is None:
            raise HTTPException(status_code=404, detail="BTC regime is not available yet")
        return regime

    @app.get("/strategy/decisions")
    def get_strategy_decisions() -> list[dict]:
        decisions = runner.runtime.get_value("strategy.decisions")
        if decisions is None:
            return []
        return decisions

    @app.get("/position/intents")
    def get_position_intents() -> list[dict]:
        intents = runner.runtime.get_value("position.intents")
        if intents is None:
            return []
        return intents

    @app.get("/account/snapshot")
    def get_account_snapshot() -> dict:
        snapshot = runner.runtime.get_value("account.snapshot")
        if snapshot is None:
            return {"summary": {}, "positions": [], "orders": []}
        return snapshot

    @app.get("/account/positions")
    def get_account_positions() -> list[dict]:
        snapshot = runner.runtime.get_value("account.snapshot")
        if snapshot is None:
            return []
        return snapshot.get("positions", [])

    @app.get("/account/orders")
    def get_account_orders() -> list[dict]:
        snapshot = runner.runtime.get_value("account.snapshot")
        if snapshot is None:
            return []
        return snapshot.get("orders", [])

    @app.websocket("/ws/events")
    async def stream_events(websocket: WebSocket) -> None:
        await websocket.accept()
        queue = asyncio.Queue(maxsize=100)
        loop = asyncio.get_running_loop()

        def enqueue(event: RuntimeEvent) -> None:
            def put_nowait() -> None:
                if queue.full():
                    try:
                        queue.get_nowait()
                    except QueueEmpty:
                        pass
                queue.put_nowait(event)

            loop.call_soon_threadsafe(put_nowait)

        unsubscribe = runner.runtime.events.subscribe(enqueue)
        try:
            for event in runner.runtime.events.recent(limit=50):
                await websocket.send_json(_serialize_event(event))

            while True:
                event = await queue.get()
                await websocket.send_json(_serialize_event(event))
        except WebSocketDisconnect:
            pass
        finally:
            unsubscribe()

    return app
