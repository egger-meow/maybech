"""
Account & position monitoring dashboard.

Provides a snapshot of:
- Current account balance (total equity, available balance)
- Open positions with unrealised PnL
- Recent trade history
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from src.exchange.client import OKXClient

logger = logging.getLogger(__name__)


class Dashboard:
    """Tracks account state and positions in real-time."""

    def __init__(self, client: OKXClient) -> None:
        self.client = client

    def get_account_summary(self) -> dict:
        """Return total equity, available balance, margin ratio, etc."""
        data = self.client.get_balance()
        if not data:
            return {}

        acct = data[0]
        available_equity = _number_or_none(acct.get("availEq"))
        unrealized_pnl = _number_or_none(acct.get("upl"))
        summary = {
            "total_equity": _number_or_none(acct.get("totalEq")),
            "total_equity_currency": "USD",
            "available_equity": available_equity,
            "available_equity_currency": "USD" if available_equity is not None else None,
            "available_equity_status": (
                "account_valued" if available_equity is not None else "per_currency_only"
            ),
            "available_equity_source": "account.availEq" if available_equity is not None else "",
            "unrealized_pnl": unrealized_pnl,
            "unrealized_pnl_currency": "USD" if unrealized_pnl is not None else None,
            "unrealized_pnl_status": (
                "account_valued" if unrealized_pnl is not None else "per_currency_only"
            ),
            "unrealized_pnl_source": "account.upl" if unrealized_pnl is not None else "",
            "initial_margin": _number_or_none(acct.get("imr")),
            "maintenance_margin": _number_or_none(acct.get("mmr")),
            "margin_ratio": acct.get("mgnRatio", ""),
            "update_time": _ts_to_str(acct.get("uTime", "")),
            "currencies": [],
        }

        for detail in acct.get("details", []):
            currency = str(detail.get("ccy") or "")
            summary["currencies"].append(
                {
                    "ccy": currency,
                    "equity": _number_or_none(detail.get("eq")),
                    "equity_usd": _number_or_none(detail.get("eqUsd")),
                    "discounted_equity_usd": _number_or_none(detail.get("disEq")),
                    "available_balance": _number_or_none(detail.get("availBal")),
                    "available_equity": _number_or_none(detail.get("availEq")),
                    "cash_balance": _number_or_none(detail.get("cashBal")),
                    "frozen_balance": _number_or_none(detail.get("frozenBal")),
                    "unrealized_pnl": _number_or_none(detail.get("upl")),
                    "unrealised_pnl": _number_or_none(detail.get("upl")),
                    "native_currency": currency,
                }
            )

        if not summary["currencies"]:
            summary["available_equity_status"] = (
                "account_valued" if available_equity is not None else "unavailable"
            )
            summary["unrealized_pnl_status"] = (
                "account_valued" if unrealized_pnl is not None else "unavailable"
            )

        return summary

    def get_open_positions(self) -> list[dict]:
        """Return all open positions with unrealised PnL."""
        raw_positions = self.client.get_positions()
        positions = []
        for pos in raw_positions:
            # Skip empty positions
            pos_size = pos.get("pos", "0")
            if pos_size == "0" or pos_size == "":
                continue

            positions.append({
                "inst_id": pos.get("instId", ""),
                "inst_type": pos.get("instType", ""),
                "pos_side": pos.get("posSide", ""),
                "position": pos_size,
                "avg_price": pos.get("avgPx", ""),
                "mark_price": pos.get("markPx", ""),
                "unrealised_pnl": pos.get("upl", "0"),
                "unrealised_pnl_ratio": pos.get("uplRatio", "0"),
                "leverage": pos.get("lever", ""),
                "liquidation_price": pos.get("liqPx", ""),
                "margin_mode": pos.get("mgnMode", ""),
                "update_time": _ts_to_str(pos.get("uTime", "")),
            })

        return positions

    def get_recent_trades(self, limit: int = 20, inst_type: str = "SWAP") -> list[dict]:
        """Return recent completed trades for review."""
        raw_orders = self.client.get_order_history(
            inst_type=inst_type, limit=str(limit),
        )
        trades = []
        for order in raw_orders:
            state = order.get("state", "")
            if state != "filled":
                continue

            trades.append({
                "order_id": order.get("ordId", ""),
                "inst_id": order.get("instId", ""),
                "side": order.get("side", ""),
                "order_type": order.get("ordType", ""),
                "size": order.get("accFillSz", "0"),
                "avg_price": order.get("avgPx", ""),
                "fee": order.get("fee", "0"),
                "fee_ccy": order.get("feeCcy", ""),
                "pnl": order.get("pnl", "0"),
                "state": state,
                "fill_time": _ts_to_str(order.get("fillTime", "")),
            })

        return trades

    def print_summary(self) -> None:
        """Pretty-print account status to console / log."""
        summary = self.get_account_summary()
        if not summary:
            logger.warning("No account data available.")
            return

        print("\n" + "=" * 60)
        print("  ACCOUNT SUMMARY")
        print("=" * 60)
        print(f"  Total Equity     : {summary['total_equity']}")
        print(f"  Available Equity : {summary['available_equity']}")
        print(f"  Initial Margin   : {summary['initial_margin']}")
        print(f"  Maint. Margin    : {summary['maintenance_margin']}")
        print(f"  Margin Ratio     : {summary['margin_ratio']}")
        print(f"  Updated          : {summary['update_time']}")

        if summary["currencies"]:
            print("\n  --- Currencies ---")
            for c in summary["currencies"]:
                equity = str(c["equity"] if c["equity"] is not None else "N/A")
                available = str(
                    c["available_balance"]
                    if c["available_balance"] is not None
                    else "N/A"
                )
                print(
                    f"  {c['ccy']:>6s}  eq={equity:>15s}  "
                    f"avail={available:>15s}  "
                    f"upl={c['unrealised_pnl']}"
                )

        positions = self.get_open_positions()
        if positions:
            print("\n  --- Open Positions ---")
            for p in positions:
                print(
                    f"  {p['inst_id']:>12s}  side={p['pos_side']:>5s}  "
                    f"pos={p['position']:>10s}  avgPx={p['avg_price']:>10s}  "
                    f"upl={p['unrealised_pnl']}"
                )
        else:
            print("\n  No open positions.")
        print("=" * 60 + "\n")


def _ts_to_str(ts_ms: str) -> str:
    """Convert OKX millisecond timestamp string to readable datetime."""
    if not ts_ms:
        return ""
    try:
        dt = datetime.fromtimestamp(int(ts_ms) / 1000, tz=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    except (ValueError, OSError):
        return ts_ms


def _number_or_none(value: object) -> str | None:
    if value in (None, ""):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return str(value) if number.is_finite() else None

if __name__ == "__main__":
    import sys
    sys.path.append("..")
    client = OKXClient()
    dashboard = Dashboard(client)
    dashboard.print_summary()
