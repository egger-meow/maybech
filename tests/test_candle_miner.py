"""
Unit tests for CandleMiner framework and the PeakValley feature (with clustering).
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.data.candle_miner.feature import CandleMiner, Feature
from src.data.candle_miner.peak_valley import PeakValley


# ── Helpers ─────────────────────────────────────────────────────────

def _make_candles(
    closes: list[float],
    highs: list[float] | None = None,
    lows: list[float] | None = None,
) -> pd.DataFrame:
    """Build a minimal candles DataFrame from close (and optional high/low)."""
    n = len(closes)
    if highs is None:
        highs = closes
    if lows is None:
        lows = closes
    return pd.DataFrame({
        "timestamp": pd.date_range("2025-01-01", periods=n, freq="h", tz="UTC"),
        "open": closes,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": [100.0] * n,
    })


class _DummyFeature(Feature):
    """Trivial feature for testing the registration machinery."""

    name = "dummy"

    def extract(self, candles: pd.DataFrame):
        return {"rows": len(candles)}


# ── CandleMiner registration tests ─────────────────────────────────

class TestCandleMinerRegistry:
    def test_register_and_list(self):
        miner = CandleMiner()
        miner.register(_DummyFeature())
        assert miner.feature_names == ["dummy"]
        assert "dummy" in miner
        assert len(miner) == 1

    def test_unregister(self):
        miner = CandleMiner()
        miner.register(_DummyFeature())
        miner.unregister("dummy")
        assert miner.feature_names == []
        with pytest.raises(KeyError):
            miner.unregister("dummy")

    def test_mine_empty_features(self):
        miner = CandleMiner()
        assert miner.mine(_make_candles([1, 2, 3])) == {}

    def test_mine_runs_all_features(self):
        class _Other(Feature):
            name = "other"
            def extract(self, candles):
                return 42

        miner = CandleMiner()
        miner.register(_DummyFeature())
        miner.register(_Other())
        result = miner.mine(_make_candles([1, 2, 3]))
        assert "dummy" in result
        assert "other" in result
        assert result["other"] == 42


# ── PeakValley detection tests ─────────────────────────────────────

class TestPeakValleyDetection:
    """Tests for raw extremum detection (the 'raw' key)."""

    def test_peak_detection(self):
        """Λ shape: 10, 20, 30, 20, 10 → peak at index 2."""
        candles = _make_candles([10, 20, 30, 20, 10])
        result = PeakValley(window=2).extract(candles)
        peaks = [e for e in result["raw"] if e["kind"] == "peak"]
        assert len(peaks) == 1
        assert peaks[0]["price"] == 30.0

    def test_valley_detection(self):
        """V shape: 30, 20, 10, 20, 30 → valley at index 2."""
        candles = _make_candles([30, 20, 10, 20, 30])
        result = PeakValley(window=2).extract(candles)
        valleys = [e for e in result["raw"] if e["kind"] == "valley"]
        assert len(valleys) == 1
        assert valleys[0]["price"] == 10.0

    def test_needle_wick_peak(self):
        closes = [100, 100, 100, 100, 100]
        highs = [100, 100, 150, 100, 100]
        candles = _make_candles(closes, highs=highs)
        result = PeakValley(window=2).extract(candles)
        peaks = [e for e in result["raw"] if e["kind"] == "peak"]
        assert len(peaks) == 1
        assert peaks[0]["price"] == 150.0

    def test_needle_wick_valley(self):
        closes = [100, 100, 100, 100, 100]
        lows = [100, 100, 50, 100, 100]
        candles = _make_candles(closes, lows=lows)
        result = PeakValley(window=2).extract(candles)
        valleys = [e for e in result["raw"] if e["kind"] == "valley"]
        assert len(valleys) == 1
        assert valleys[0]["price"] == 50.0

    def test_sharpness_ordering(self):
        mild = _make_candles([10, 12, 14, 12, 10])
        sharp = _make_candles([10, 12, 50, 12, 10])
        pv = PeakValley(window=2)
        mild_peaks = [e for e in pv.extract(mild)["raw"] if e["kind"] == "peak"]
        sharp_peaks = [e for e in pv.extract(sharp)["raw"] if e["kind"] == "peak"]
        assert sharp_peaks[0]["sharpness"] > mild_peaks[0]["sharpness"]

    def test_window_parameter(self):
        closes = [10, 20, 15, 20, 10]
        candles = _make_candles(closes)
        assert len([e for e in PeakValley(window=1).extract(candles)["raw"] if e["kind"] == "peak"]) >= 1
        assert len([e for e in PeakValley(window=2).extract(candles)["raw"] if e["kind"] == "peak"]) == 0

    def test_min_sharpness_filter(self):
        candles = _make_candles([100, 101, 102, 101, 100])
        result = PeakValley(window=2, min_sharpness=0.5).extract(candles)
        assert len(result["raw"]) == 0

    def test_no_extrema_flat(self):
        candles = _make_candles([100] * 10)
        result = PeakValley(window=2).extract(candles)
        assert result["raw"] == []
        assert result["levels"] == []

    def test_too_few_candles(self):
        candles = _make_candles([10, 20, 10])
        result = PeakValley(window=2).extract(candles)
        assert result["raw"] == []


# ── Clustering tests ───────────────────────────────────────────────

class TestPeakValleyClustering:
    """Tests for the price-level clustering ('levels' key)."""

    def test_nearby_peaks_merge(self):
        """Two peaks at similar prices should merge into one level."""
        # Two Λ shapes with peaks at ~100
        closes = [90, 95, 100, 95, 90, 95, 101, 95, 90]
        candles = _make_candles(closes)
        result = PeakValley(window=2, cluster_tolerance=0.02).extract(candles)
        # Both peaks (100 and 101) are within 2% → merged
        levels = result["levels"]
        peak_levels = [lv for lv in levels if lv["kind"] == "peak"]
        assert len(peak_levels) == 1
        assert peak_levels[0]["count"] == 2

    def test_distant_peaks_stay_separate(self):
        """Two peaks far apart should remain as separate levels."""
        closes = [50, 60, 100, 60, 50, 60, 200, 60, 50]
        candles = _make_candles(closes)
        result = PeakValley(window=2, cluster_tolerance=0.003).extract(candles)
        peak_levels = [lv for lv in result["levels"] if lv["kind"] == "peak"]
        assert len(peak_levels) == 2

    def test_purity_all_same_kind(self):
        """A cluster of only peaks should have purity = 1.0."""
        closes = [90, 95, 100, 95, 90, 95, 101, 95, 90]
        candles = _make_candles(closes)
        result = PeakValley(window=2, cluster_tolerance=0.02).extract(candles)
        peak_levels = [lv for lv in result["levels"] if lv["kind"] == "peak"]
        assert len(peak_levels) >= 1
        assert peak_levels[0]["purity"] == 1.0

    def test_significance_normalised(self):
        """All significance values should be in [0, 1] with max == 1."""
        closes = [10, 20, 50, 20, 10, 5, 1, 5, 10, 20, 50, 20, 10]
        candles = _make_candles(closes)
        result = PeakValley(window=2).extract(candles)
        levels = result["levels"]
        assert len(levels) >= 1
        assert max(lv["significance"] for lv in levels) == 1.0
        assert all(0 <= lv["significance"] <= 1.0 for lv in levels)

    def test_levels_sorted_by_significance(self):
        """Levels should be sorted descending by significance."""
        closes = [10, 20, 50, 20, 10, 5, 1, 5, 10, 20, 50, 20, 10]
        candles = _make_candles(closes)
        levels = PeakValley(window=2).extract(candles)["levels"]
        sigs = [lv["significance"] for lv in levels]
        assert sigs == sorted(sigs, reverse=True)

    def test_output_shape(self):
        """Result should have both 'raw' and 'levels' keys."""
        candles = _make_candles([10, 20, 30, 20, 10])
        result = PeakValley(window=2).extract(candles)
        assert "raw" in result
        assert "levels" in result
        assert isinstance(result["raw"], list)
        assert isinstance(result["levels"], list)


# ── Integration test ───────────────────────────────────────────────

class TestIntegration:
    def test_peak_valley_in_candleminer(self):
        """Full pipeline: register PeakValley in CandleMiner and mine."""
        candles = _make_candles([10, 20, 50, 20, 10, 5, 1, 5, 10])
        miner = CandleMiner()
        miner.register(PeakValley(window=2))
        result = miner.mine(candles)

        assert "peak_valley" in result
        pv = result["peak_valley"]
        assert "raw" in pv
        assert "levels" in pv

        peaks = [e for e in pv["raw"] if e["kind"] == "peak"]
        valleys = [e for e in pv["raw"] if e["kind"] == "valley"]
        assert len(peaks) >= 1
        assert len(valleys) >= 1
        assert peaks[0]["price"] == 50.0

        # Levels should also be populated
        assert len(pv["levels"]) >= 1
