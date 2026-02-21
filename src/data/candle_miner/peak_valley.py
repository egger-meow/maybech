"""
PeakValley — detect local price extrema and cluster them into price levels.

**Detection** uses **high** for peaks and **low** for valleys so that
needle wicks are captured.

**Clustering** groups nearby extrema by price into support/resistance
levels, scored by *significance* (count + sharpness + purity).
"""

from __future__ import annotations

import math
from typing import Any, Literal, TypedDict

import pandas as pd

from src.data.candle_miner.feature import Feature


# ── Data types ───────────────────────────────────────────────────────

class Extremum(TypedDict):
    """A single detected peak or valley."""

    price: float
    sharpness: float
    timestamp: pd.Timestamp
    kind: Literal["peak", "valley"]


class PriceLevel(TypedDict):
    """A cluster of nearby extrema forming a support/resistance zone."""

    price: float                              # sharpness-weighted avg
    significance: float                       # 0–1 composite score
    kind: Literal["peak", "valley", "mixed"]  # majority kind
    count: int                                # extrema in cluster
    purity: float                             # fraction of majority kind


class PeakValleyResult(TypedDict):
    """Full output of the PeakValley feature."""

    raw: list[Extremum]
    levels: list[PriceLevel]


# ── Feature ──────────────────────────────────────────────────────────

