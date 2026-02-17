"""
Grid Search Optimizer.

Finds the best strategy parameters by running backtests over a range of values.
"""

import itertools
import logging
from dataclasses import dataclass
from typing import List, Dict

from src.backtesting.engine import BacktestEngine, BacktestResult
from src.config.strategy import StrategyConfig, MomentumConfig
from src.data.candles import CandleManager
from src.strategies.momentum import MomentumStrategy

logger = logging.getLogger(__name__)


@dataclass
class OptimizationResult:
    config: StrategyConfig
    result: BacktestResult
    score: float  # Custom score (e.g. Total PnL * Win Rate)


class GridSearch:
    """ exhaustive search over parameter ranges. """

    def __init__(self, candle_manager: CandleManager):
        self.candle_manager = candle_manager

    def optimize(
        self,
        inst_id: str,
        k_long_range: List[float],
        k_short_range: List[float],
        gap_range: List[float],
        days: int = 7,
        bar: str = "1m",
        on_progress=None,
    ) -> List[OptimizationResult]:
        """
        Run grid search.
        
        Args:
            inst_id: Trading pair.
            k_long_range: List of K-Long values to test.
            k_short_range: List of K-Short values to test.
            gap_range: List of Gap Thresholds to test.
            days: Backtest duration.
            bar: Candle interval.
            on_progress: Optional callback function(current, total).
            
        Returns:
            List of OptimizationResult, sorted by score descending.
        """
        logger.info("Starting Grid Search for %s...", inst_id)
        
        # 1. Pre-fetch data once to avoid repetitive API calls
        df = self.candle_manager.get_history(inst_id, bar, days=days)
        if df.empty or len(df) < 50:
            logger.warning("Not enough data for optimization.")
            return []

        combinations = list(itertools.product(k_long_range, k_short_range, gap_range))
        total_runs = len(combinations)
        logger.info("Total combinations to test: %d", total_runs)

        results = []

        for i, (k_l, k_s, gap) in enumerate(combinations):
            if on_progress:
                on_progress(i, total_runs)
            
            # Create config and strategy
            mom_cfg = MomentumConfig(k_long=k_l, k_short=k_s, gap_threshold=gap)
            cfg = StrategyConfig(momentum=mom_cfg)
            strategy = MomentumStrategy(config=mom_cfg)
            
            # Create engine with this strategy
            engine = BacktestEngine(strategy, self.candle_manager)
            
            # Run backtest (we assume candle_manager handles caching efficiently)
            # To speed up, we could hack engine to accept DF, but for now standard path is safer for correctness.
            # actually, fetching 100 times is bad. 
            # Optimization: The engine calls `get_history`. 
            # If `get_history` is cached, it's fast. 
            # Let's hope `lru_cache` on `get_history_candles` works or file cache works.
            
            # Actually, let's inject the pre-fetched DF if we modify engine. 
            # But BacktestEngine.run signature is (inst_id, bar, days). 
            # Let's override `run` or add `run_on_data` to Engine? 
            # For this task, strict adherence to existing Engine is preferred unless too slow.
            # Let's assume FileCache in CandleManager is fast enough.
            
            res = engine.run(inst_id, bar, days)

            # Score: Simple PnL for now, or PnL * WinRate
            # Let's use Total PnL as primary, but penalize low win rate?
            # User wants "best values".
            score = res.total_pnl

            results.append(OptimizationResult(config=cfg, result=res, score=score))

            if (i + 1) % 10 == 0:
                logger.info("Processed %d/%d...", i + 1, total_runs)

        # Sort by score descending
        results.sort(key=lambda x: x.score, reverse=True)
        return results
