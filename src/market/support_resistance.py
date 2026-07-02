"""Bounded, research-only support and resistance evidence from OHLCV candles."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from threading import RLock
from typing import Callable

import pandas as pd

from src.data.candles import CandleManager, _BAR_MS


@dataclass(frozen=True)
class AnalysisRequest:
    inst_id: str
    bar: str
    limit: int
    btc_direction: str


class SupportResistanceService:
    """Fetch and analyze a small candle window with a process-local TTL cache."""

    def __init__(
        self,
        client_provider: Callable[[], object],
        *,
        cache_ttl: timedelta = timedelta(seconds=15),
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._client_provider = client_provider
        self._cache_ttl = cache_ttl
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._manager: CandleManager | None = None
        self._cache: dict[AnalysisRequest, tuple[datetime, dict]] = {}
        self._lock = RLock()

    def analyze(
        self,
        inst_id: str,
        *,
        bar: str = "15m",
        limit: int = 200,
        btc_regime: dict | None = None,
    ) -> dict:
        btc_direction = str((btc_regime or {}).get("direction") or "unknown").lower()
        request = AnalysisRequest(
            inst_id=inst_id, bar=bar, limit=limit, btc_direction=btc_direction
        )
        now = self._now()
        with self._lock:
            cached = self._cache.get(request)
            if cached and now - cached[0] < self._cache_ttl:
                return {**cached[1], "cache_hit": True}
            if self._manager is None:
                self._manager = CandleManager(self._client_provider())

            try:
                frame = self._manager.fetch(inst_id, bar=bar, limit=limit)
            except Exception as exc:
                return _unavailable(inst_id, bar, now, str(exc))

            result = analyze_candles(
                frame.tail(limit),
                inst_id=inst_id,
                bar=bar,
                now=now,
                btc_regime=btc_regime,
            )
            self._cache[request] = (now, result)
            return result


def analyze_candles(
    frame: pd.DataFrame,
    *,
    inst_id: str,
    bar: str,
    now: datetime | None = None,
    pivot_window: int = 2,
    max_levels: int = 12,
    btc_regime: dict | None = None,
) -> dict:
    """Return explainable S/R evidence; never an executable trading rule."""
    evaluated_at = now or datetime.now(timezone.utc)
    interval_ms = _BAR_MS.get(bar)
    errors: list[str] = []
    if interval_ms is None:
        return _unavailable(inst_id, bar, evaluated_at, f"unsupported candle bar: {bar}")
    if frame.empty:
        return _unavailable(inst_id, bar, evaluated_at, "no candles returned")

    required = {"timestamp", "open", "high", "low", "close", "volume"}
    missing_columns = sorted(required.difference(frame.columns))
    if missing_columns:
        return _unavailable(
            inst_id, bar, evaluated_at, f"missing candle columns: {', '.join(missing_columns)}"
        )

    data = frame.copy()
    data["timestamp"] = pd.to_datetime(data["timestamp"], utc=True, errors="coerce")
    invalid_rows = int(data["timestamp"].isna().sum())
    data = data.dropna(subset=["timestamp"]).sort_values("timestamp")
    duplicate_count = int(data.duplicated(subset="timestamp", keep="last").sum())
    data = data.drop_duplicates(subset="timestamp", keep="last").reset_index(drop=True)
    if invalid_rows:
        errors.append(f"{invalid_rows} candles have invalid timestamps")
    if duplicate_count:
        errors.append(f"{duplicate_count} duplicate candle timestamps were ignored")
    if len(data) < pivot_window * 2 + 1:
        errors.append("insufficient candles for pivot analysis")

    interval = pd.Timedelta(milliseconds=interval_ms)
    deltas = data["timestamp"].diff().dropna()
    missing_candles = int(sum(max(0, round(delta / interval) - 1) for delta in deltas))
    if missing_candles:
        errors.append(f"{missing_candles} expected candles are missing")

    latest = data.iloc[-1]
    latest_at = latest["timestamp"].to_pydatetime()
    age_seconds = max(0.0, (evaluated_at - latest_at).total_seconds())
    stale_after_seconds = interval.total_seconds() * 2
    stale = age_seconds > stale_after_seconds
    if stale:
        errors.append("latest candle is stale")

    ranges = (data["high"] - data["low"]).abs()
    atr = float(ranges.tail(14).mean()) if len(data) else 0.0
    latest_price = float(latest["close"])
    cluster_distance = max(latest_price * 0.001, atr * 0.35)
    pivots: list[dict] = []
    for index in range(pivot_window, max(pivot_window, len(data) - pivot_window)):
        row = data.iloc[index]
        neighborhood = data.iloc[index - pivot_window : index + pivot_window + 1]
        if float(row["low"]) <= float(neighborhood["low"].min()):
            pivots.append(_pivot(row, "support", data, latest_price, atr, evaluated_at))
        if float(row["high"]) >= float(neighborhood["high"].max()):
            pivots.append(_pivot(row, "resistance", data, latest_price, atr, evaluated_at))

    btc_direction = str((btc_regime or {}).get("direction") or "unknown").lower()
    levels = _cluster_pivots(
        pivots,
        cluster_distance=cluster_distance,
        btc_direction=btc_direction,
        latest_price=latest_price,
    )
    levels.sort(key=lambda item: (-item["score"], abs(item["price"] - latest_price)))
    levels = levels[:max_levels]
    status = "fresh"
    if stale or errors or not levels:
        status = "partial"
    return {
        "inst_id": inst_id,
        "bar": bar,
        "status": status,
        "freshness": {
            "evaluated_at": evaluated_at.isoformat(),
            "latest_candle_at": latest_at.isoformat(),
            "age_seconds": age_seconds,
            "stale_after_seconds": stale_after_seconds,
            "stale": stale,
        },
        "quality": {
            "input_candles": len(frame),
            "usable_candles": len(data),
            "duplicate_candles": duplicate_count,
            "missing_candles": missing_candles,
            "invalid_candles": invalid_rows,
        },
        "latest_price": latest_price,
        "volatility_atr": atr,
        "levels": levels,
        "context": {
            "btc_regime": btc_regime or {},
            "btc_direction": btc_direction,
        },
        "errors": errors,
        "cache_hit": False,
        "research_only": True,
        "eligible_as_live_rule": False,
    }


def _pivot(row, kind: str, data: pd.DataFrame, latest_price: float, atr: float, now: datetime) -> dict:
    price = float(row["low"] if kind == "support" else row["high"])
    candle_range = max(float(row["high"] - row["low"]), 1e-12)
    wick = (
        min(float(row["open"]), float(row["close"])) - float(row["low"])
        if kind == "support"
        else float(row["high"]) - max(float(row["open"]), float(row["close"]))
    )
    volume_mean = float(data["volume"].mean()) or 1.0
    timestamp = row["timestamp"].to_pydatetime()
    return {
        "kind": kind,
        "price": price,
        "timestamp": timestamp.isoformat(),
        "volume_ratio": float(row["volume"]) / volume_mean,
        "wick_ratio": max(0.0, wick / candle_range),
        "age_seconds": max(0.0, (now - timestamp).total_seconds()),
        "invalidation_distance_pct": abs(latest_price - price) / latest_price if latest_price else 0.0,
        "volatility_distance_atr": abs(latest_price - price) / atr if atr else None,
    }


def _cluster_pivots(
    pivots: list[dict], *, cluster_distance: float, btc_direction: str, latest_price: float
) -> list[dict]:
    clusters: list[list[dict]] = []
    for pivot in sorted(pivots, key=lambda item: (item["kind"], item["price"])):
        matching = next(
            (
                cluster
                for cluster in clusters
                if cluster[0]["kind"] == pivot["kind"]
                and abs(sum(item["price"] for item in cluster) / len(cluster) - pivot["price"])
                <= cluster_distance
            ),
            None,
        )
        if matching is None:
            clusters.append([pivot])
        else:
            matching.append(pivot)

    results: list[dict] = []
    for cluster in clusters:
        touches = len(cluster)
        newest = min(cluster, key=lambda item: item["age_seconds"])
        volume_ratio = sum(item["volume_ratio"] for item in cluster) / touches
        wick_ratio = sum(item["wick_ratio"] for item in cluster) / touches
        regime_aligned = (
            (btc_direction == "bullish" and cluster[0]["kind"] == "support")
            or (btc_direction == "bearish" and cluster[0]["kind"] == "resistance")
        )
        regime_conflicts = (
            (btc_direction == "bearish" and cluster[0]["kind"] == "support")
            or (btc_direction == "bullish" and cluster[0]["kind"] == "resistance")
        )
        regime_adjustment = Decimal("0.05") if regime_aligned else Decimal("-0.05") if regime_conflicts else Decimal("0")
        base_score = 0.2 + min(touches, 5) * 0.1 + min(volume_ratio, 3) * 0.1 + wick_ratio * 0.2
        score = min(1.0, max(0.0, base_score + float(regime_adjustment)))
        level_price = sum(item["price"] for item in cluster) / touches
        invalidated = (
            (cluster[0]["kind"] == "support" and latest_price < level_price)
            or (cluster[0]["kind"] == "resistance" and latest_price > level_price)
        )
        if invalidated:
            score *= 0.25
        results.append(
            {
                "kind": cluster[0]["kind"],
                "price": level_price,
                "score": score,
                "state": "invalidated" if invalidated else "active",
                "touches": touches,
                "latest_touch_at": newest["timestamp"],
                "evidence": {
                    "volume_ratio": volume_ratio,
                    "wick_ratio": wick_ratio,
                    "recency_seconds": newest["age_seconds"],
                    "invalidation_distance_pct": newest["invalidation_distance_pct"],
                    "volatility_distance_atr": newest["volatility_distance_atr"],
                    "btc_direction": btc_direction,
                    "btc_regime_alignment": (
                        "aligned" if regime_aligned else "conflicting" if regime_conflicts else "neutral"
                    ),
                    "invalidation_rule": (
                        "close_below_level" if cluster[0]["kind"] == "support" else "close_above_level"
                    ),
                    "invalidated": invalidated,
                },
            }
        )
    return results


def _unavailable(inst_id: str, bar: str, now: datetime, error: str) -> dict:
    return {
        "inst_id": inst_id,
        "bar": bar,
        "status": "unavailable",
        "freshness": {
            "evaluated_at": now.isoformat(),
            "latest_candle_at": None,
            "age_seconds": None,
            "stale_after_seconds": None,
            "stale": True,
        },
        "quality": {
            "input_candles": 0,
            "usable_candles": 0,
            "duplicate_candles": 0,
            "missing_candles": 0,
            "invalid_candles": 0,
        },
        "latest_price": None,
        "volatility_atr": None,
        "levels": [],
        "context": {"btc_regime": {}, "btc_direction": "unknown"},
        "errors": [error],
        "cache_hit": False,
        "research_only": True,
        "eligible_as_live_rule": False,
    }
