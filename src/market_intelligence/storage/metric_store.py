"""SQLite-backed store for metric observations and provider sync runs."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator

from src.config.settings import settings
from src.market_intelligence.models import MetricObservation, ProviderSyncRun
from src.trading.sqlite_schema import (
    applied_schema_versions,
    configure_connection,
    connect_database,
    initialize_schema,
    sqlite_read_only,
)

_SCHEMA_COMPONENT = "market_intelligence"
_SCHEMA_VERSION = 1
_SCHEMA = """
CREATE TABLE IF NOT EXISTS market_metric_observations (
    metric_id TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    value REAL NOT NULL,
    unit TEXT NOT NULL,
    source_provider TEXT NOT NULL,
    source_reference TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    quality TEXT NOT NULL,
    is_estimated INTEGER NOT NULL,
    methodology_version TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    PRIMARY KEY (metric_id, source_provider, observed_at)
);
CREATE INDEX IF NOT EXISTS idx_market_metric_observations_metric_time
ON market_metric_observations(metric_id, observed_at);

CREATE TABLE IF NOT EXISTS market_provider_sync_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider_id TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    status TEXT NOT NULL,
    records_fetched INTEGER NOT NULL,
    records_stored INTEGER NOT NULL,
    error_category TEXT,
    error_detail TEXT
);
CREATE INDEX IF NOT EXISTS idx_market_provider_sync_runs_provider_time
ON market_provider_sync_runs(provider_id, started_at DESC);
"""


class MetricStore:
    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path or settings.MAYBECH_DB_PATH
        if not sqlite_read_only():
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
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

    def applied_schema_versions(self) -> list[int]:
        with self._conn() as conn:
            return applied_schema_versions(conn, component=_SCHEMA_COMPONENT)

    def insert_observations(self, observations: list[MetricObservation]) -> int:
        """Idempotently insert observations; returns the count actually stored."""
        if not observations:
            return 0
        with self._conn() as conn:
            before = conn.total_changes
            conn.executemany(
                """INSERT OR IGNORE INTO market_metric_observations
                   (metric_id, observed_at, value, unit, source_provider, source_reference,
                    fetched_at, quality, is_estimated, methodology_version, metadata_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        obs.metric_id,
                        obs.observed_at,
                        obs.value,
                        obs.unit,
                        obs.source_provider,
                        obs.source_reference,
                        obs.fetched_at,
                        obs.quality,
                        int(obs.is_estimated),
                        obs.methodology_version,
                        json.dumps(obs.metadata, ensure_ascii=False, sort_keys=True),
                    )
                    for obs in observations
                ],
            )
            inserted = conn.total_changes - before
        return inserted

    @staticmethod
    def _row_to_observation(row: sqlite3.Row) -> MetricObservation:
        return MetricObservation(
            metric_id=str(row["metric_id"]),
            observed_at=str(row["observed_at"]),
            value=float(row["value"]),
            unit=str(row["unit"]),
            source_provider=str(row["source_provider"]),
            source_reference=str(row["source_reference"]),
            fetched_at=str(row["fetched_at"]),
            quality=str(row["quality"]),
            is_estimated=bool(row["is_estimated"]),
            methodology_version=str(row["methodology_version"]),
            metadata=json.loads(str(row["metadata_json"])),
        )

    def latest(self, metric_id: str) -> MetricObservation | None:
        with self._conn() as conn:
            row = conn.execute(
                """SELECT * FROM market_metric_observations
                   WHERE metric_id = ? ORDER BY observed_at DESC LIMIT 1""",
                (metric_id,),
            ).fetchone()
        return self._row_to_observation(row) if row is not None else None

    def history(
        self,
        metric_id: str,
        *,
        start: str | None = None,
        end: str | None = None,
        limit: int = 500,
    ) -> list[MetricObservation]:
        clauses = ["metric_id = ?"]
        values: list[Any] = [metric_id]
        if start is not None:
            clauses.append("observed_at >= ?")
            values.append(start)
        if end is not None:
            clauses.append("observed_at <= ?")
            values.append(end)
        bounded_limit = max(1, min(int(limit), 2000))
        with self._conn() as conn:
            rows = conn.execute(
                f"""SELECT * FROM market_metric_observations
                    WHERE {' AND '.join(clauses)}
                    ORDER BY observed_at ASC LIMIT ?""",
                (*values, bounded_limit),
            ).fetchall()
        return [self._row_to_observation(row) for row in rows]

    def record_sync_run(self, run: ProviderSyncRun) -> None:
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO market_provider_sync_runs
                   (provider_id, started_at, completed_at, status, records_fetched,
                    records_stored, error_category, error_detail)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run.provider_id,
                    run.started_at,
                    run.completed_at,
                    run.status,
                    run.records_fetched,
                    run.records_stored,
                    run.error_category,
                    run.error_detail,
                ),
            )

    @staticmethod
    def _row_to_run(row: sqlite3.Row) -> ProviderSyncRun:
        return ProviderSyncRun(
            provider_id=str(row["provider_id"]),
            started_at=str(row["started_at"]),
            completed_at=str(row["completed_at"]),
            status=str(row["status"]),
            records_fetched=int(row["records_fetched"]),
            records_stored=int(row["records_stored"]),
            error_category=row["error_category"],
            error_detail=row["error_detail"],
        )

    def latest_sync_run(self, provider_id: str) -> ProviderSyncRun | None:
        with self._conn() as conn:
            row = conn.execute(
                """SELECT * FROM market_provider_sync_runs
                   WHERE provider_id = ? ORDER BY started_at DESC, id DESC LIMIT 1""",
                (provider_id,),
            ).fetchone()
        return self._row_to_run(row) if row is not None else None

    def recent_sync_runs(self, provider_id: str, *, limit: int = 20) -> list[ProviderSyncRun]:
        bounded_limit = max(1, min(int(limit), 200))
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT * FROM market_provider_sync_runs
                   WHERE provider_id = ? ORDER BY started_at DESC, id DESC LIMIT ?""",
                (provider_id, bounded_limit),
            ).fetchall()
        return [self._row_to_run(row) for row in rows]

    def consecutive_failures(self, provider_id: str, *, lookback: int = 20) -> int:
        """Count failed runs since the most recent success, newest first.

        ``skipped`` runs (rate-limit gating, not an error) neither count as a
        failure nor reset the streak.
        """
        count = 0
        for run in self.recent_sync_runs(provider_id, limit=lookback):
            if run.status == "success":
                break
            if run.status == "failed":
                count += 1
        return count