class PeakValley(Feature):
    """Detect local peaks (Λ) and valleys (V), then cluster into price levels.

    Parameters
    ----------
    window : int
        Candles on **each side** to compare for local extremum.  Default ``2``.
    min_sharpness : float
        Discard extrema with sharpness below this.  Default ``0.0``.
    cluster_tolerance : float
        Maximum price difference (as fraction of price) to merge two
        adjacent extrema into the same cluster.  Default ``0.003`` (0.3%).
    count_weight, sharpness_weight, purity_weight : float
        Relative weights for the significance formula.
    """

    name = "peak_valley"

    def __init__(
        self,
        window: int = 2,
        min_sharpness: float = 0.0,
        cluster_tolerance: float = 0.003,
        *,
        count_weight: float = 0.5,
        sharpness_weight: float = 0.3,
        purity_weight: float = 0.2,
    ) -> None:
        self.window = window
        self.min_sharpness = min_sharpness
        self.cluster_tolerance = cluster_tolerance
        self.count_weight = count_weight
        self.sharpness_weight = sharpness_weight
        self.purity_weight = purity_weight

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract(self, candles: pd.DataFrame) -> PeakValleyResult:
        """Detect extrema and cluster them into price levels."""
        raw = self._detect(candles)
        levels = self._cluster(raw)
        return PeakValleyResult(raw=raw, levels=levels)

    # ------------------------------------------------------------------
    # Detection (unchanged logic)
    # ------------------------------------------------------------------

    def _detect(self, candles: pd.DataFrame) -> list[Extremum]:
        results: list[Extremum] = []
        n = len(candles)
        w = self.window

        if n < 2 * w + 1:
            return results

        highs = candles["high"].to_numpy()
        lows = candles["low"].to_numpy()
        closes = candles["close"].to_numpy()
        timestamps = candles["timestamp"]

        for i in range(w, n - w):
            left = slice(i - w, i)
            right = slice(i + 1, i + w + 1)

            # Peak: high[i] > all neighbouring highs
            if highs[i] > highs[left].max() and highs[i] > highs[right].max():
                sharpness = self._sharpness(highs, closes, i, w)
                if sharpness >= self.min_sharpness:
                    results.append(Extremum(
                        price=float(highs[i]),
                        sharpness=sharpness,
                        timestamp=timestamps.iloc[i],
                        kind="peak",
                    ))

            # Valley: low[i] < all neighbouring lows
            if lows[i] < lows[left].min() and lows[i] < lows[right].min():
                sharpness = self._sharpness(lows, closes, i, w)
                if sharpness >= self.min_sharpness:
                    results.append(Extremum(
                        price=float(lows[i]),
                        sharpness=sharpness,
                        timestamp=timestamps.iloc[i],
                        kind="valley",
                    ))

        return results

    # ------------------------------------------------------------------
    # Clustering
    # ------------------------------------------------------------------

    def _cluster(self, extrema: list[Extremum]) -> list[PriceLevel]:
        """Group nearby extrema by price and score each cluster."""
        if not extrema:
            return []

        # Sort by price
        sorted_ext = sorted(extrema, key=lambda e: e["price"])

        # Greedy merge: walk through sorted list, extend current cluster
        # while the next extremum's price is within tolerance of the
        # cluster's representative price.
        clusters: list[list[Extremum]] = []
        current: list[Extremum] = [sorted_ext[0]]

        for ext in sorted_ext[1:]:
            # Compare against the weighted-average price of current cluster
            cluster_price = self._weighted_avg_price(current)
            if abs(ext["price"] - cluster_price) / cluster_price <= self.cluster_tolerance:
                current.append(ext)
            else:
                clusters.append(current)
                current = [ext]
        clusters.append(current)

        # Score each cluster
        levels = [self._score_cluster(c) for c in clusters]

        # Normalise significance to 0–1
        if levels:
            max_sig = max(lv["significance"] for lv in levels)
            if max_sig > 0:
                for lv in levels:
                    lv["significance"] = lv["significance"] / max_sig

        # Sort descending by significance
        levels.sort(key=lambda lv: lv["significance"], reverse=True)
        return levels

    def _score_cluster(self, cluster: list[Extremum]) -> PriceLevel:
        """Compute a PriceLevel from a group of extrema."""
        count = len(cluster)
        n_peaks = sum(1 for e in cluster if e["kind"] == "peak")
        n_valleys = count - n_peaks

        # Weighted average price (weighted by sharpness)
        price = self._weighted_avg_price(cluster)

        # Average sharpness
        avg_sharpness = sum(e["sharpness"] for e in cluster) / count

        # Purity: fraction of majority kind
        majority = max(n_peaks, n_valleys)
        purity = majority / count

        # Kind
        if n_peaks > n_valleys:
            kind: Literal["peak", "valley", "mixed"] = "peak"
        elif n_valleys > n_peaks:
            kind = "valley"
        else:
            kind = "mixed"

        # Raw significance (un-normalised)
        raw_sig = (
            self.count_weight * math.log2(1 + count)
            + self.sharpness_weight * avg_sharpness
            + self.purity_weight * purity
        )

        return PriceLevel(
            price=round(price, 6),
            significance=raw_sig,
            kind=kind,
            count=count,
            purity=round(purity, 4),
        )

    @staticmethod
    def _weighted_avg_price(cluster: list[Extremum]) -> float:
        """Sharpness-weighted average price of a cluster."""
        total_w = sum(e["sharpness"] for e in cluster)
        if total_w == 0:
            # Fallback: simple average
            return sum(e["price"] for e in cluster) / len(cluster)
        return sum(e["price"] * e["sharpness"] for e in cluster) / total_w

    # ------------------------------------------------------------------
    # Sharpness helper
    # ------------------------------------------------------------------

    @staticmethod
    def _sharpness(
        hl: "numpy array",
        closes: "numpy array",
        centre: int,
        window: int,
    ) -> float:
        """Normalised sharpness: avg |extremum – neighbour close| / price."""
        price = float(hl[centre])
        if price == 0:
            return 0.0

        diffs = 0.0
        count = 0
        for j in range(centre - window, centre + window + 1):
            if j == centre:
                continue
            diffs += abs(price - closes[j])
            count += 1

        return (diffs / count) / price if count else 0.0

    def __repr__(self) -> str:
        return (
            f"PeakValley(window={self.window}, "
            f"min_sharpness={self.min_sharpness}, "
            f"cluster_tolerance={self.cluster_tolerance})"
        )
