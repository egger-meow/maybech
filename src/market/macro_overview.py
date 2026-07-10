"""Whole-market macro overview: free external indices plus OKX-native metrics.

Unlike ``src/market/support_resistance.py`` and ``btc_regime.py``, which analyze
one instrument's own candles, this module aggregates market-wide "vibe"
indicators for the dashboard: the Fear & Greed Index, the MVRV Z-Score, and an
open-interest-weighted BTC/ETH funding rate. The on-chain source
(bitcoin-data.com) enforces a shared 10-requests/hour limit with no API key,
so results are cached in-process well past that window; a stale cache entry is
served instead of failing outright if a refresh call errors.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Callable

import requests

logger = logging.getLogger(__name__)

_FEAR_GREED_URL = "https://api.alternative.me/fng/"
_MVRV_ZSCORE_URL = "https://bitcoin-data.com/v1/mvrv-zscore"
_COINGECKO_GLOBAL_URL = "https://api.coingecko.com/api/v3/global"

FEAR_GREED_TTL_SECONDS = 300.0
MVRV_TTL_SECONDS = 3600.0
GLOBAL_MARKET_TTL_SECONDS = 300.0

FUNDING_SYMBOLS: tuple[str, ...] = ("BTC-USDT-SWAP", "ETH-USDT-SWAP")

_cache: dict[str, tuple[float, Any]] = {}


def _cached(key: str, ttl_seconds: float, fetch: Callable[[], Any]) -> Any:
    """Return a cached value within ``ttl_seconds``, else refresh; serve stale on error."""
    now = time.monotonic()
    entry = _cache.get(key)
    if entry is not None and now - entry[0] < ttl_seconds:
        return entry[1]
    try:
        value = fetch()
    except Exception:
        if entry is not None:
            logger.warning("macro overview refresh failed for %s; serving stale cache", key)
            return entry[1]
        raise
    _cache[key] = (now, value)
    return value


def _fear_greed_date(raw: Any) -> str:
    try:
        return datetime.fromtimestamp(int(raw), tz=timezone.utc).date().isoformat()
    except (TypeError, ValueError):
        return ""


def fetch_fear_greed(history_days: int = 14) -> dict:
    """Fetch the current Fear & Greed Index plus recent history from alternative.me."""

    def _fetch() -> dict:
        resp = requests.get(_FEAR_GREED_URL, params={"limit": history_days, "format": "json"}, timeout=5)
        resp.raise_for_status()
        payload = resp.json()
        items = payload.get("data") or []
        history = [
            {
                "value": int(item["value"]),
                "classification": str(item.get("value_classification") or ""),
                "date": _fear_greed_date(item.get("timestamp")),
            }
            for item in items
            if isinstance(item, dict) and "value" in item
        ]
        history.reverse()  # alternative.me returns newest first; charts want oldest first
        return {"history": history, "latest": history[-1] if history else None}

    try:
        return _cached("fear_greed", FEAR_GREED_TTL_SECONDS, _fetch)
    except Exception as exc:  # noqa: BLE001 - external API, degrade gracefully
        logger.warning("Fear & Greed Index unavailable: %s", exc)
        return {"history": [], "latest": None, "unavailable_reason": f"Fear & Greed Index unavailable: {exc}"}


def classify_mvrv(value: float | None) -> str | None:
    """Bucket an MVRV Z-Score into a plain-language market-cycle label."""
    if value is None:
        return None
    if value < 0:
        return "undervalued"
    if value < 2:
        return "neutral"
    if value < 5:
        return "elevated"
    return "overheated"


def _coerce_float(raw: Any) -> float | None:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if value != value:  # NaN
        return None
    return value


def _mvrv_history_from_payload(payload: Any, history_days: int) -> list[dict]:
    raw_items = payload.get("value") if isinstance(payload, dict) and isinstance(payload.get("value"), list) else None
    if raw_items is None and isinstance(payload, list):
        raw_items = payload
    if raw_items is None:
        return []

    history: list[dict] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        value = _coerce_float(item.get("mvrvZscore"))
        date = str(item.get("d") or "")
        if value is None or not date:
            continue
        history.append({"value": value, "date": date})
    return history[-history_days:] if history_days > 0 else history


def fetch_mvrv_zscore(history_days: int = 180) -> dict:
    """Fetch BTC MVRV Z-Score history from bitcoin-data.com (BGeometrics)."""

    def _fetch() -> dict:
        resp = requests.get(_MVRV_ZSCORE_URL, timeout=5)
        resp.raise_for_status()
        payload = resp.json()
        history = _mvrv_history_from_payload(payload, history_days)
        if history:
            latest = history[-1]
            return {"value": latest["value"], "as_of": latest["date"], "history": history}
        if not isinstance(payload, dict):
            raise ValueError("unexpected MVRV payload")
        value = _coerce_float(payload.get("mvrvZscore"))
        if value is None:
            raise ValueError("missing MVRV Z-Score")
        return {"value": value, "as_of": str(payload.get("d") or ""), "history": []}

    try:
        result = _cached("mvrv_zscore", MVRV_TTL_SECONDS, _fetch)
        return {**result, "classification": classify_mvrv(result.get("value"))}
    except Exception as exc:  # noqa: BLE001 - external API, degrade gracefully
        logger.warning("MVRV Z-Score unavailable: %s", exc)
        return {
            "value": None,
            "as_of": None,
            "classification": None,
            "history": [],
            "unavailable_reason": f"MVRV Z-Score unavailable: {exc}",
        }


def fetch_global_market() -> dict:
    """Fetch free global crypto market totals and dominance from CoinGecko."""

    def _fetch() -> dict:
        resp = requests.get(_COINGECKO_GLOBAL_URL, timeout=5)
        resp.raise_for_status()
        data = (resp.json() or {}).get("data") or {}
        market_cap = data.get("total_market_cap") or {}
        volume = data.get("total_volume") or {}
        dominance = data.get("market_cap_percentage") or {}
        updated_at = data.get("updated_at")
        return {
            "total_market_cap_usd": _coerce_float(market_cap.get("usd")),
            "total_volume_usd": _coerce_float(volume.get("usd")),
            "market_cap_change_24h_pct": _coerce_float(data.get("market_cap_change_percentage_24h_usd")),
            "volume_change_24h_pct": _coerce_float(data.get("volume_change_percentage_24h_usd")),
            "btc_dominance_pct": _coerce_float(dominance.get("btc")),
            "eth_dominance_pct": _coerce_float(dominance.get("eth")),
            "active_cryptocurrencies": int(data.get("active_cryptocurrencies") or 0),
            "markets": int(data.get("markets") or 0),
            "updated_at": (
                datetime.fromtimestamp(int(updated_at), tz=timezone.utc).isoformat()
                if updated_at is not None
                else None
            ),
        }

    try:
        return _cached("global_market", GLOBAL_MARKET_TTL_SECONDS, _fetch)
    except Exception as exc:  # noqa: BLE001 - external API, degrade gracefully
        logger.warning("Global market overview unavailable: %s", exc)
        return {
            "total_market_cap_usd": None,
            "total_volume_usd": None,
            "market_cap_change_24h_pct": None,
            "volume_change_24h_pct": None,
            "btc_dominance_pct": None,
            "eth_dominance_pct": None,
            "active_cryptocurrencies": None,
            "markets": None,
            "updated_at": None,
            "unavailable_reason": f"Global market overview unavailable: {exc}",
        }


def fetch_prices(client: Any, symbols: tuple[str, ...] = FUNDING_SYMBOLS) -> list[dict]:
    """Fetch last price and 24h change for each symbol via the OKX ticker."""
    rows: list[dict] = []
    for symbol in symbols:
        try:
            ticker = client.get_ticker(symbol)
            last = float(ticker[0]["last"]) if ticker else None
            open_24h = float(ticker[0].get("open24h") or 0) if ticker else None
            change_pct = ((last - open_24h) / open_24h * 100) if last is not None and open_24h else None
        except Exception as exc:  # noqa: BLE001 - one bad symbol shouldn't blank the row
            logger.warning("price fetch failed for %s: %s", symbol, exc)
            last, change_pct = None, None
        rows.append({"symbol": symbol, "last_price": last, "change_24h_pct": change_pct})
    return rows


def fetch_funding_overview(client: Any, symbols: tuple[str, ...] = FUNDING_SYMBOLS) -> dict:
    """Fetch funding rate + open interest per symbol, plus an OI-weighted average rate."""
    entries: list[dict] = []
    weighted_numerator = 0.0
    weighted_denominator = 0.0
    for symbol in symbols:
        try:
            funding = client.get_funding_rate(symbol)
            rate = float(funding[0]["fundingRate"]) if funding else None
        except Exception as exc:  # noqa: BLE001
            logger.warning("funding rate fetch failed for %s: %s", symbol, exc)
            rate = None
        try:
            open_interest = client.get_open_interest(symbol)
            open_interest_ccy = float(open_interest[0]["oiCcy"]) if open_interest else None
        except Exception as exc:  # noqa: BLE001
            logger.warning("open interest fetch failed for %s: %s", symbol, exc)
            open_interest_ccy = None

        entries.append({"symbol": symbol, "funding_rate": rate, "open_interest_ccy": open_interest_ccy})
        if rate is not None and open_interest_ccy:
            weighted_numerator += rate * open_interest_ccy
            weighted_denominator += open_interest_ccy

    weighted_average = weighted_numerator / weighted_denominator if weighted_denominator else None
    if not entries or all(entry["funding_rate"] is None for entry in entries):
        return {
            "entries": entries,
            "weighted_average_funding_rate": None,
            "unavailable_reason": "OKX funding rate feed unavailable",
        }
    return {"entries": entries, "weighted_average_funding_rate": weighted_average}
