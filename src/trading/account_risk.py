"""Persisted account risk limits and fail-closed live entry approval.

The envelope's primary invariant is the all-stop loss budget
(``max_stop_loss_equity_pct``): assuming every open position and the candidate
entry hit their stop-losses, the combined loss must stay under that percentage
of current account equity (OKX ``totalEq``). Per-order notional, total
exposure, and leverage caps remain as secondary bounds, but the budget is what
rules out any single catastrophic loss — many small losses are survivable, one
uncapped one is not. The budget excludes fees and stop-market slippage past
the trigger price, so it should be set with slack for both.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Generator
from uuid import uuid4

from src.config.settings import settings
from src.trading.logical_position_store import LogicalPositionStore
from src.trading.position_reconciliation import PositionReconciler
from src.trading.sqlite_schema import (
    applied_schema_versions,
    connect_database,
    configure_connection,
    initialize_schema,
    record_schema_version,
    sqlite_read_only,
)


_SCHEMA_COMPONENT = "account_risk"
_SCHEMA_VERSION = 4
_SCHEMA = """
CREATE TABLE IF NOT EXISTS account_risk_limits (
    id                         TEXT PRIMARY KEY CHECK (id = 'account'),
    enabled                    INTEGER NOT NULL DEFAULT 0,
    max_order_notional_usd     TEXT NOT NULL,
    max_total_exposure_usd     TEXT NOT NULL,
    max_leverage               TEXT NOT NULL,
    created_at                 TEXT NOT NULL,
    updated_at                 TEXT NOT NULL
);
"""

_SCHEMA_V2 = """
CREATE TABLE IF NOT EXISTS entry_control (
    id              TEXT PRIMARY KEY CHECK (id = 'account'),
    entries_enabled INTEGER NOT NULL DEFAULT 0,
    updated_at      TEXT NOT NULL
);
"""

_SCHEMA_V3 = """
ALTER TABLE account_risk_limits
ADD COLUMN allowed_instruments_json TEXT NOT NULL DEFAULT '[]';
"""

# '0' is deliberately invalid: a pre-v4 envelope stays readable but cannot
# approve entries or pass preflight until the operator sets a real budget.
_SCHEMA_V4 = """
ALTER TABLE account_risk_limits
ADD COLUMN max_stop_loss_equity_pct TEXT NOT NULL DEFAULT '0';
"""


def _decimal(value: object, *, field: str, allow_zero: bool = False) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Invalid {field}: {value!r}") from exc
    if not result.is_finite() or result < 0 or (result == 0 and not allow_zero):
        qualifier = "non-negative" if allow_zero else "positive"
        raise ValueError(f"{field} must be {qualifier}")
    return result


def _signed_decimal(value: object, *, field: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Invalid {field}: {value!r}") from exc
    if not result.is_finite():
        raise ValueError(f"{field} must be finite")
    return result


def _is_true(value: object) -> bool:
    return str(value).lower() == "true"


@dataclass(frozen=True)
class AccountRiskLimits:
    enabled: bool
    max_order_notional_usd: Decimal
    max_total_exposure_usd: Decimal
    max_leverage: Decimal
    max_stop_loss_equity_pct: Decimal
    allowed_instruments: tuple[str, ...] = ()
    entries_enabled: bool = False
    created_at: str = ""
    updated_at: str = ""

    def validate(self) -> None:
        order_limit = _decimal(
            self.max_order_notional_usd, field="max_order_notional_usd"
        )
        total_limit = _decimal(
            self.max_total_exposure_usd, field="max_total_exposure_usd"
        )
        leverage = _decimal(self.max_leverage, field="max_leverage")
        stop_loss_budget = _decimal(
            self.max_stop_loss_equity_pct, field="max_stop_loss_equity_pct"
        )
        if order_limit > total_limit:
            raise ValueError(
                "max_order_notional_usd cannot exceed max_total_exposure_usd"
            )
        if leverage > Decimal("125"):
            raise ValueError("max_leverage cannot exceed 125")
        if stop_loss_budget > Decimal("100"):
            raise ValueError("max_stop_loss_equity_pct cannot exceed 100")
        instruments = tuple(dict.fromkeys(self.allowed_instruments))
        if self.enabled and not instruments:
            raise ValueError("allowed_instruments must not be empty when enabled")
        for inst_id in instruments:
            if not inst_id or not inst_id.endswith("-SWAP"):
                raise ValueError(f"invalid allowed instrument: {inst_id!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "max_order_notional_usd": float(self.max_order_notional_usd),
            "max_total_exposure_usd": float(self.max_total_exposure_usd),
            "max_leverage": float(self.max_leverage),
            "max_stop_loss_equity_pct": float(self.max_stop_loss_equity_pct),
            "allowed_instruments": list(self.allowed_instruments),
            "entries_enabled": self.entries_enabled,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class AccountRiskStore:
    """SQLite persistence for the single operator account risk envelope."""

    def __init__(self, db_path: str | None = None, *, initialize: bool = True) -> None:
        self.db_path = db_path or settings.MAYBECH_DB_PATH
        if initialize and not sqlite_read_only():
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
            self._init_db()

    def _init_db(self) -> None:
        with self._conn() as conn:
            initialize_schema(
                conn,
                schema_sql=_SCHEMA,
                component=_SCHEMA_COMPONENT,
                version=1,
            )
            versions = applied_schema_versions(conn, component=_SCHEMA_COMPONENT)
            if 2 not in versions:
                conn.executescript(_SCHEMA_V2)
                conn.execute(
                    """INSERT OR IGNORE INTO entry_control
                       (id, entries_enabled, updated_at)
                       VALUES ('account', 0, ?)""",
                    (datetime.now(timezone.utc).isoformat(),),
                )
                record_schema_version(
                    conn,
                    component=_SCHEMA_COMPONENT,
                    version=2,
                )
            versions = applied_schema_versions(conn, component=_SCHEMA_COMPONENT)
            if 3 not in versions:
                conn.executescript(_SCHEMA_V3)
                record_schema_version(conn, component=_SCHEMA_COMPONENT, version=3)
            versions = applied_schema_versions(conn, component=_SCHEMA_COMPONENT)
            if 4 not in versions:
                conn.executescript(_SCHEMA_V4)
                record_schema_version(conn, component=_SCHEMA_COMPONENT, version=4)

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

    def get(
        self,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> AccountRiskLimits | None:
        def read(conn: sqlite3.Connection) -> tuple[sqlite3.Row | None, sqlite3.Row | None]:
            row = conn.execute(
                "SELECT * FROM account_risk_limits WHERE id = 'account'"
            ).fetchone()
            control = conn.execute(
                "SELECT entries_enabled FROM entry_control WHERE id = 'account'"
            ).fetchone()
            return row, control
        if connection is not None:
            try:
                row, control = read(connection)
            except sqlite3.OperationalError as exc:
                if "no such table" not in str(exc):
                    raise
                return None
        else:
            with self._conn() as conn:
                try:
                    row, control = read(conn)
                except sqlite3.OperationalError as exc:
                    if "no such table" not in str(exc):
                        raise
                    return None
        if row is None:
            return None
        return AccountRiskLimits(
            enabled=bool(row["enabled"]),
            max_order_notional_usd=_decimal(
                row["max_order_notional_usd"], field="max_order_notional_usd"
            ),
            max_total_exposure_usd=_decimal(
                row["max_total_exposure_usd"], field="max_total_exposure_usd"
            ),
            max_leverage=_decimal(row["max_leverage"], field="max_leverage"),
            # allow_zero: a pre-v4 row must stay readable; validate() still
            # rejects it so entries and preflight remain blocked until set.
            max_stop_loss_equity_pct=_decimal(
                row["max_stop_loss_equity_pct"],
                field="max_stop_loss_equity_pct",
                allow_zero=True,
            ),
            allowed_instruments=tuple(json.loads(row["allowed_instruments_json"])),
            entries_enabled=bool(control["entries_enabled"]) if control else False,
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    def applied_schema_versions(self) -> list[int]:
        with self._conn() as conn:
            return applied_schema_versions(conn, component=_SCHEMA_COMPONENT)

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Connection, None, None]:
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            yield conn

    def save(
        self,
        limits: AccountRiskLimits,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> AccountRiskLimits:
        limits.validate()
        now = datetime.now(timezone.utc).isoformat()
        created_at = limits.created_at or now
        def persist(conn: sqlite3.Connection) -> AccountRiskLimits:
            conn.execute(
                """INSERT INTO account_risk_limits
                   (id, enabled, max_order_notional_usd,
                    max_total_exposure_usd, max_leverage, max_stop_loss_equity_pct,
                    allowed_instruments_json, created_at, updated_at)
                   VALUES ('account', ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                    enabled = excluded.enabled,
                    max_order_notional_usd = excluded.max_order_notional_usd,
                    max_total_exposure_usd = excluded.max_total_exposure_usd,
                    max_leverage = excluded.max_leverage,
                    max_stop_loss_equity_pct = excluded.max_stop_loss_equity_pct,
                    allowed_instruments_json = excluded.allowed_instruments_json,
                    updated_at = excluded.updated_at""",
                (
                    1 if limits.enabled else 0,
                    str(limits.max_order_notional_usd),
                    str(limits.max_total_exposure_usd),
                    str(limits.max_leverage),
                    str(limits.max_stop_loss_equity_pct),
                    json.dumps(list(dict.fromkeys(limits.allowed_instruments))),
                    created_at,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM account_risk_limits WHERE id = 'account'"
            ).fetchone()
            control = conn.execute(
                "SELECT entries_enabled FROM entry_control WHERE id = 'account'"
            ).fetchone()
            if row is None:
                raise RuntimeError("Account risk limits were not persisted")
            return AccountRiskLimits(
                enabled=bool(row["enabled"]),
                max_order_notional_usd=_decimal(
                    row["max_order_notional_usd"], field="max_order_notional_usd"
                ),
                max_total_exposure_usd=_decimal(
                    row["max_total_exposure_usd"], field="max_total_exposure_usd"
                ),
                max_leverage=_decimal(row["max_leverage"], field="max_leverage"),
                max_stop_loss_equity_pct=_decimal(
                    row["max_stop_loss_equity_pct"],
                    field="max_stop_loss_equity_pct",
                    allow_zero=True,
                ),
                allowed_instruments=tuple(json.loads(row["allowed_instruments_json"])),
                entries_enabled=bool(control["entries_enabled"]) if control else False,
                created_at=str(row["created_at"]),
                updated_at=str(row["updated_at"]),
            )
        if connection is not None:
            saved = persist(connection)
        else:
            with self._conn() as conn:
                saved = persist(conn)
        if saved is None:
            raise RuntimeError("Account risk limits were not persisted")
        return saved

    def set_entries_enabled(self, enabled: bool) -> AccountRiskLimits | None:
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO entry_control (id, entries_enabled, updated_at)
                   VALUES ('account', ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                    entries_enabled = excluded.entries_enabled,
                    updated_at = excluded.updated_at""",
                (1 if enabled else 0, now),
            )
        return self.get()

    def entries_enabled(self) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT entries_enabled FROM entry_control WHERE id = 'account'"
            ).fetchone()
        return bool(row["entries_enabled"]) if row else False


