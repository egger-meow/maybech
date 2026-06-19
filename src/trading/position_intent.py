"""Structured position-management intent policy."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal


PositionAction = Literal["hold", "reduce", "close", "manual_review"]


@dataclass(frozen=True)
class PositionIntent:
    """Read-only action guidance for an existing perpetual position."""

    inst_id: str
    side: str
    action: PositionAction
    reason: str
    btc_direction: str | None = None
    btc_strength: str | None = None
    btc_impulse: str | None = None
    position_size: float | None = None
    unrealised_pnl_pct: float | None = None
    leverage: float | None = None
    liquidation_distance_pct: float | None = None

    def to_dict(self) -> dict:
        return asdict(self)


class PositionIntentPolicy:
    """Conservative BTC-led guidance for managing open positions."""

    def __init__(
        self,
        *,
        reduce_loss_pct: float = -1.0,
        close_loss_pct: float = -2.5,
        high_leverage_threshold: float = 10.0,
        liquidation_distance_threshold_pct: float = 5.0,
    ) -> None:
        self.reduce_loss_pct = reduce_loss_pct
        self.close_loss_pct = close_loss_pct
        self.high_leverage_threshold = high_leverage_threshold
        self.liquidation_distance_threshold_pct = liquidation_distance_threshold_pct

    def evaluate(
        self,
        *,
        position: dict[str, Any],
        btc_regime: dict[str, Any] | None,
    ) -> PositionIntent:
        inst_id = str(position.get("inst_id") or position.get("instId") or "")
        side = self._normalize_side(position.get("pos_side") or position.get("posSide") or "")
        size = self._as_float(position.get("position") or position.get("pos"))
        avg_price = self._as_float(position.get("avg_price") or position.get("avgPx"))
        mark_price = self._as_float(position.get("mark_price") or position.get("markPx"))
        leverage = self._as_float(position.get("leverage") or position.get("lever"))
        liquidation_price = self._as_float(position.get("liquidation_price") or position.get("liqPx"))
        unrealised_pnl_pct = self._unrealised_pnl_pct(side, avg_price, mark_price)
        liquidation_distance_pct = self._liquidation_distance_pct(mark_price, liquidation_price)

        if size <= 0:
            return PositionIntent(
                inst_id=inst_id,
                side=side,
                action="hold",
                reason="flat position",
                position_size=size,
            )

        if side == "unknown":
            return self._intent(
                inst_id=inst_id,
                side=side,
                action="manual_review",
                reason="position side is unknown",
                btc_regime=btc_regime,
                position_size=size,
                unrealised_pnl_pct=unrealised_pnl_pct,
                leverage=leverage,
                liquidation_distance_pct=liquidation_distance_pct,
            )

        if btc_regime is None:
            return self._intent(
                inst_id=inst_id,
                side=side,
                action="manual_review",
                reason="BTC regime unavailable",
                btc_regime=None,
                position_size=size,
                unrealised_pnl_pct=unrealised_pnl_pct,
                leverage=leverage,
                liquidation_distance_pct=liquidation_distance_pct,
            )

        direction = str(btc_regime.get("direction") or "neutral")
        strength = str(btc_regime.get("strength") or "weak")
        impulse = str(btc_regime.get("impulse") or "none")
        opposite = self._btc_opposes_position(side, direction, strength, impulse)
        high_risk = self._is_high_risk(
            unrealised_pnl_pct=unrealised_pnl_pct,
            leverage=leverage,
            liquidation_distance_pct=liquidation_distance_pct,
        )
        moderate_risk = self._is_moderate_risk(
            unrealised_pnl_pct=unrealised_pnl_pct,
            leverage=leverage,
            liquidation_distance_pct=liquidation_distance_pct,
        )

        if opposite and high_risk:
            return self._intent(
                inst_id=inst_id,
                side=side,
                action="close",
                reason="BTC regime is strongly against the position and risk is elevated",
                btc_regime=btc_regime,
                position_size=size,
                unrealised_pnl_pct=unrealised_pnl_pct,
                leverage=leverage,
                liquidation_distance_pct=liquidation_distance_pct,
            )

        if opposite and moderate_risk:
            return self._intent(
                inst_id=inst_id,
                side=side,
                action="reduce",
                reason="BTC regime is against the position and risk is rising",
                btc_regime=btc_regime,
                position_size=size,
                unrealised_pnl_pct=unrealised_pnl_pct,
                leverage=leverage,
                liquidation_distance_pct=liquidation_distance_pct,
            )

        if opposite:
            return self._intent(
                inst_id=inst_id,
                side=side,
                action="reduce",
                reason="BTC regime is against the position",
                btc_regime=btc_regime,
                position_size=size,
                unrealised_pnl_pct=unrealised_pnl_pct,
                leverage=leverage,
                liquidation_distance_pct=liquidation_distance_pct,
            )

        if moderate_risk:
            return self._intent(
                inst_id=inst_id,
                side=side,
                action="manual_review",
                reason="position risk is rising",
                btc_regime=btc_regime,
                position_size=size,
                unrealised_pnl_pct=unrealised_pnl_pct,
                leverage=leverage,
                liquidation_distance_pct=liquidation_distance_pct,
            )

        if direction == "neutral" or strength == "weak":
            return self._intent(
                inst_id=inst_id,
                side=side,
                action="manual_review",
                reason="BTC regime is not strong enough for a position change",
                btc_regime=btc_regime,
                position_size=size,
                unrealised_pnl_pct=unrealised_pnl_pct,
                leverage=leverage,
                liquidation_distance_pct=liquidation_distance_pct,
            )

        return self._intent(
            inst_id=inst_id,
            side=side,
            action="hold",
            reason="BTC regime supports the position",
            btc_regime=btc_regime,
            position_size=size,
            unrealised_pnl_pct=unrealised_pnl_pct,
            leverage=leverage,
            liquidation_distance_pct=liquidation_distance_pct,
        )

    def _intent(
        self,
        *,
        inst_id: str,
        side: str,
        action: PositionAction,
        reason: str,
        btc_regime: dict[str, Any] | None,
        position_size: float | None,
        unrealised_pnl_pct: float | None,
        leverage: float | None,
        liquidation_distance_pct: float | None,
    ) -> PositionIntent:
        return PositionIntent(
            inst_id=inst_id,
            side=side,
            action=action,
            reason=reason,
            btc_direction=None if btc_regime is None else str(btc_regime.get("direction") or "neutral"),
            btc_strength=None if btc_regime is None else str(btc_regime.get("strength") or "weak"),
            btc_impulse=None if btc_regime is None else str(btc_regime.get("impulse") or "none"),
            position_size=position_size,
            unrealised_pnl_pct=unrealised_pnl_pct,
            leverage=leverage,
            liquidation_distance_pct=liquidation_distance_pct,
        )

    def _btc_opposes_position(self, side: str, direction: str, strength: str, impulse: str) -> bool:
        if side == "long":
            return direction == "bearish" and strength == "strong" or impulse == "down"
        if side == "short":
            return direction == "bullish" and strength == "strong" or impulse == "up"
        return False

    def _is_high_risk(
        self,
        *,
        unrealised_pnl_pct: float | None,
        leverage: float | None,
        liquidation_distance_pct: float | None,
    ) -> bool:
        if unrealised_pnl_pct is not None and unrealised_pnl_pct <= self.close_loss_pct:
            return True
        if leverage is not None and leverage >= self.high_leverage_threshold:
            return True
        if liquidation_distance_pct is not None and liquidation_distance_pct <= self.liquidation_distance_threshold_pct:
            return True
        return False

    def _is_moderate_risk(
        self,
        *,
        unrealised_pnl_pct: float | None,
        leverage: float | None,
        liquidation_distance_pct: float | None,
    ) -> bool:
        if unrealised_pnl_pct is not None and unrealised_pnl_pct <= self.reduce_loss_pct:
            return True
        if leverage is not None and leverage >= self.high_leverage_threshold * 0.8:
            return True
        if (
            liquidation_distance_pct is not None
            and liquidation_distance_pct <= self.liquidation_distance_threshold_pct * 2
        ):
            return True
        return False

    def _normalize_side(self, side: str) -> str:
        side = side.lower()
        if side in {"long", "buy"}:
            return "long"
        if side in {"short", "sell"}:
            return "short"
        return "unknown"

    def _unrealised_pnl_pct(self, side: str, avg_price: float | None, mark_price: float | None) -> float | None:
        if not avg_price or not mark_price:
            return None
        if side == "short":
            return ((avg_price - mark_price) / avg_price) * 100.0
        return ((mark_price - avg_price) / avg_price) * 100.0

    def _liquidation_distance_pct(self, mark_price: float | None, liquidation_price: float | None) -> float | None:
        if not mark_price or not liquidation_price:
            return None
        return abs(mark_price - liquidation_price) / mark_price * 100.0

    def _as_float(self, value: Any) -> float | None:
        try:
            if value in (None, ""):
                return None
            return float(value)
        except (TypeError, ValueError):
            return None
