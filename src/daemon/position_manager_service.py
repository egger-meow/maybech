"""Position manager daemon — checks active rule groups on open trades and closes them.

For each open trade in TradeStore, this service:
1. Reads current prices from RuntimeState.
2. Computes velocity (1m, 5m, 10m).
3. Evaluates each RuleGroup attached to the trade.
4. If any rule group fires → closes the trade via OKX and records the exit.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from datetime import datetime, timezone

from src.daemon.service import DaemonService
from src.trading.trade_store import TradeStore
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


class PositionManagerService(DaemonService):
    """Actively monitors open trades and closes them when dynamic rules fire."""

    name = "position_manager"
    interval = 5.0

    def __init__(self, store: TradeStore, *, dry_run: bool = True) -> None:
        super().__init__()
        self.store = store
        self.dry_run = dry_run

        # Price history for velocity calculations
        self._price_history: dict[str, deque[tuple[float, float]]] = {}
        self._max_history_seconds = 600

    def setup(self) -> None:
        logger.info(
            "PositionManagerService setup complete. Dry run: %s", self.dry_run
        )

    def tick(self) -> None:
        if self.runtime is None:
            return

        # 1. Gather prices and update velocities
        prices = self._gather_prices()
        if not prices:
            return

        self._update_price_history(prices)
        velocities = self._compute_velocities()

        btc_price = prices.get("BTC-USDT-SWAP", 0.0)

        # 2. Get all open trades from the store
        open_trades = self.store.get_open_trades()
        if not open_trades:
            return

        intents: list[dict] = []

        for trade in open_trades:
            current_price = prices.get(trade.inst_id, 0.0)
            if current_price <= 0:
                intents.append({
                    "trade_id": trade.id,
                    "inst_id": trade.inst_id,
                    "action": "hold",
                    "reason": "no price data",
                })
                continue

            # Compute position PnL %
            if trade.entry_price > 0:
                if trade.side == "long":
                    pnl_pct = ((current_price - trade.entry_price) / trade.entry_price) * 100
                else:
                    pnl_pct = ((trade.entry_price - current_price) / trade.entry_price) * 100
            else:
                pnl_pct = 0.0

            # 3. Get attached rule groups
            rule_groups = self.store.get_trade_rules(trade.id)
            triggered_group = None

            for group, enabled in rule_groups:
                if not enabled:
                    continue

                fired = group.evaluate(
                    self_inst_id=trade.inst_id,
                    prices=prices,
                    velocities=velocities,
                    pnl_pct=pnl_pct,
                )

                if fired:
                    triggered_group = group
                    break

            if triggered_group:
                # Close the trade
                exit_reason = f"rule_fired:{triggered_group.name}"

                # TODO: Trigger real OKX close order here if not dry_run
                
                closed = self.store.close_trade(
                    trade.id,
                    exit_price=current_price,
                    exit_reason=exit_reason,
                    btc_price_at_exit=btc_price,
                )

                if closed:
                    logger.info(
                        "POSITION CLOSED: %s %s %s @ %.2f → %.2f (reason=%s, pnl=%.4f)",
                        trade.id, trade.side, trade.inst_id,
                        trade.entry_price, current_price,
                        triggered_group.name, closed.pnl or 0,
                    )

                    self.publish_event("position.closed", {
                        "trade_id": trade.id,
                        "inst_id": trade.inst_id,
                        "side": trade.side,
                        "entry_price": trade.entry_price,
                        "exit_price": current_price,
                        "exit_reason": exit_reason,
                        "pnl": closed.pnl,
                        "pnl_pct": closed.pnl_pct,
                        "strategy_id": trade.strategy_id,
                        "dry_run": self.dry_run,
                    })

                    intents.append({
                        "trade_id": trade.id,
                        "inst_id": trade.inst_id,
                        "action": "closed",
                        "reason": exit_reason,
                        "pnl": closed.pnl,
                    })
            else:
                # No rule fired — hold
                active_rules_info = [
                    {"name": g.name, "id": g.id, "enabled": enabled}
                    for g, enabled in rule_groups
                ]
                intents.append({
                    "trade_id": trade.id,
                    "inst_id": trade.inst_id,
                    "side": trade.side,
                    "action": "hold",
                    "reason": "no rule fired",
                    "current_price": current_price,
                    "entry_price": trade.entry_price,
                    "pnl_pct": round(pnl_pct, 2),
                    "active_rules": active_rules_info,
                    "strategy_id": trade.strategy_id,
                })

        # Publish all intents to runtime for API consumption
        if self.runtime is not None:
            self.runtime.set_value("position_manager.intents", intents)
        self.publish_event("position_manager.tick", {
            "open_count": len(open_trades),
            "intents": intents,
        })

    def _gather_prices(self) -> dict[str, float]:
        prices: dict[str, float] = {}
        btc_regime = self.runtime.get_value("market.btc_regime")
        if btc_regime and "price" in btc_regime:
            prices["BTC-USDT-SWAP"] = float(btc_regime["price"])

        snapshot = self.runtime.get_value("account.snapshot") or {}
        for pos in snapshot.get("positions", []):
            inst_id = pos.get("inst_id", "")
            mark_px = pos.get("mark_price", "")
            if inst_id and mark_px:
                try:
                    prices[inst_id] = float(mark_px)
                except (ValueError, TypeError):
                    pass
        return prices

    def _update_price_history(self, prices: dict[str, float]) -> None:
        now = time.time()
        cutoff = now - self._max_history_seconds
        for inst_id, price in prices.items():
            if inst_id not in self._price_history:
                self._price_history[inst_id] = deque(maxlen=1200)
            hist = self._price_history[inst_id]
            hist.append((now, price))
            while hist and hist[0][0] < cutoff:
                hist.popleft()

    def _compute_velocities(self) -> dict[str, float]:
        velocities: dict[str, float] = {}
        now = time.time()
        windows = {"velocity_1m": 60, "velocity_5m": 300, "velocity_10m": 600}
        
        for inst_id, hist in self._price_history.items():
            if len(hist) < 2:
                continue
            current = hist[-1][1]
            for name, seconds in windows.items():
                cutoff = now - seconds
                oldest = None
                for ts, px in hist:
                    if ts >= cutoff:
                        oldest = px
                        break
                if oldest and oldest > 0:
                    velocities[f"{inst_id}:{name}"] = ((current - oldest) / oldest) * 100
        return velocities

    def teardown(self) -> None:
        logger.info("PositionManagerService shutting down.")

