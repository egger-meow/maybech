"""Bounded, research-only support and resistance evidence from OHLCV candles."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
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


@dataclass(frozen=True)
class MarketWindow:
    inst_id: str
    bar: str


@dataclass
class CandleWindowState:
    fetched_at: datetime
    frame: pd.DataFrame
    coverage: int
    pivot_state: dict = field(default_factory=dict)


class SupportResistanceService:
    """Fetch and analyze a small candle window with a process-local TTL cache."""

    def __init__(
        self,
        client_provider: Callable[[], object],
        *,
        cache_ttl: timedelta = timedelta(seconds=15),
        incremental_fetch_limit: int = 32,
        max_market_windows: int = 32,
        max_state_candles: int = 300,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._client_provider = client_provider
        self._cache_ttl = cache_ttl
        if incremental_fetch_limit < 5:
            raise ValueError("incremental_fetch_limit must be at least 5")
        if max_market_windows < 1 or max_state_candles < 20:
            raise ValueError("market-state bounds are invalid")
        self._incremental_fetch_limit = incremental_fetch_limit
        self._max_market_windows = max_market_windows
        self._max_state_candles = max_state_candles
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._manager: CandleManager | None = None
        self._cache: dict[AnalysisRequest, tuple[datetime, dict]] = {}
        self._windows: OrderedDict[MarketWindow, CandleWindowState] = OrderedDict()
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

            window = MarketWindow(inst_id=inst_id, bar=bar)
            state = self._windows.get(window)
            can_reuse = (
                state is not None
                and state.coverage >= limit
                and now - state.fetched_at < self._cache_ttl
            )
            fetch_mode = "state_reuse" if can_reuse else "full"
            api_requested_candles = 0
            rescan_from: pd.Timestamp | None = None
            next_state = state
            try:
                if can_reuse:
                    frame = state.frame
                else:
                    incremental = state is not None and state.coverage >= limit
                    api_requested_candles = (
                        min(limit, self._incremental_fetch_limit) if incremental else limit
                    )
                    fetch_mode = "incremental_overlap" if incremental else "full"
                    fetched = self._manager.fetch(
                        inst_id, bar=bar, limit=api_requested_candles
                    )
                    if "timestamp" in fetched.columns and not fetched.empty:
                        recent_fetch = fetched.tail(api_requested_candles)
                        rescan_from = pd.to_datetime(
                            recent_fetch["timestamp"], utc=True, errors="coerce"
                        ).min()
                    frames = [state.frame, fetched] if state is not None else [fetched]
                    frame = pd.concat(frames, ignore_index=True)
                    if "timestamp" in frame.columns:
                        frame = frame.drop_duplicates(subset="timestamp", keep="last")
                        frame = frame.sort_values("timestamp")
                    frame = frame.tail(self._max_state_candles).reset_index(drop=True)
                    coverage = min(
                        self._max_state_candles,
                        max(limit, state.coverage if state is not None else 0),
                    )
                    next_state = CandleWindowState(
                        fetched_at=now,
                        frame=frame,
                        coverage=coverage,
                        pivot_state=(state.pivot_state if state is not None else {}),
                    )
            except Exception as exc:
                return _unavailable(inst_id, bar, now, str(exc))

            result = analyze_candles(
                frame.tail(limit),
                inst_id=inst_id,
                bar=bar,
                now=now,
                btc_regime=btc_regime,
                pivot_state=(next_state.pivot_state if next_state is not None else {}),
                rescan_from=rescan_from,
            )
            if next_state is not None:
                self._windows[window] = next_state
                self._windows.move_to_end(window)
                while len(self._windows) > self._max_market_windows:
                    self._windows.popitem(last=False)
            result["computation"] = {
                "fetch_mode": fetch_mode,
                "api_requested_candles": api_requested_candles,
                "state_candles": min(len(frame), self._max_state_candles),
                "state_capacity_candles": self._max_state_candles,
                "market_windows": len(self._windows),
                "market_window_capacity": self._max_market_windows,
                "calculation_mode": "incremental_pivot_scan",
                "analyzed_candles": min(len(frame), limit),
                "pivot_scan_candles": int(
                    (next_state.pivot_state if next_state is not None else {}).get(
                        "scanned_candles", min(len(frame), limit)
                    )
                ),
                "reused_pivots": int(
                    (next_state.pivot_state if next_state is not None else {}).get(
                        "reused_pivots", 0
                    )
                ),
            }
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
    pivot_state: dict | None = None,
    rescan_from: pd.Timestamp | None = None,
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
    pivot_bases, scanned_candles, reused_pivots = _discover_pivot_bases(
        data,
        pivot_window=pivot_window,
        prior=(pivot_state or {}).get("pivots"),
        rescan_from=rescan_from,
        interval=interval,
    )
    volume_mean = float(data["volume"].mean()) or 1.0
    pivots = [
        _enrich_pivot(
            pivot,
            latest_price=latest_price,
            atr=atr,
            now=evaluated_at,
            volume_mean=volume_mean,
        )
        for pivot in pivot_bases
    ]
    if pivot_state is not None:
        pivot_state.update({
            "pivots": pivot_bases,
            "scanned_candles": scanned_candles,
            "reused_pivots": reused_pivots,
        })

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


def _discover_pivot_bases(
    data: pd.DataFrame,
    *,
    pivot_window: int,
    prior: list[dict] | None,
    rescan_from: pd.Timestamp | None,
    interval: pd.Timedelta,
) -> tuple[list[dict], int, int]:
    if prior and rescan_from is None:
        return list(prior), 0, len(prior)
    cutoff = None
    retained: list[dict] = []
    start = pivot_window
    if prior and rescan_from is not None and not pd.isna(rescan_from):
        cutoff = rescan_from - interval * pivot_window
        retained = [
            item for item in prior
            if pd.Timestamp(item["timestamp"]) < cutoff
        ]
        candidates = data.index[data["timestamp"] >= cutoff].tolist()
        if candidates:
            start = max(pivot_window, candidates[0] - pivot_window)
    discovered: list[dict] = []
    stop = max(pivot_window, len(data) - pivot_window)
    for index in range(start, stop):
        row = data.iloc[index]
        neighborhood = data.iloc[index - pivot_window : index + pivot_window + 1]
        if float(row["low"]) <= float(neighborhood["low"].min()):
            discovered.append(_pivot_base(row, "support"))
        if float(row["high"]) >= float(neighborhood["high"].max()):
            discovered.append(_pivot_base(row, "resistance"))
    deduplicated = {
        (item["kind"], item["timestamp"]): item
        for item in [*retained, *discovered]
    }
    scanned = max(0, stop - start)
    return list(deduplicated.values()), scanned, len(retained)


def _pivot_base(row, kind: str) -> dict:
    price = float(row["low"] if kind == "support" else row["high"])
    candle_range = max(float(row["high"] - row["low"]), 1e-12)
    wick = (
        min(float(row["open"]), float(row["close"])) - float(row["low"])
        if kind == "support"
        else float(row["high"]) - max(float(row["open"]), float(row["close"]))
    )
    timestamp = row["timestamp"].to_pydatetime()
    return {
        "kind": kind,
        "price": price,
        "timestamp": timestamp.isoformat(),
        "volume": float(row["volume"]),
        "wick_ratio": max(0.0, wick / candle_range),
    }


def _enrich_pivot(
    pivot: dict,
    *,
    latest_price: float,
    atr: float,
    now: datetime,
    volume_mean: float,
) -> dict:
    timestamp = datetime.fromisoformat(str(pivot["timestamp"]).replace("Z", "+00:00"))
    price = float(pivot["price"])
    return {
        **pivot,
        "volume_ratio": float(pivot["volume"]) / volume_mean,
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
