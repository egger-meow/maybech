"""FastAPI adapter for the Maybech daemon runtime."""

from __future__ import annotations

import asyncio
from asyncio import QueueEmpty
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect

from src.daemon.events import RuntimeEvent
from src.daemon.service import DaemonRunner

from src.trading.trade_store import TradeStore
from src.trading.rules import PositionRule, RuleGroup
from src.api.schemas import (
    TradeResponse, TradeDetailResponse, TradeRuleResponse,
    TradeRuleAttach, RuleGroupResponse, PositionRuleResponse
)


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
    async def websocket_events(websocket: WebSocket, types: Optional[str] = None):
        """Subscribe to live runtime events.

        Optionally filter by event types (comma-separated).
        """
        await websocket.accept()
        event_types = set(types.split(",")) if types else None

        queue = asyncio.Queue()

        def _handler(event: RuntimeEvent):
            if event_types and event.type not in event_types:
                return
            queue.put_nowait(event)

        runner.bus.subscribe("*", _handler)

        try:
            while True:
                # Flush existing
                while not queue.empty():
                    try:
                        event = queue.get_nowait()
                        await websocket.send_json(_serialize_event(event))
                    except QueueEmpty:
                        break

                await asyncio.sleep(0.1)

        except WebSocketDisconnect:
            pass
        finally:
            runner.bus.unsubscribe("*", _handler)

    # ------------------------------------------------------------------------
    # Trades & Rules API
    # ------------------------------------------------------------------------

    @app.get("/trades/open", response_model=list[TradeDetailResponse])
    def get_open_trades():
        store = TradeStore()
        trades = store.get_open_trades()
        result = []
        for t in trades:
            rules = store.get_trade_rules(t.id)
            active_rules = []
            for group, enabled in rules:
                active_rules.append(
                    TradeRuleResponse(
                        group=RuleGroupResponse(
                            id=group.id,
                            name=group.name,
                            operator=group.operator,
                            created_at=group.created_at,
                            rules=[PositionRuleResponse(**r.to_dict()) for r in group.rules]
                        ),
                        enabled=enabled
                    )
                )
            tr_dict = t.to_dict()
            tr_dict["active_rules"] = active_rules
            result.append(TradeDetailResponse(**tr_dict))
        return result

    @app.get("/trades/history", response_model=list[TradeResponse])
    def get_trade_history(limit: int = 50, strategy_id: Optional[str] = None):
        store = TradeStore()
        trades = store.get_trade_history(limit=limit, strategy_id=strategy_id, status="closed")
        return [TradeResponse(**t.to_dict()) for t in trades]

    @app.post("/trades/{trade_id}/rules", response_model=RuleGroupResponse)
    def add_trade_rule(trade_id: str, payload: TradeRuleAttach):
        store = TradeStore()
        trade = store.get_trade(trade_id)
        if not trade:
            raise HTTPException(status_code=404, detail="Trade not found")
        if trade.status != "open":
            raise HTTPException(status_code=400, detail="Cannot add rules to closed trade")

        # Convert payload to RuleGroup model
        rules = []
        for r_in in payload.rule_group.rules:
            rules.append(
                PositionRule(
                    target=r_in.target,
                    metric=r_in.metric,
                    operator=r_in.operator,
                    value=r_in.value
                )
            )
        
        group = RuleGroup(
            name=payload.rule_group.name,
            operator=payload.rule_group.operator,
            rules=rules
        )
        
        store.attach_rule_group(trade_id, group, enabled=payload.enabled)
        
        return RuleGroupResponse(
            id=group.id,
            name=group.name,
            operator=group.operator,
            created_at=group.created_at,
            rules=[PositionRuleResponse(**r.to_dict()) for r in group.rules]
        )

    @app.delete("/trades/{trade_id}/rules/{group_id}")
    def remove_trade_rule(trade_id: str, group_id: str):
        store = TradeStore()
        if not store.get_trade(trade_id):
            raise HTTPException(status_code=404, detail="Trade not found")
            
        success = store.remove_rule_group(group_id)
        if not success:
            raise HTTPException(status_code=404, detail="Rule group not found")
            
        return {"status": "ok"}

    return app
