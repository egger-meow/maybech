"""Inbound LINE Messaging API webhook: identity-checked, fixed-grammar command bot.

A LINE Official Account can be added as a friend and messaged by anyone who finds
it — LINE gives no built-in guarantee that only the operator talks to it. Every
inbound sender is therefore checked against the single configured LINE_USER_ID
before any command runs; unrecognized senders never reach the command grammar.

Authorized commands execute by calling this same FastAPI app's own HTTP routes
in-process (via an ASGI transport), so every existing safety gate — bearer auth,
the replica read-only guard, strategy validation, optimistic-concurrency checks —
applies identically to a chat-triggered action as to one made through the
dashboard. No trading logic is duplicated here.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import httpx
from fastapi import FastAPI
from linebot.v3.webhooks import Event, MessageEvent, TextMessageContent

from src.api.line_commands import HELP_TEXT, ParsedCommand, parse_command
from src.notifications.line_bot import LineBotNotifier
from src.trading.audit_event_store import AuditEventStore

logger = logging.getLogger(__name__)

UNAUTHORIZED_REPLY = "此帳號未授權使用本機器人。"
GENERIC_FAILURE_REPLY = "指令執行失敗，請稍後再試。"


@dataclass
class LineWebhookHandler:
    app: FastAPI
    api_token: str
    authorized_user_id: str
    line: LineBotNotifier
    audit_store_factory: Callable[[], AuditEventStore] = AuditEventStore
    unauthorized_reply_cooldown: float = 60.0
    _unauthorized_reply_at: dict[str, float] = field(default_factory=dict, init=False)

    async def handle_event(self, event: Event) -> None:
        if not isinstance(event, MessageEvent) or not isinstance(
            event.message, TextMessageContent
        ):
            return
        user_id = getattr(event.source, "user_id", None)
        if not user_id or not self.authorized_user_id or user_id != self.authorized_user_id:
            await self._handle_unauthorized(event, user_id)
            return
        parsed = parse_command(event.message.text)
        if parsed is None:
            await self._reply(event.reply_token, HELP_TEXT)
            return
        self._record_audit_event(
            "line_bot.command_received",
            {"command": parsed.name, "arg": parsed.arg},
        )
        reply_text = await self._execute(parsed)
        await self._reply(event.reply_token, reply_text)

    async def _handle_unauthorized(self, event: Event, user_id: str | None) -> None:
        digest = hashlib.sha256((user_id or "unknown").encode("utf-8")).hexdigest()[:16]
        self._record_audit_event("line_bot.unauthorized_attempt", {"user_id_hash": digest})
        now = time.monotonic()
        last = self._unauthorized_reply_at.get(digest)
        if last is not None and now - last < self.unauthorized_reply_cooldown:
            return
        self._unauthorized_reply_at[digest] = now
        await self._reply(event.reply_token, UNAUTHORIZED_REPLY)

    async def _execute(self, parsed: ParsedCommand) -> str:
        try:
            if parsed.name == "status":
                return await self._status_reply()
            if parsed.name == "strategies":
                return await self._strategies_reply()
            if parsed.name in {"enable", "disable"}:
                return await self._toggle_strategy(parsed.arg, enable=parsed.name == "enable")
            if parsed.name == "mute":
                return self._mute_last_repeat_reply()
        except Exception:
            logger.exception("LINE bot command failed: %s", parsed.name)
            return GENERIC_FAILURE_REPLY
        return HELP_TEXT

    def _mute_last_repeat_reply(self) -> str:
        muted = self.line.mute_last_repeat()
        if not muted:
            return "目前沒有重複通知可以停止。"
        self._record_audit_event("line_bot.repeat_muted", {})
        return "已停止這則通知重複發送；換內容的話還是會通知你。"

    async def _call(
        self, method: str, path: str, json_body: dict[str, Any] | None = None
    ) -> httpx.Response:
        transport = httpx.ASGITransport(app=self.app)
        headers = {"Authorization": f"Bearer {self.api_token}"} if self.api_token else {}
        async with httpx.AsyncClient(transport=transport, base_url="http://line-bot") as client:
            return await client.request(method, path, json=json_body, headers=headers)

    async def _status_reply(self) -> str:
        entries = (await self._call("GET", "/risk/entries")).json()
        positions = (await self._call("GET", "/positions/logical?status=open")).json()
        health = (await self._call("GET", "/notifications/health")).json()
        entry_state = "啟用" if entries.get("entries_enabled") else "已停止（Kill Switch）"
        lines = [f"進場狀態：{entry_state}", f"未平倉部位：{len(positions)} 筆"]
        for position in positions[:10]:
            quantity = position.get("remaining_quantity") or position.get("opened_quantity")
            lines.append(
                f"・{position['inst_id']} {position['side']} "
                f"{quantity} @ {position['entry_price']}"
            )
        for channel in health.get("channels", []):
            lines.append(f"通知（{channel['channel']}）：{channel['state']}")
        return "\n".join(lines)

    async def _strategies_reply(self) -> str:
        strategies = (await self._call("GET", "/strategies")).json()
        if not strategies:
            return "尚未建立任何策略。"
        lines = ["策略清單："]
        for strategy in strategies:
            state = "啟用" if strategy.get("enabled") else "停用"
            lines.append(f"・{strategy['id']}（{strategy.get('name', '')}）－{state}")
        return "\n".join(lines)

    async def _toggle_strategy(self, strategy_id: str | None, *, enable: bool) -> str:
        if not strategy_id:
            return "請提供策略 ID，例如：enable strategy-a"
        get_resp = await self._call("GET", f"/strategies/{strategy_id}")
        if get_resp.status_code == 404:
            return f"找不到策略：{strategy_id}"
        if get_resp.status_code != 200:
            return f"查詢策略失敗（HTTP {get_resp.status_code}）"
        strategy = get_resp.json()
        if enable:
            resp = await self._call(
                "POST",
                f"/strategies/{strategy_id}/enable",
                {"confirm": True, "expected_updated_at": strategy["updated_at"]},
            )
        else:
            resp = await self._call("POST", f"/strategies/{strategy_id}/disable")
        if resp.status_code != 200:
            try:
                detail = resp.json().get("detail", resp.text)
            except ValueError:
                detail = resp.text
            return f"操作失敗（HTTP {resp.status_code}）：{detail}"
        state = "啟用" if enable else "停用"
        return f"策略 {strategy_id} 已{state}。"

    async def _reply(self, reply_token: str, text: str) -> None:
        await asyncio.to_thread(self.line.reply, reply_token, text)

    def _record_audit_event(self, event_type: str, payload: dict[str, Any]) -> None:
        try:
            self.audit_store_factory().create(
                type=event_type, source="line_webhook", payload=payload
            )
        except Exception:
            logger.exception("Failed to record LINE webhook audit event")
