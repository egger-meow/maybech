"""Per-instrument open-interest history (OKX public API, any swap instrument).

Separate from ``macro_overview.py``, which aggregates a fixed BTC/ETH
whole-market "vibe" snapshot — this fetches OKX's own historical
open-interest series for whichever instrument the operator picks. No
Maybech-side persistence is needed since OKX already serves real history
(unlike the current-snapshot-only ``GET /public/open-interest`` endpoint
the rest of this codebase uses).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

VALID_PERIODS: tuple[str, ...] = ("5m", "1H", "1D")
DEFAULT_PERIOD = "1H"


def parse_open_interest_history(raw_rows: list[Any]) -> list[dict]:
    """Parse OKX's ``[ts, oi, oiCcy, oiUsd]`` rows into ascending, typed points.

    Skips malformed rows rather than raising — one bad row shouldn't drop
    the rest of an otherwise-usable series.
    """
    points: list[dict] = []
    for row in raw_rows:
        if not isinstance(row, (list, tuple)) or len(row) < 4:
            continue
        try:
            ts_ms = int(row[0])
            oi_contracts = float(row[1])
            oi_ccy = float(row[2])
            oi_usd = float(row[3])
        except (TypeError, ValueError):
            continue
        points.append(
            {
                "observed_at": datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat(),
                "oi_contracts": oi_contracts,
                "oi_ccy": oi_ccy,
                "oi_usd": oi_usd,
            }
        )
    points.sort(key=lambda point: point["observed_at"])
    return points


def fetch_open_interest_history(
    client: Any, inst_id: str, *, period: str = DEFAULT_PERIOD, limit: int = 100
) -> list[dict]:
    raw_rows = client.get_open_interest_history(inst_id, period=period, limit=str(limit))
    return parse_open_interest_history(raw_rows)
