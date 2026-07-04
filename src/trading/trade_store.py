"""SQLite-backed persistence for trades and their dynamic rule groups.

Tables
------
``trades``
    Trade records (open and closed).
``trade_rules``
    Dynamic rule groups attached to open trades.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator
from uuid import uuid4

from src.config.settings import settings
from src.trading.rules import RuleGroup
from src.trading.sqlite_schema import (
    applied_schema_versions,
    connect_database,
    configure_connection,
    initialize_schema,
    sqlite_read_only,
)

logger = logging.getLogger(__name__)

_SCHEMA_COMPONENT = "trade_store"
_SCHEMA_VERSION = 1


class TradeRecord:
    """Represents a single trade (open or closed)."""

    __slots__ = (
        "id", "strategy_id", "inst_id", "side",
        "entry_price", "entry_time",
        "exit_price", "exit_time", "exit_reason",
        "pnl", "pnl_pct", "status",
        "signal_reason", "btc_price_at_entry", "btc_price_at_exit",
        "metadata_json",
    )

    def __init__(
        self,
        *,
        id: str | None = None,
        strategy_id: str = "",
        inst_id: str = "",
        side: str = "",
        entry_price: float = 0.0,
        entry_time: str = "",
        exit_price: float | None = None,
        exit_time: str | None = None,
        exit_reason: str = "",
        pnl: float | None = None,
        pnl_pct: float | None = None,
        status: str = "open",
        signal_reason: str = "",
        btc_price_at_entry: float | None = None,
        btc_price_at_exit: float | None = None,
        metadata_json: str = "{}",
    ) -> None:
        self.id = id or uuid4().hex[:12]
        self.strategy_id = strategy_id
        self.inst_id = inst_id
        self.side = side
        self.entry_price = entry_price
        self.entry_time = entry_time or datetime.now(timezone.utc).isoformat()
        self.exit_price = exit_price
        self.exit_time = exit_time
        self.exit_reason = exit_reason
        self.pnl = pnl
        self.pnl_pct = pnl_pct
        self.status = status
        self.signal_reason = signal_reason
        self.btc_price_at_entry = btc_price_at_entry
        self.btc_price_at_exit = btc_price_at_exit
        self.metadata_json = metadata_json

    def to_dict(self) -> dict:
        return {attr: getattr(self, attr) for attr in self.__slots__}

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "TradeRecord":
        return cls(**{k: row[k] for k in row.keys()})


_SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id              TEXT PRIMARY KEY,
    strategy_id     TEXT NOT NULL DEFAULT '',
    inst_id         TEXT NOT NULL DEFAULT '',
    side            TEXT NOT NULL DEFAULT '',
    entry_price     REAL NOT NULL DEFAULT 0.0,
    entry_time      TEXT NOT NULL,
    exit_price      REAL,
    exit_time       TEXT,
    exit_reason     TEXT NOT NULL DEFAULT '',
    pnl             REAL,
    pnl_pct         REAL,
    status          TEXT NOT NULL DEFAULT 'open',
    signal_reason   TEXT NOT NULL DEFAULT '',
    btc_price_at_entry REAL,
    btc_price_at_exit  REAL,
    metadata_json   TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS trade_rules (
    id              TEXT PRIMARY KEY,
    trade_id        TEXT NOT NULL,
    rule_group_json TEXT NOT NULL,
    enabled         INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (trade_id) REFERENCES trades(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status);
CREATE INDEX IF NOT EXISTS idx_trades_strategy ON trades(strategy_id);
CREATE INDEX IF NOT EXISTS idx_trade_rules_trade ON trade_rules(trade_id);
"""


