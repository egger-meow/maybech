"""Fixed command grammar for the inbound LINE bot.

Deliberately not free-form NLU: a small, enumerable set of commands keeps the
parsing surface (and therefore what a compromised/confused sender could trigger)
easy to reason about. Read-only status commands plus strategy enable/disable only
— destructive actions (kill-switch, position close) are intentionally not wired.
"""

from __future__ import annotations

from dataclasses import dataclass

HELP_TEXT = (
    "可用指令：\n"
    "status｜health － 查看進場狀態、未平倉部位、通知健康度\n"
    "strategies － 列出所有策略與啟用狀態\n"
    "enable <策略ID> － 啟用策略\n"
    "disable <策略ID> － 停用策略"
)

_ALIASES = {"health": "status"}
_READ_COMMANDS = {"status", "strategies"}
_TOGGLE_COMMANDS = {"enable", "disable"}


@dataclass(frozen=True)
class ParsedCommand:
    name: str
    arg: str | None = None


def parse_command(text: str) -> ParsedCommand | None:
    """Parse one line of chat text into a known command, or None if unrecognized."""
    tokens = text.strip().split(maxsplit=1)
    if not tokens:
        return None
    name = tokens[0].strip().lower()
    name = _ALIASES.get(name, name)
    arg = tokens[1].strip() if len(tokens) > 1 and tokens[1].strip() else None
    if name in _READ_COMMANDS:
        return ParsedCommand(name)
    if name in _TOGGLE_COMMANDS:
        return ParsedCommand(name, arg)
    return None
