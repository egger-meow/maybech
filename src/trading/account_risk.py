"""Persisted account risk limits and fail-closed live entry approval."""

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
    configure_connection,
    initialize_schema,
    record_schema_version,
)


_SCHEMA_COMPONENT = "account_risk"
_SCHEMA_VERSION = 2
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
        if order_limit > total_limit:
            raise ValueError(
                "max_order_notional_usd cannot exceed max_total_exposure_usd"
            )
        if leverage > Decimal("125"):
            raise ValueError("max_leverage cannot exceed 125")

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "max_order_notional_usd": float(self.max_order_notional_usd),
            "max_total_exposure_usd": float(self.max_total_exposure_usd),
            "max_leverage": float(self.max_leverage),
            "entries_enabled": self.entries_enabled,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class AccountRiskStore:
    """SQLite persistence for the single operator account risk envelope."""

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path or settings.MAYBECH_DB_PATH
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
            if _SCHEMA_VERSION not in versions:
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
                    version=_SCHEMA_VERSION,
                )

    @contextmanager
    def _conn(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(self.db_path)
        configure_connection(conn)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def get(self) -> AccountRiskLimits | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM account_risk_limits WHERE id = 'account'"
            ).fetchone()
            control = conn.execute(
                "SELECT entries_enabled FROM entry_control WHERE id = 'account'"
            ).fetchone()
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
            entries_enabled=bool(control["entries_enabled"]) if control else False,
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    def applied_schema_versions(self) -> list[int]:
        with self._conn() as conn:
            return applied_schema_versions(conn, component=_SCHEMA_COMPONENT)

    def save(self, limits: AccountRiskLimits) -> AccountRiskLimits:
        limits.validate()
        now = datetime.now(timezone.utc).isoformat()
        created_at = limits.created_at or now
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO account_risk_limits
                   (id, enabled, max_order_notional_usd,
                    max_total_exposure_usd, max_leverage, created_at, updated_at)
                   VALUES ('account', ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                    enabled = excluded.enabled,
                    max_order_notional_usd = excluded.max_order_notional_usd,
                    max_total_exposure_usd = excluded.max_total_exposure_usd,
                    max_leverage = excluded.max_leverage,
                    updated_at = excluded.updated_at""",
                (
                    1 if limits.enabled else 0,
                    str(limits.max_order_notional_usd),
                    str(limits.max_total_exposure_usd),
                    str(limits.max_leverage),
                    created_at,
                    now,
                ),
            )
        saved = self.get()
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
    order_notional_usd: Decimal
    existing_exposure_usd: Decimal
    projected_exposure_usd: Decimal
    leverage: Decimal
    approved_at: str
    dry_run: bool = False

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        for key, value in list(payload.items()):
            if isinstance(value, Decimal):
                payload[key] = float(value)
        return payload

    def matches(self, *, inst_id: str, requested_size: object, entry_price: object) -> bool:
        return (
            self.inst_id == inst_id
            and self.requested_size == _decimal(requested_size, field="requested_size")
            and self.entry_price == _decimal(entry_price, field="entry_price")
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
        requested_size: object,
        entry_price: object,
    ) -> EntryRiskApproval:
        limits = self.store.get()
        if limits is None:
            raise EntryRiskBlocked("account risk limits are not configured")
        if not limits.enabled:
            raise EntryRiskBlocked("account risk limits are disabled")
        if not limits.entries_enabled:
            raise EntryRiskBlocked("strategy entries are disabled by the operator")
        limits.validate()

        size = _decimal(requested_size, field="requested_size")
        price = _decimal(entry_price, field="entry_price")
        instrument = self._instrument(inst_id)
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
        self._verify_imported_protection(logical_positions)

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
            order_notional_usd=order_notional,
            existing_exposure_usd=existing_exposure,
            projected_exposure_usd=projected_exposure,
            leverage=leverage,
            approved_at=datetime.now(timezone.utc).isoformat(),
        )

    def _verify_imported_protection(self, positions: list[Any]) -> None:
        pending_by_instrument: dict[str, list[dict[str, Any]]] = {}
        for position in positions:
            if position.source not in {"import", "recovery"}:
                continue
            try:
                metadata = json.loads(position.metadata_json or "{}")
            except json.JSONDecodeError as exc:
                raise EntryRiskBlocked(
                    f"logical position {position.id} has invalid protection metadata"
                ) from exc
            proof = metadata.get("exchange_protection") if isinstance(metadata, dict) else None
            if not isinstance(proof, dict):
                raise EntryRiskBlocked(
                    f"logical position {position.id} has no exchange protection proof"
                )
            pending = pending_by_instrument.get(position.inst_id)
            if pending is None:
                pending = self.client.get_pending_algo_orders(inst_id=position.inst_id)
                pending_by_instrument[position.inst_id] = pending
            algo_id = str(proof.get("algo_id") or "")
            algo_client_id = str(proof.get("algo_client_order_id") or "")
            matches = [
                order
                for order in pending
                if str(order.get("algoId") or "") == algo_id
                and str(order.get("algoClOrdId") or "") == algo_client_id
            ]
            expected_side = "sell" if position.side == "long" else "buy"
            active = len(matches) == 1 and self._protection_matches(
                matches[0],
                position=position,
                expected_side=expected_side,
                proof=proof,
            )
            if not active:
                raise EntryRiskBlocked(
                    f"exchange protection is not active for logical position {position.id}"
                )

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
                str(order.get("ordType") or "").lower() == "conditional",
                str(order.get("state") or "").lower() == "live",
                str(order.get("posSide") or "").lower() == "net",
                _is_true(order.get("reduceOnly")),
                size_matches,
                stop_matches,
                str(order.get("slOrdPx") or "") == "-1",
            )
        )

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
