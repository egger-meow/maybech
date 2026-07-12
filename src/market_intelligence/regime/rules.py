"""Deterministic, auditable per-pillar regime rules (plan.md Phase 4, section 9).

Each function takes an already-fetched metric value (or ``None`` when no
observation exists) and returns ``(state, summary)``. ``state`` is a
risk-pressure reading for that pillar — supportive | neutral | cautious |
stressed | unavailable — never a buy/sell instruction (plan.md's Non-Goals
explicitly rule out connecting these to order execution). Thresholds are
Maybech's own heuristic choice, not a claimed industry standard; that
context lives in the caller's evidence/caveats, not repeated in every
summary string.

Kept separate from ``assessor.py`` (which reads the store) so these rules are
unit-testable against plain numbers with no database involved.
"""

from __future__ import annotations


def classify_derivatives(funding_annualized: float | None) -> tuple[str, str]:
    if funding_annualized is None:
        return "unavailable", "No OKX annualized funding rate observation available."
    magnitude = abs(funding_annualized)
    pct = funding_annualized * 100
    if magnitude < 0.05:
        return "supportive", f"Annualized OKX funding ~{pct:.2f}%; leverage pressure looks mild."
    if magnitude < 0.20:
        return "neutral", f"Annualized OKX funding ~{pct:.2f}%; leverage pressure looks moderate."
    if magnitude < 0.50:
        return (
            "cautious",
            f"Annualized OKX funding ~{pct:.2f}%; leverage pressure looks elevated, crowded positioning risk.",
        )
    return "stressed", f"Annualized OKX funding ~{pct:.2f}%; leverage pressure looks extreme, squeeze risk."


def classify_valuation(mvrv_z: float | None) -> tuple[str, str]:
    if mvrv_z is None:
        return "unavailable", "No BTC MVRV Z-Score observation available."
    if mvrv_z < 0:
        return (
            "supportive",
            f"MVRV Z-Score {mvrv_z:.2f}; long-horizon valuation looks low "
            "(aggregate holders in unrealized loss).",
        )
    if mvrv_z < 3:
        return "neutral", f"MVRV Z-Score {mvrv_z:.2f}; long-horizon valuation looks neutral."
    if mvrv_z < 6:
        return "cautious", f"MVRV Z-Score {mvrv_z:.2f}; long-horizon valuation looks elevated."
    return "stressed", f"MVRV Z-Score {mvrv_z:.2f}; long-horizon valuation is in a historically extreme range."


def classify_liquidity(change_7d_pct: float | None) -> tuple[str, str]:
    if change_7d_pct is None:
        return "unavailable", "Not enough stablecoin market-cap history yet (needs at least 7 days)."
    if change_7d_pct >= 2.0:
        return "supportive", f"Stablecoin total market cap {change_7d_pct:+.2f}% over 7d; liquidity expanding."
    if change_7d_pct > -2.0:
        return "neutral", f"Stablecoin total market cap {change_7d_pct:+.2f}% over 7d; liquidity roughly flat."
    if change_7d_pct > -5.0:
        return "cautious", f"Stablecoin total market cap {change_7d_pct:+.2f}% over 7d; liquidity contracting."
    return (
        "stressed",
        f"Stablecoin total market cap {change_7d_pct:+.2f}% over 7d; liquidity contracting sharply.",
    )


def classify_price_breadth(advancing_pct: float | None) -> tuple[str, str]:
    if advancing_pct is None:
        return "unavailable", "No market breadth observation available."
    if advancing_pct >= 60:
        return (
            "supportive",
            f"{advancing_pct:.0f}% of tracked top-100 coins advancing over 24h; broad-based strength.",
        )
    if advancing_pct >= 40:
        return (
            "neutral",
            f"{advancing_pct:.0f}% of tracked top-100 coins advancing over 24h; breadth roughly balanced.",
        )
    if advancing_pct >= 25:
        return (
            "cautious",
            f"{advancing_pct:.0f}% of tracked top-100 coins advancing over 24h; breadth narrowing.",
        )
    return (
        "stressed",
        f"{advancing_pct:.0f}% of tracked top-100 coins advancing over 24h; "
        "breadth has collapsed, strength highly concentrated.",
    )


def classify_sentiment(fear_greed: float | None) -> tuple[str, str]:
    if fear_greed is None:
        return "unavailable", "No Fear & Greed Index observation available."
    if fear_greed <= 20:
        return "stressed", f"Fear & Greed Index {fear_greed:.0f}; extreme fear, sentiment stretched."
    if fear_greed <= 35:
        return "cautious", f"Fear & Greed Index {fear_greed:.0f}; leaning fearful."
    if fear_greed < 65:
        return "neutral", f"Fear & Greed Index {fear_greed:.0f}; sentiment neutral."
    if fear_greed < 80:
        return "cautious", f"Fear & Greed Index {fear_greed:.0f}; leaning greedy."
    return "stressed", f"Fear & Greed Index {fear_greed:.0f}; extreme greed, sentiment stretched."
