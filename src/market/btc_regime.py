"""BTC-led market regime classification."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

import pandas as pd


Direction = Literal["bullish", "bearish", "neutral"]
Strength = Literal["strong", "normal", "weak"]
Impulse = Literal["up", "down", "none"]


@dataclass(frozen=True)
class BTCMarketRegime:
    """Structured BTC state used by downstream strategy/risk logic."""

    symbol: str
    price: float
    direction: Direction
    strength: Strength
    impulse: Impulse
    change_pct: float
    volatility_pct: float
    ema_fast: float
    ema_slow: float
    nearest_level: float | None
    distance_to_level_pct: float | None
    reason: str

    def to_dict(self) -> dict:
        return asdict(self)


class BTCRegimeAnalyzer:
    """Classifies BTC trend, impulse, volatility, and level proximity."""

    def __init__(
        self,
        *,
        fast_span: int = 20,
        slow_span: int = 50,
        impulse_threshold_pct: float = 1.0,
        strong_trend_threshold_pct: float = 0.5,
        level_window: int = 20,
    ) -> None:
        self.fast_span = fast_span
        self.slow_span = slow_span
        self.impulse_threshold_pct = impulse_threshold_pct
        self.strong_trend_threshold_pct = strong_trend_threshold_pct
        self.level_window = level_window

    def analyze(self, df: pd.DataFrame, *, symbol: str = "BTC-USDT-SWAP") -> BTCMarketRegime:
        self._validate(df)
        close = df["close"].astype(float)
        high = df["high"].astype(float) if "high" in df else close
        low = df["low"].astype(float) if "low" in df else close

        price = float(close.iloc[-1])
        prev_price = float(close.iloc[-2])
        change_pct = ((price - prev_price) / prev_price) * 100 if prev_price else 0.0

        ema_fast = float(close.ewm(span=self.fast_span, adjust=False).mean().iloc[-1])
        ema_slow = float(close.ewm(span=self.slow_span, adjust=False).mean().iloc[-1])
        ema_gap_pct = ((ema_fast - ema_slow) / price) * 100 if price else 0.0

        direction = self._direction(ema_gap_pct)
        strength = self._strength(abs(ema_gap_pct))
        impulse = self._impulse(change_pct)
        volatility_pct = self._volatility_pct(high, low, close)
        nearest_level, distance_pct = self._nearest_level(price, high, low)

        reason = (
            f"BTC {direction}/{strength}; impulse={impulse}; "
            f"change={change_pct:.2f}%; ema_gap={ema_gap_pct:.2f}%; "
            f"volatility={volatility_pct:.2f}%"
        )

        return BTCMarketRegime(
            symbol=symbol,
            price=price,
            direction=direction,
            strength=strength,
            impulse=impulse,
            change_pct=change_pct,
            volatility_pct=volatility_pct,
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            nearest_level=nearest_level,
            distance_to_level_pct=distance_pct,
            reason=reason,
        )

    def _validate(self, df: pd.DataFrame) -> None:
        if len(df) < max(3, self.slow_span):
            raise ValueError(f"BTC regime requires at least {self.slow_span} candles")
        if "close" not in df:
            raise ValueError("BTC regime requires a close column")

    def _direction(self, ema_gap_pct: float) -> Direction:
        if ema_gap_pct > 0:
            return "bullish"
        if ema_gap_pct < 0:
            return "bearish"
        return "neutral"

    def _strength(self, ema_gap_abs_pct: float) -> Strength:
        if ema_gap_abs_pct >= self.strong_trend_threshold_pct:
            return "strong"
        if ema_gap_abs_pct > 0:
            return "normal"
        return "weak"

    def _impulse(self, change_pct: float) -> Impulse:
        if change_pct >= self.impulse_threshold_pct:
            return "up"
        if change_pct <= -self.impulse_threshold_pct:
            return "down"
        return "none"

    def _volatility_pct(self, high: pd.Series, low: pd.Series, close: pd.Series) -> float:
        window = min(self.level_window, len(close))
        recent_high = high.tail(window)
        recent_low = low.tail(window)
        recent_close = close.tail(window)
        true_range_pct = ((recent_high - recent_low) / recent_close).abs() * 100
        return float(true_range_pct.mean())

    def _nearest_level(self, price: float, high: pd.Series, low: pd.Series) -> tuple[float | None, float | None]:
        window = min(self.level_window, len(high))
        levels = [
            float(high.tail(window).max()),
            float(low.tail(window).min()),
        ]
        nearest = min(levels, key=lambda level: abs(price - level))
        distance_pct = abs(price - nearest) / price * 100 if price else None
        return nearest, distance_pct
