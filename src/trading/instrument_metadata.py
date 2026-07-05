"""Persisted OKX instrument metadata used for selection and unit conversion."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Generator, Iterable

from src.config.settings import settings
from src.trading.instrument_constraints import InstrumentConstraints
from src.trading.sqlite_schema import applied_schema_versions, connect_database, configure_connection, initialize_schema, sqlite_read_only

_SCHEMA_COMPONENT = "instrument_metadata"
_SCHEMA_VERSION = 1
_SCHEMA = """
CREATE TABLE IF NOT EXISTS instrument_metadata (
    inst_id TEXT PRIMARY KEY, inst_type TEXT NOT NULL, state TEXT NOT NULL,
    base_ccy TEXT NOT NULL, quote_ccy TEXT NOT NULL, settle_ccy TEXT NOT NULL,
    contract_type TEXT NOT NULL, contract_value TEXT NOT NULL,
    contract_currency TEXT NOT NULL, contract_multiplier TEXT NOT NULL,
    lot_size TEXT NOT NULL, min_size TEXT NOT NULL, tick_size TEXT NOT NULL,
    size_precision INTEGER NOT NULL, price_precision INTEGER NOT NULL,
    max_limit_size TEXT NOT NULL, max_market_size TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_instrument_metadata_type_state
ON instrument_metadata(inst_type, state, inst_id);
CREATE TABLE IF NOT EXISTS instrument_metadata_rejections (
    inst_type TEXT NOT NULL, rejection_key TEXT NOT NULL, inst_id TEXT NOT NULL,
    error TEXT NOT NULL, payload_json TEXT NOT NULL, updated_at TEXT NOT NULL,
    PRIMARY KEY (inst_type, rejection_key)
);
CREATE INDEX IF NOT EXISTS idx_instrument_metadata_rejections_type
ON instrument_metadata_rejections(inst_type, updated_at, rejection_key);
"""


def _precision(value: object) -> int:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return 0
    return max(0, -number.normalize().as_tuple().exponent)


@dataclass(frozen=True)
class InstrumentMetadata:
    inst_id: str
    inst_type: str
    state: str
    base_ccy: str
    quote_ccy: str
    settle_ccy: str
    contract_type: str
    contract_value: str
    contract_currency: str
    contract_multiplier: str
    lot_size: str
    min_size: str
    tick_size: str
    size_precision: int
    price_precision: int
    max_limit_size: str
    max_market_size: str
    updated_at: str

    @classmethod
    def from_okx(cls, payload: dict[str, Any], *, updated_at: str) -> "InstrumentMetadata":
        inst_id = str(payload.get("instId") or "").strip()
        inst_type = str(payload.get("instType") or "").strip()
        if not inst_id or not inst_type:
            raise ValueError("OKX instrument metadata requires instId and instType")
        contract_value = str(payload.get("ctVal") or "").strip()
        contract_currency = str(payload.get("ctValCcy") or "").strip()
        lot_size = str(payload.get("lotSz") or "").strip()
        min_size = str(payload.get("minSz") or "").strip()
        tick_size = str(payload.get("tickSz") or "").strip()
        if not lot_size or not min_size or not tick_size:
            raise ValueError(f"{inst_id} metadata is missing lotSz, minSz, or tickSz")
        if inst_type == "SWAP" and (not contract_value or not contract_currency):
            raise ValueError(f"{inst_id} metadata is missing ctVal or ctValCcy")
        InstrumentConstraints.from_okx(payload)
        return cls(
            inst_id=inst_id, inst_type=inst_type, state=str(payload.get("state") or ""),
            base_ccy=str(payload.get("baseCcy") or ""), quote_ccy=str(payload.get("quoteCcy") or ""),
            settle_ccy=str(payload.get("settleCcy") or ""), contract_type=str(payload.get("ctType") or ""),
            contract_value=contract_value, contract_currency=contract_currency,
            contract_multiplier=str(payload.get("ctMult") or ""), lot_size=lot_size, min_size=min_size,
            tick_size=tick_size, size_precision=_precision(lot_size), price_precision=_precision(tick_size),
            max_limit_size=str(payload.get("maxLmtSz") or ""), max_market_size=str(payload.get("maxMktSz") or ""),
            updated_at=updated_at,
        )

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass(frozen=True)
class InstrumentMetadataRejection:
    inst_type: str
    rejection_key: str
    inst_id: str
    error: str
    payload: dict[str, Any]
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "inst_type": self.inst_type,
            "rejection_key": self.rejection_key,
            "inst_id": self.inst_id,
            "error": self.error,
            "payload": self.payload,
            "updated_at": self.updated_at,
        }


class InstrumentMetadataStore:
    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path or settings.MAYBECH_DB_PATH
        if not sqlite_read_only():
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
            with self._conn() as conn:
                initialize_schema(conn, schema_sql=_SCHEMA, component=_SCHEMA_COMPONENT, version=_SCHEMA_VERSION)

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

    def applied_schema_versions(self) -> list[int]:
        with self._conn() as conn:
            return applied_schema_versions(conn, component=_SCHEMA_COMPONENT)

    def replace_type(self, inst_type: str, payloads: Iterable[dict[str, Any]]) -> list[InstrumentMetadata]:
        normalized_type = inst_type.strip().upper()
        now = datetime.now(timezone.utc).isoformat()
        records: list[InstrumentMetadata] = []
        rejections: list[InstrumentMetadataRejection] = []
        seen_ids: set[str] = set()
        used_rejection_keys: set[str] = set()
        for index, payload in enumerate(payloads):
            inst_id = str(payload.get("instId") or "").strip().upper()
            rejection_key = inst_id or f"missing-inst-id:{index}"
            try:
                record = InstrumentMetadata.from_okx(payload, updated_at=now)
                if record.inst_type != normalized_type:
                    raise ValueError(
                        f"{record.inst_id} has unexpected instrument type {record.inst_type}"
                    )
                if record.inst_id in seen_ids:
                    raise ValueError(f"OKX returned duplicate instrument {record.inst_id}")
                seen_ids.add(record.inst_id)
                records.append(record)
            except (TypeError, ValueError) as exc:
                if rejection_key in used_rejection_keys:
                    rejection_key = f"{rejection_key}:{index}"
                used_rejection_keys.add(rejection_key)
                rejections.append(
                    InstrumentMetadataRejection(
                        inst_type=normalized_type,
                        rejection_key=rejection_key,
                        inst_id=inst_id,
                        error=str(exc),
                        payload=dict(payload),
                        updated_at=now,
                    )
                )

        with self._conn() as conn:
            if records:
                columns = tuple(records[0].to_dict())
                placeholders = ", ".join("?" for _ in columns)
                conn.execute(
                    "DELETE FROM instrument_metadata WHERE inst_type = ?",
                    (normalized_type,),
                )
                conn.executemany(
                    f"INSERT INTO instrument_metadata ({', '.join(columns)}) VALUES ({placeholders})",
                    [
                        tuple(record.to_dict()[column] for column in columns)
                        for record in records
                    ],
                )
            conn.execute(
                "DELETE FROM instrument_metadata_rejections WHERE inst_type = ?",
                (normalized_type,),
            )
            conn.executemany(
                """INSERT INTO instrument_metadata_rejections
                   (inst_type, rejection_key, inst_id, error, payload_json, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                [
                    (
                        rejection.inst_type,
                        rejection.rejection_key,
                        rejection.inst_id,
                        rejection.error,
                        json.dumps(rejection.payload, ensure_ascii=False, sort_keys=True),
                        rejection.updated_at,
                    )
                    for rejection in rejections
                ],
            )
        if not records:
            detail = (
                rejections[0].error
                if rejections
                else f"OKX returned no {normalized_type} instruments"
            )
            raise ValueError(
                f"OKX returned no valid {normalized_type} instruments; previous cache preserved: {detail}"
            )
        return self.list(inst_type=normalized_type)

    def list_rejections(
        self,
        *,
        inst_type: str = "SWAP",
    ) -> list[InstrumentMetadataRejection]:
        normalized_type = inst_type.strip().upper()
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT * FROM instrument_metadata_rejections
                   WHERE inst_type = ? ORDER BY rejection_key""",
                (normalized_type,),
            ).fetchall()
        return [
            InstrumentMetadataRejection(
                inst_type=str(row["inst_type"]),
                rejection_key=str(row["rejection_key"]),
                inst_id=str(row["inst_id"]),
                error=str(row["error"]),
                payload=json.loads(str(row["payload_json"])),
                updated_at=str(row["updated_at"]),
            )
            for row in rows
        ]

    def list(self, *, inst_type: str = "SWAP", tradable_only: bool = True) -> list[InstrumentMetadata]:
        clauses = ["inst_type = ?"]
        values: list[object] = [inst_type.strip().upper()]
        if tradable_only:
            clauses.append("state = 'live'")
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM instrument_metadata WHERE {' AND '.join(clauses)} ORDER BY inst_id", values
            ).fetchall()
        return [InstrumentMetadata(**dict(row)) for row in rows]

    def get(self, inst_id: str) -> InstrumentMetadata | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM instrument_metadata WHERE inst_id = ?",
                (inst_id.strip().upper(),),
            ).fetchone()
        return InstrumentMetadata(**dict(row)) if row is not None else None

    def cache_status(
        self,
        *,
        inst_type: str = "SWAP",
        max_age: timedelta = timedelta(days=1),
        now: datetime | None = None,
    ) -> dict[str, Any]:
        records = self.list(inst_type=inst_type, tradable_only=False)
        if not records:
            return {"stale": True, "refreshed_at": "", "refresh_due_at": ""}
        refreshed_at = max(record.updated_at for record in records)
        refreshed = datetime.fromisoformat(refreshed_at)
        if refreshed.tzinfo is None:
            refreshed = refreshed.replace(tzinfo=timezone.utc)
        due = refreshed + max_age
        current = now or datetime.now(timezone.utc)
        return {
            "stale": current >= due,
            "refreshed_at": refreshed_at,
            "refresh_due_at": due.isoformat(),
        }

    def refresh_if_stale(
        self,
        client: Any,
        *,
        inst_type: str = "SWAP",
        max_age: timedelta = timedelta(days=1),
        now: datetime | None = None,
    ) -> list[InstrumentMetadata]:
        if self.cache_status(inst_type=inst_type, max_age=max_age, now=now)["stale"]:
            return self.replace_type(
                inst_type,
                client.get_instruments(inst_type=inst_type),
            )
        return self.list(inst_type=inst_type)
