"""
CandleMiner — extensible candle feature extraction framework.

CandleMiner takes a DataFrame of OHLCV candles and runs registered
Feature instances to produce a dict of extracted characteristics.

Usage:
    miner = CandleMiner()
    miner.register(PeakValley(window=3))
    result = miner.mine(candles_df)
    # result == {"peak_valley": [{"price": ..., "sharpness": ..., ...}, ...]}
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)


class Feature(ABC):
    """Base class for all candle features.

    Subclasses must set a class-level ``name`` attribute (the key used
    in the CandleMiner output dict) and implement ``extract``.
    """

    name: str

    @abstractmethod
    def extract(self, candles: pd.DataFrame) -> Any:
        """Extract this feature from *candles* and return the result.

        Parameters
        ----------
        candles : pd.DataFrame
            Must contain at least the columns: timestamp, open, high,
            low, close, volume.  Rows are sorted ascending by time.

        Returns
        -------
        Any
            Feature-specific result (list, dict, scalar, etc.).
        """

    def __repr__(self) -> str:
        return f"{type(self).__name__}(name={self.name!r})"


class CandleMiner:
    """Orchestrates feature extraction over candle data.

    Register one or more :class:`Feature` instances, then call
    :meth:`mine` to run them all and collect the results.
    """

    def __init__(self) -> None:
        self._features: dict[str, Feature] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, feature: Feature) -> None:
        """Register a *feature* instance.  Overwrites if name collides."""
        if feature.name in self._features:
            logger.warning(
                "Overwriting existing feature %r with %r",
                self._features[feature.name],
                feature,
            )
        self._features[feature.name] = feature
        logger.info("Registered feature: %s", feature)

    def unregister(self, name: str) -> None:
        """Remove a feature by *name*.  Raises KeyError if not found."""
        removed = self._features.pop(name)
        logger.info("Unregistered feature: %s", removed)

    @property
    def feature_names(self) -> list[str]:
        """Return the names of all registered features."""
        return list(self._features.keys())

    def __len__(self) -> int:
        return len(self._features)

    def __contains__(self, name: str) -> bool:
        return name in self._features

    # ------------------------------------------------------------------
    # Mining
    # ------------------------------------------------------------------

    def mine(self, candles: pd.DataFrame) -> dict[str, Any]:
        """Run every registered feature on *candles*.

        Returns a dict mapping feature name → extracted result.
        Features that raise are logged and skipped (their key is absent
        from the output).
        """
        results: dict[str, Any] = {}
        for name, feature in self._features.items():
            try:
                results[name] = feature.extract(candles)
            except Exception:
                logger.exception("Feature %r failed", name)
        return results
