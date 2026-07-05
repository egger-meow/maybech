"""Explicit local-only instrument metadata for a fresh Simulation workspace."""

from __future__ import annotations


SIMULATION_SWAP_INSTRUMENTS: tuple[dict[str, str], ...] = (
    {
        "instId": "BTC-USDT-SWAP", "instType": "SWAP", "state": "live",
        "settleCcy": "USDT", "ctType": "linear", "ctVal": "0.01",
        "ctValCcy": "BTC", "ctMult": "1", "lotSz": "0.01",
        "minSz": "0.01", "tickSz": "0.1",
    },
    {
        "instId": "ETH-USDT-SWAP", "instType": "SWAP", "state": "live",
        "settleCcy": "USDT", "ctType": "linear", "ctVal": "0.1",
        "ctValCcy": "ETH", "ctMult": "1", "lotSz": "0.01",
        "minSz": "0.01", "tickSz": "0.01",
    },
    {
        "instId": "SOL-USDT-SWAP", "instType": "SWAP", "state": "live",
        "settleCcy": "USDT", "ctType": "linear", "ctVal": "1",
        "ctValCcy": "SOL", "ctMult": "1", "lotSz": "0.01",
        "minSz": "0.01", "tickSz": "0.01",
    },
    {
        "instId": "XRP-USDT-SWAP", "instType": "SWAP", "state": "live",
        "settleCcy": "USDT", "ctType": "linear", "ctVal": "100",
        "ctValCcy": "XRP", "ctMult": "1", "lotSz": "0.01",
        "minSz": "0.01", "tickSz": "0.0001",
    },
    {
        "instId": "DOGE-USDT-SWAP", "instType": "SWAP", "state": "live",
        "settleCcy": "USDT", "ctType": "linear", "ctVal": "1000",
        "ctValCcy": "DOGE", "ctMult": "1", "lotSz": "0.01",
        "minSz": "0.01", "tickSz": "0.00001",
    },
)
