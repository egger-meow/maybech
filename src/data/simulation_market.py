"""Local-only market input used by Simulation runtime mode."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


class SimulationMarketClient:
    """Read OKX-shaped candle rows from a local replay file, never the network.

    The optional JSON file is selected with ``MAYBECH_SIMULATION_CANDLES`` and
    maps ``<instrument>:<bar>`` keys to newest-first OKX-shaped candle arrays.
    Missing data is represented honestly as an empty result.
    """

    flag = "simulation"

    def __init__(self, path: str | os.PathLike[str] | None = None) -> None:
        configured = path if path is not None else os.getenv("MAYBECH_SIMULATION_CANDLES", "")
        self.path = Path(configured).expanduser() if configured else None
        self._candles = self._load()

    def _load(self) -> dict[str, list[list[Any]]]:
        if self.path is None:
            return {}
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("simulation candle replay must be a JSON object")
        result: dict[str, list[list[Any]]] = {}
        for key, rows in payload.items():
            if not isinstance(key, str) or not isinstance(rows, list):
                raise ValueError("simulation candle replay entries must map strings to arrays")
            result[key] = rows
        return result

    def get_candles(self, inst_id: str, bar: str = "1m", limit: str = "100") -> list[list[Any]]:
        return list(self._candles.get(f"{inst_id}:{bar}", []))[: int(limit)]

    def get_history_candles(
        self,
        inst_id: str,
        bar: str = "1m",
        limit: str = "100",
        after: str = "",
        before: str = "",
    ) -> list[list[Any]]:
        rows = self.get_candles(inst_id, bar, limit)
        if after:
            rows = [row for row in rows if row and int(row[0]) < int(after)]
        if before:
            rows = [row for row in rows if row and int(row[0]) > int(before)]
        return rows[: int(limit)]
