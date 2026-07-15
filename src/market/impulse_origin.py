"""Locate the 起漲點/起跌點 (impulse origin): the boundary candle where a
volume-burst, long-real-body move began, for use as a stop-loss anchor.

Mirrors `support_resistance.find_swing_level` in shape and contract (a
one-shot lookup meant to be materialized into an actual stop trigger), but
the qualifying condition here is deliberately narrower: a candle only
counts as an "impulse" if its volume AND its real body are both a marked
step up from the recent baseline. Neither alone is meaningful — a big
body on ordinary volume is just noise, and a volume spike on a small body
(a wick-heavy reversal candle) isn't a sustained move worth anchoring a
stop to.
"""

from __future__ import annotations

import pandas as pd

from src.data.candles import CandleManager


def find_impulse_origin(
    client: object,
    inst_id: str,
    *,
    bar: str,
    kind: str,
    nth: int = 1,
    min_volume_multiple: float = 2.0,
    min_body_ratio: float = 0.6,
    min_body_vs_baseline_multiple: float = 1.5,
    buffer_pct: float = 0.0,
    limit: int = 200,
    baseline_window: int = 20,
) -> dict:
    """Resolve the nth most-recent qualifying impulse candle's origin price.

    `kind="bullish"` looks for an up-burst candle (anchors a long stop_loss);
    `kind="bearish"` looks for a down-burst candle (anchors a short
    stop_loss). A candle qualifies only when, relative to the trailing
    `baseline_window` candles: its volume is at least `min_volume_multiple`
    times the baseline mean volume, its body occupies at least
    `min_body_ratio` of its own high-low range (a decisive candle, not a
    doji/wick-dominated bar), and its body is at least
    `min_body_vs_baseline_multiple` times the baseline mean body size (a
    real body that's long relative to what came before it, not just in
    isolation).

    The resolved price is the candle's `open` — the boundary between the
    quiet candle before it and the burst candle itself, i.e. "the point
    where the move started" — offset by `buffer_pct` away from price.
    `nth=1` is the most recent qualifying candle. Raises `ValueError` if
    candle data is unavailable or fewer than `nth` qualifying candles exist.
    """
    if kind not in {"bullish", "bearish"}:
        raise ValueError("kind must be 'bullish' or 'bearish'")
    if nth < 1:
        raise ValueError("nth must be at least 1")
    if min_volume_multiple <= 1:
        raise ValueError("min_volume_multiple must be greater than 1")
    if not (0.0 <= min_body_ratio <= 1.0):
        raise ValueError("min_body_ratio must be between 0 and 1")
    if min_body_vs_baseline_multiple < 0:
        raise ValueError("min_body_vs_baseline_multiple must be non-negative")
    if buffer_pct < 0:
        raise ValueError("buffer_pct must be non-negative")
    if baseline_window < 2:
        raise ValueError("baseline_window must be at least 2")

    frame = CandleManager(client).fetch(inst_id, bar=bar, limit=limit)
    if frame.empty or len(frame) < baseline_window + 1:
        raise ValueError(
            f"insufficient candle history for {inst_id} {bar}: "
            f"need at least {baseline_window + 1} candles"
        )

    candidates: list[dict] = []
    for index in range(baseline_window, len(frame)):
        row = frame.iloc[index]
        baseline = frame.iloc[index - baseline_window:index]
        volume_baseline = float(baseline["volume"].mean())
        body_baseline = float((baseline["close"] - baseline["open"]).abs().mean())
        if volume_baseline <= 0 or body_baseline <= 0:
            continue

        body = float(row["close"]) - float(row["open"])
        if kind == "bullish" and body <= 0:
            continue
        if kind == "bearish" and body >= 0:
            continue

        candle_range = max(float(row["high"]) - float(row["low"]), 1e-12)
        body_ratio = abs(body) / candle_range
        volume_ratio = float(row["volume"]) / volume_baseline
        body_vs_baseline = abs(body) / body_baseline
        if (
            volume_ratio < min_volume_multiple
            or body_ratio < min_body_ratio
            or body_vs_baseline < min_body_vs_baseline_multiple
        ):
            continue

        timestamp = row["timestamp"]
        if isinstance(timestamp, pd.Timestamp):
            timestamp = timestamp.to_pydatetime()
        candidates.append({
            "timestamp": timestamp,
            "open": float(row["open"]),
            "volume_ratio": volume_ratio,
            "body_ratio": body_ratio,
            "body_vs_baseline_multiple": body_vs_baseline,
        })

    candidates.sort(key=lambda item: item["timestamp"], reverse=True)
    if len(candidates) < nth:
        raise ValueError(
            f"only {len(candidates)} qualifying {kind} impulse candle(s) found for "
            f"{inst_id} {bar}; requested nth={nth}"
        )
    selected = candidates[nth - 1]
    raw_price = selected["open"]
    price = raw_price * (1 - buffer_pct) if kind == "bullish" else raw_price * (1 + buffer_pct)
    return {
        "price": price,
        "raw_origin_price": raw_price,
        "evidence": {
            "source": "impulse_origin",
            "bar": bar,
            "kind": kind,
            "nth": nth,
            "min_volume_multiple": min_volume_multiple,
            "min_body_ratio": min_body_ratio,
            "min_body_vs_baseline_multiple": min_body_vs_baseline_multiple,
            "buffer_pct": buffer_pct,
            "volume_ratio": selected["volume_ratio"],
            "body_ratio": selected["body_ratio"],
            "body_vs_baseline_multiple": selected["body_vs_baseline_multiple"],
            "candle_timestamp": selected["timestamp"].isoformat(),
            "qualifying_candidates": len(candidates),
        },
    }
