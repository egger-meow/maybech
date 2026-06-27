"""Policy checks before a strategy action is allowed to execute."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

@dataclass(frozen=True)
class ActionDecision:
    """Result of evaluating whether a setup may proceed."""

    allowed: bool
    reason: str
    pair: str
    signal: str
    btc_direction: str | None = None
    btc_strength: str | None = None
    btc_impulse: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


class BTCRegimeActionPolicy:
    """Conservative BTC-led gate for perpetual position actions."""

    def evaluate(
        self,
        *,
        pair: str,
        position_side: str,
        btc_regime: dict[str, Any] | None,
    ) -> ActionDecision:
        side = position_side.lower()
        if btc_regime is None:
            return ActionDecision(
                allowed=False,
                reason="blocked: BTC regime unavailable",
                pair=pair,
                signal=side,
            )

        direction = btc_regime.get("direction")
        strength = btc_regime.get("strength")
        impulse = btc_regime.get("impulse")

        if side == "long":
            if direction == "bearish" and strength == "strong":
                return self._blocked(pair, side, btc_regime, "strong bearish BTC regime")
            if impulse == "down":
                return self._blocked(pair, side, btc_regime, "BTC downside impulse")
            return self._allowed(pair, side, btc_regime, "BTC regime permits long action")

        if side == "short":
            if direction == "bullish" and strength == "strong":
                return self._blocked(pair, side, btc_regime, "strong bullish BTC regime")
            if impulse == "up":
                return self._blocked(pair, side, btc_regime, "BTC upside impulse")
            return self._allowed(pair, side, btc_regime, "BTC regime permits short action")

        return ActionDecision(
            allowed=False,
            reason="blocked: invalid position side",
            pair=pair,
            signal=side,
            btc_direction=direction,
            btc_strength=strength,
            btc_impulse=impulse,
        )

    def _allowed(self, pair: str, side: str, regime: dict[str, Any], reason: str) -> ActionDecision:
        return self._decision(True, pair, side, regime, reason)

    def _blocked(self, pair: str, side: str, regime: dict[str, Any], reason: str) -> ActionDecision:
        return self._decision(False, pair, side, regime, f"blocked: {reason}")

    def _decision(
        self,
        allowed: bool,
        pair: str,
        side: str,
        regime: dict[str, Any],
        reason: str,
    ) -> ActionDecision:
        return ActionDecision(
            allowed=allowed,
            reason=reason,
            pair=pair,
            signal=side,
            btc_direction=regime.get("direction"),
            btc_strength=regime.get("strength"),
            btc_impulse=regime.get("impulse"),
        )