class TradeStore:
    """SQLite-backed persistence for trades and their dynamic rules."""

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path or settings.MAYBECH_DB_PATH
        if not sqlite_read_only():
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
            self._init_db()

    def _init_db(self) -> None:
        with self._conn() as conn:
            initialize_schema(
                conn,
                schema_sql=_SCHEMA,
                component=_SCHEMA_COMPONENT,
                version=_SCHEMA_VERSION,
            )

    @contextmanager
    def _conn(self) -> Generator[sqlite3.Connection, None, None]:
        conn = connect_database(self.db_path)
        configure_connection(conn)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # -- Trades --------------------------------------------------------------

    def save_trade(self, trade: TradeRecord) -> str:
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO trades
                   (id, strategy_id, inst_id, side,
                    entry_price, entry_time,
                    exit_price, exit_time, exit_reason,
                    pnl, pnl_pct, status,
                    signal_reason, btc_price_at_entry, btc_price_at_exit,
                    metadata_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                    strategy_id = excluded.strategy_id,
                    inst_id = excluded.inst_id,
                    side = excluded.side,
                    entry_price = excluded.entry_price,
                    entry_time = excluded.entry_time,
                    exit_price = excluded.exit_price,
                    exit_time = excluded.exit_time,
                    exit_reason = excluded.exit_reason,
                    pnl = excluded.pnl,
                    pnl_pct = excluded.pnl_pct,
                    status = excluded.status,
                    signal_reason = excluded.signal_reason,
                    btc_price_at_entry = excluded.btc_price_at_entry,
                    btc_price_at_exit = excluded.btc_price_at_exit,
                    metadata_json = excluded.metadata_json""",
                (
                    trade.id, trade.strategy_id, trade.inst_id, trade.side,
                    trade.entry_price, trade.entry_time,
                    trade.exit_price, trade.exit_time, trade.exit_reason,
                    trade.pnl, trade.pnl_pct, trade.status,
                    trade.signal_reason, trade.btc_price_at_entry,
                    trade.btc_price_at_exit, trade.metadata_json,
                ),
            )
        return trade.id

    def get_trade(self, trade_id: str) -> TradeRecord | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM trades WHERE id = ?", (trade_id,)
            ).fetchone()
        if row is None:
            return None
        return TradeRecord.from_row(row)

    def get_open_trades(self) -> list[TradeRecord]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM trades WHERE status = 'open' ORDER BY entry_time"
            ).fetchall()
        return [TradeRecord.from_row(r) for r in rows]

    def mark_trade_open(self, trade_id: str, *, entry_price: float) -> TradeRecord | None:
        trade = self.get_trade(trade_id)
        if trade is None or trade.status not in {"pending_open", "open"}:
            return None
        with self._conn() as conn:
            conn.execute(
                "UPDATE trades SET status = 'open', entry_price = ? WHERE id = ?",
                (entry_price, trade_id),
            )
        trade.status = "open"
        trade.entry_price = entry_price
        return trade

    def mark_trade_failed(self, trade_id: str, *, reason: str) -> TradeRecord | None:
        trade = self.get_trade(trade_id)
        if trade is None or trade.status != "pending_open":
            return None
        with self._conn() as conn:
            conn.execute(
                "UPDATE trades SET status = 'failed', exit_reason = ? WHERE id = ?",
                (reason, trade_id),
            )
        trade.status = "failed"
        trade.exit_reason = reason
        return trade

    def get_trade_history(
        self,
        *,
        limit: int = 50,
        strategy_id: str | None = None,
        status: str | None = None,
    ) -> list[TradeRecord]:
        query = "SELECT * FROM trades WHERE 1=1"
        params: list[Any] = []
        if strategy_id:
            query += " AND strategy_id = ?"
            params.append(strategy_id)
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY entry_time DESC LIMIT ?"
        params.append(limit)
        with self._conn() as conn:
            rows = conn.execute(query, params).fetchall()
        return [TradeRecord.from_row(r) for r in rows]

    def close_trade(
        self,
        trade_id: str,
        *,
        exit_price: float,
        exit_reason: str,
        btc_price_at_exit: float | None = None,
        realized_pnl: float | None = None,
        realized_pnl_pct: float | None = None,
        pnl_metadata: dict | None = None,
    ) -> TradeRecord | None:
        trade = self.get_trade(trade_id)
        if trade is None or trade.status != "open":
            return None

        if realized_pnl is None:
            if trade.side == "long":
                pnl = exit_price - trade.entry_price
            else:
                pnl = trade.entry_price - exit_price
            pnl_pct = (pnl / trade.entry_price) * 100 if trade.entry_price else 0.0
            pnl_evidence = {"status": "legacy_price_delta", "reliable": False}
        else:
            pnl = realized_pnl
            pnl_pct = realized_pnl_pct or 0.0
            pnl_evidence = {
                "status": "confirmed_allocations",
                "reliable": True,
                **(pnl_metadata or {}),
            }
        try:
            metadata = json.loads(trade.metadata_json or "{}")
        except json.JSONDecodeError:
            metadata = {}
        if not isinstance(metadata, dict):
            metadata = {}
        metadata["realized_pnl"] = pnl_evidence
        metadata_json = json.dumps(metadata, separators=(",", ":"), sort_keys=True)
        exit_time = datetime.now(timezone.utc).isoformat()

        with self._conn() as conn:
            conn.execute(
                """UPDATE trades SET
                   exit_price = ?, exit_time = ?, exit_reason = ?,
                   pnl = ?, pnl_pct = ?, status = 'closed', metadata_json = ?,
                   btc_price_at_exit = ?
                   WHERE id = ?""",
                (exit_price, exit_time, exit_reason, pnl, pnl_pct, metadata_json,
                 btc_price_at_exit, trade_id),
            )
            # Remove all active rules for closed trade
            conn.execute("DELETE FROM trade_rules WHERE trade_id = ?", (trade_id,))

        trade.exit_price = exit_price
        trade.exit_time = exit_time
        trade.exit_reason = exit_reason
        trade.pnl = pnl
        trade.pnl_pct = pnl_pct
        trade.metadata_json = metadata_json
        trade.status = "closed"
        trade.btc_price_at_exit = btc_price_at_exit
        return trade

    # -- Trade Rules ---------------------------------------------------------

    def attach_rule_group(
        self,
        trade_id: str,
        rule_group: RuleGroup,
        enabled: bool = True,
    ) -> str:
        """Serialize and attach a RuleGroup to a trade."""
        with self._conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO trade_rules
                   (id, trade_id, rule_group_json, enabled)
                   VALUES (?, ?, ?, ?)""",
                (rule_group.id, trade_id, json.dumps(rule_group.to_dict()), 1 if enabled else 0),
            )
        return rule_group.id

    def get_trade_rules(self, trade_id: str) -> list[tuple[RuleGroup, bool]]:
        """Return list of (RuleGroup, enabled_flag) attached to trade."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, rule_group_json, enabled FROM trade_rules WHERE trade_id = ?",
                (trade_id,),
            ).fetchall()
        
        result = []
        for row in rows:
            data = json.loads(row["rule_group_json"])
            data["id"] = row["id"] # ensure ID matches DB
            group = RuleGroup.from_dict(data)
            result.append((group, bool(row["enabled"])))
        return result

    def remove_rule_group(self, rule_group_id: str) -> bool:
        with self._conn() as conn:
            cur = conn.execute(
                "DELETE FROM trade_rules WHERE id = ?", (rule_group_id,)
            )
        return cur.rowcount > 0

    def remove_trade_rule_group(self, trade_id: str, rule_group_id: str) -> bool:
        """Remove a rule group only when it belongs to the requested trade."""
        with self._conn() as conn:
            cur = conn.execute(
                "DELETE FROM trade_rules WHERE trade_id = ? AND id = ?",
                (trade_id, rule_group_id),
            )
        return cur.rowcount > 0

    def set_rule_group_enabled(self, rule_group_id: str, enabled: bool) -> bool:
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE trade_rules SET enabled = ? WHERE id = ?",
                (1 if enabled else 0, rule_group_id),
            )
        return cur.rowcount > 0

    def applied_schema_versions(self) -> list[int]:
        with self._conn() as conn:
            return applied_schema_versions(conn, component=_SCHEMA_COMPONENT)