class EntryRiskBlocked(RuntimeError):
    """Raised when a live entry cannot prove it is inside the risk envelope."""


@dataclass(frozen=True)
class EntryRiskApproval:
    approval_id: str
    inst_id: str
    requested_size: Decimal
    entry_price: Decimal
    stop_loss_price: Decimal
    order_notional_usd: Decimal
    existing_exposure_usd: Decimal
    projected_exposure_usd: Decimal
    worst_case_loss_usd: Decimal
    existing_worst_case_loss_usd: Decimal
    projected_worst_case_loss_usd: Decimal
    equity_usd: Decimal
    leverage: Decimal
    approved_at: str
    dry_run: bool = False

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        for key, value in list(payload.items()):
            if isinstance(value, Decimal):
                payload[key] = float(value)
        return payload

    def matches(
        self,
        *,
        inst_id: str,
        requested_size: object,
        entry_price: object,
        stop_loss_price: object,
    ) -> bool:
        return (
            self.inst_id == inst_id
            and self.requested_size == _decimal(requested_size, field="requested_size")
            and self.entry_price == _decimal(entry_price, field="entry_price")
            and self.stop_loss_price
            == _decimal(stop_loss_price, field="stop_loss_price")
        )


class AccountRiskGuard:
    """Approve a live entry only from fresh authenticated OKX account state."""

    def __init__(
        self,
        client: Any,
        store: AccountRiskStore,
        position_store: LogicalPositionStore | None = None,
    ) -> None:
        self.client = client
        self.store = store
        self.position_store = position_store or LogicalPositionStore(store.db_path)
        self._instruments: dict[str, dict[str, Any]] = {}

    def approve_entry(
        self,
        *,
        inst_id: str,
        side: str,
        requested_size: object,
        entry_price: object,
        stop_loss_price: object,
    ) -> EntryRiskApproval:
        limits = self.store.get()
        if limits is None:
            raise EntryRiskBlocked("account risk limits are not configured")
        if not limits.enabled:
            raise EntryRiskBlocked("account risk limits are disabled")
        if not limits.entries_enabled:
            raise EntryRiskBlocked("strategy entries are disabled by the operator")
        limits.validate()

        if inst_id not in limits.allowed_instruments:
            raise EntryRiskBlocked(
                f"instrument {inst_id} is outside the account risk allowlist"
            )

        size = _decimal(requested_size, field="requested_size")
        price = _decimal(entry_price, field="entry_price")
        normalized_side = str(side).lower()
        if normalized_side not in {"long", "short"}:
            raise EntryRiskBlocked("entry side must be 'long' or 'short'")
        stop_price = _decimal(stop_loss_price, field="stop_loss_price")
        if normalized_side == "long" and stop_price >= price:
            raise EntryRiskBlocked(
                "entry requires a stop loss below the long entry price"
            )
        if normalized_side == "short" and stop_price <= price:
            raise EntryRiskBlocked(
                "entry requires a stop loss above the short entry price"
            )
        instrument = self._instrument(inst_id)
        contract_value = _decimal(instrument.get("ctVal"), field="ctVal")
        worst_case_loss = abs(price - stop_price) * size * contract_value
        order_notional = self._contract_notional(
            instrument=instrument,
            contracts=size,
            price=price,
        )
        if order_notional > limits.max_order_notional_usd:
            raise EntryRiskBlocked(
                f"order notional {order_notional} USD exceeds limit "
                f"{limits.max_order_notional_usd} USD"
            )

        leverage = self._leverage(inst_id)
        if leverage > limits.max_leverage:
            raise EntryRiskBlocked(
                f"configured leverage {leverage} exceeds limit {limits.max_leverage}"
            )

        exchange_positions = self.client.get_positions(inst_type="SWAP")
        logical_positions = self.position_store.list_active()
        reconciliation = PositionReconciler().reconcile_account(
            logical_positions=logical_positions,
            exchange_positions=exchange_positions,
        )
        if not reconciliation.safe_for_entries:
            raise EntryRiskBlocked(
                "OKX exposure does not reconcile to logical positions: "
                f"{reconciliation.state}"
            )
        existing_worst_case_loss = self._verify_owned_protection(
            logical_positions,
            mark_prices=self._mark_prices(exchange_positions),
        )

        # The primary envelope invariant: if every stop (existing units plus
        # this candidate) fires, the combined loss must fit the equity budget.
        equity = self._account_equity()
        projected_worst_case_loss = existing_worst_case_loss + worst_case_loss
        loss_budget = equity * limits.max_stop_loss_equity_pct / Decimal("100")
        if projected_worst_case_loss > loss_budget:
            raise EntryRiskBlocked(
                f"projected all-stop loss {projected_worst_case_loss} USD exceeds "
                f"{limits.max_stop_loss_equity_pct}% of equity {equity} USD "
                f"(budget {loss_budget} USD)"
            )

        existing_exposure = (
            self._position_exposure(exchange_positions)
            + self._pending_order_exposure()
        )
        projected_exposure = existing_exposure + order_notional
        if projected_exposure > limits.max_total_exposure_usd:
            raise EntryRiskBlocked(
                f"projected exposure {projected_exposure} USD exceeds limit "
                f"{limits.max_total_exposure_usd} USD"
            )

        return EntryRiskApproval(
            approval_id=uuid4().hex,
            inst_id=inst_id,
            requested_size=size,
            entry_price=price,
            stop_loss_price=stop_price,
            order_notional_usd=order_notional,
            existing_exposure_usd=existing_exposure,
            projected_exposure_usd=projected_exposure,
            worst_case_loss_usd=worst_case_loss,
            existing_worst_case_loss_usd=existing_worst_case_loss,
            projected_worst_case_loss_usd=projected_worst_case_loss,
            equity_usd=equity,
            leverage=leverage,
            approved_at=datetime.now(timezone.utc).isoformat(),
        )

    def _verify_owned_protection(
        self,
        positions: list[Any],
        *,
        mark_prices: dict[str, Decimal],
    ) -> Decimal:
        """Verify every open unit's exchange stop and return their combined
        worst-case loss in USD (mark price to stop trigger, per unit)."""
        pending_by_instrument: dict[str, list[dict[str, Any]]] = {}
        worst_case_loss = Decimal("0")
        for position in positions:
            remaining = position.remaining_quantity or position.opened_quantity or 0.0
            if remaining <= 0:
                continue
            protection = self.position_store.get_protection(position.id)
            if protection is None:
                raise EntryRiskBlocked(
                    f"logical position {position.id} has no owned exchange protection"
                )
            if protection.status != "active":
                raise EntryRiskBlocked(
                    f"logical position {position.id} protection is {protection.status}"
                )
            try:
                quantity_matches = Decimal(str(protection.quantity)) == Decimal(
                    str(remaining)
                )
            except (InvalidOperation, ValueError):
                quantity_matches = False
            if not quantity_matches:
                raise EntryRiskBlocked(
                    "logical position "
                    f"{position.id} protection quantity does not match remaining quantity"
                )
            pending = pending_by_instrument.get(position.inst_id)
            if pending is None:
                pending = self._pending_protections(position.inst_id)
                pending_by_instrument[position.inst_id] = pending
            matches = [
                order
                for order in pending
                if str(order.get("algoId") or "") == protection.algo_id
                and str(order.get("algoClOrdId") or "")
                == protection.algo_client_order_id
            ]
            expected_side = "sell" if position.side == "long" else "buy"
            active = len(matches) == 1 and self._protection_matches(
                matches[0],
                position=position,
                expected_side=expected_side,
                proof={
                    "quantity": protection.quantity,
                    "stop_loss": protection.stop_loss,
                    "kind": protection.kind,
                },
            )
            if not active:
                raise EntryRiskBlocked(
                    f"exchange protection is not active for logical position {position.id}"
                )
            mark = mark_prices.get(position.inst_id)
            if mark is None:
                raise EntryRiskBlocked(
                    f"OKX reported no mark price for {position.inst_id}; "
                    "cannot bound the all-stop loss"
                )
            stop = _decimal(protection.stop_loss, field="protection stop_loss")
            quantity = _decimal(remaining, field="remaining quantity")
            unit_contract_value = _decimal(
                self._instrument(position.inst_id).get("ctVal"), field="ctVal"
            )
            signed_distance = (
                mark - stop if position.side == "long" else stop - mark
            )
            # A stop already past the mark (about to trigger, or locking in
            # profit) contributes no further loss — but never a credit that
            # could offset another unit's risk.
            worst_case_loss += (
                max(Decimal("0"), signed_distance) * quantity * unit_contract_value
            )
        return worst_case_loss

    def _account_equity(self) -> Decimal:
        rows = self.client.get_balance()
        if not rows:
            raise EntryRiskBlocked("OKX returned no account balance")
        try:
            return _decimal(rows[0].get("totalEq"), field="account totalEq")
        except ValueError as exc:
            raise EntryRiskBlocked(str(exc)) from exc

    @staticmethod
    def _mark_prices(positions: list[dict[str, Any]]) -> dict[str, Decimal]:
        marks: dict[str, Decimal] = {}
        for position in positions:
            inst_id = str(position.get("instId") or "")
            value = position.get("markPx")
            if not inst_id or value in (None, ""):
                continue
            marks[inst_id] = _decimal(value, field=f"{inst_id} markPx")
        return marks

    @staticmethod
    def _protection_matches(
        order: dict[str, Any],
        *,
        position: Any,
        expected_side: str,
        proof: dict[str, Any],
    ) -> bool:
        try:
            size_matches = Decimal(str(order.get("sz"))) == Decimal(
                str(proof.get("quantity"))
            )
            stop_matches = Decimal(str(order.get("slTriggerPx"))) == Decimal(
                str(proof.get("stop_loss"))
            )
        except (InvalidOperation, ValueError):
            return False
        return all(
            (
                str(order.get("instId") or "") == position.inst_id,
                str(order.get("side") or "").lower() == expected_side,
                str(order.get("ordType") or "").lower()
                in (
                    {"conditional"}
                    if proof.get("kind") == "standalone_stop"
                    else {"conditional", "oco"}
                ),
                str(order.get("state") or "").lower() == "live",
                str(order.get("posSide") or "").lower() == "net",
                _is_true(order.get("reduceOnly")),
                size_matches,
                stop_matches,
                str(order.get("slOrdPx") or "") == "-1",
            )
        )

    def _pending_protections(self, inst_id: str) -> list[dict[str, Any]]:
        try:
            orders = [
                *self.client.get_pending_algo_orders(
                    inst_id=inst_id,
                    ord_type="conditional",
                ),
                *self.client.get_pending_algo_orders(inst_id=inst_id, ord_type="oco"),
            ]
        except TypeError:
            orders = self.client.get_pending_algo_orders(inst_id=inst_id)
        unique: dict[str, dict[str, Any]] = {}
        for order in orders:
            key = str(order.get("algoId") or order.get("algoClOrdId") or id(order))
            unique[key] = order
        return list(unique.values())

    def _instrument(self, inst_id: str) -> dict[str, Any]:
        cached = self._instruments.get(inst_id)
        if cached is not None:
            return cached
        payloads = self.client.get_instruments(inst_type="SWAP", inst_id=inst_id)
        if len(payloads) != 1:
            raise EntryRiskBlocked(
                f"expected one SWAP instrument for {inst_id}, got {len(payloads)}"
            )
        payload = payloads[0]
        if str(payload.get("instId") or "") != inst_id:
            raise EntryRiskBlocked(f"instrument metadata mismatch for {inst_id}")
        if str(payload.get("state") or "") != "live":
            raise EntryRiskBlocked(f"instrument {inst_id} is not live")
        if str(payload.get("ctType") or "") != "linear":
            raise EntryRiskBlocked(f"instrument {inst_id} must be a linear contract")
        if str(payload.get("settleCcy") or "") != "USDT":
            raise EntryRiskBlocked(f"instrument {inst_id} must settle in USDT")
        _decimal(payload.get("ctVal"), field=f"{inst_id} ctVal")
        self._instruments[inst_id] = payload
        return payload

    def _contract_notional(
        self,
        *,
        instrument: dict[str, Any],
        contracts: Decimal,
        price: Decimal,
    ) -> Decimal:
        contract_value = _decimal(instrument.get("ctVal"), field="ctVal")
        return contracts * contract_value * price

    def _leverage(self, inst_id: str) -> Decimal:
        rows = self.client.get_leverage(inst_id=inst_id, mgn_mode="cross")
        matching = [
            row
            for row in rows
            if str(row.get("instId") or "") == inst_id
            and str(row.get("mgnMode") or "") == "cross"
        ]
        if not matching:
            raise EntryRiskBlocked(f"no cross leverage returned for {inst_id}")
        return max(_decimal(row.get("lever"), field="lever") for row in matching)

    def _position_exposure(self, positions: list[dict[str, Any]]) -> Decimal:
        exposure = Decimal("0")
        for position in positions:
            size = _signed_decimal(position.get("pos") or "0", field="position size")
            if size == 0:
                continue
            notional = position.get("notionalUsd")
            if notional in (None, ""):
                raise EntryRiskBlocked(
                    f"position {position.get('instId') or 'unknown'} has no notionalUsd"
                )
            exposure += abs(_signed_decimal(notional, field="position notional"))
        return exposure

    def _pending_order_exposure(self) -> Decimal:
        exposure = Decimal("0")
        for order in self.client.get_pending_orders(inst_type="SWAP"):
            if _is_true(order.get("reduceOnly")):
                continue
            size = _decimal(order.get("sz"), field="pending order size")
            filled = _decimal(
                order.get("accFillSz") or "0",
                field="pending filled size",
                allow_zero=True,
            )
            remaining = size - filled
            if remaining <= 0:
                continue
            inst_id = str(order.get("instId") or "")
            price_value = order.get("px") or order.get("avgPx")
            if not inst_id or price_value in (None, ""):
                raise EntryRiskBlocked("pending entry order lacks instrument or price")
            exposure += self._contract_notional(
                instrument=self._instrument(inst_id),
                contracts=remaining,
                price=_decimal(price_value, field="pending order price"),
            )
        return exposure
