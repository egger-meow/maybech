"""
OKX REST API client wrapper.

Wraps the `python-okx` SDK so the rest of the codebase never imports OKX
directly — makes it easy to mock in tests and swap exchanges later.
"""

from __future__ import annotations

import logging
import os

import okx.Account as Account
import okx.MarketData as MarketData
import okx.PublicData as PublicData
import okx.Trade as Trade

from src.config.settings import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Safety flag — set via env or at runtime to allow real order placement.
# When False, place_limit_order() will refuse to execute.
# ---------------------------------------------------------------------------
_ORDER_PLACEMENT_ARMED = os.getenv("MAYBECH_ARM_ORDERS", "0") == "1"


def arm_order_placement() -> None:
    """Explicitly arm order placement.  Call this ONLY when you are ready."""
    global _ORDER_PLACEMENT_ARMED  # noqa: PLW0603
    _ORDER_PLACEMENT_ARMED = True
    logger.warning("⚠️  Order placement is now ARMED — real orders CAN be sent.")


def disarm_order_placement() -> None:
    """Disarm order placement — the default safe state."""
    global _ORDER_PLACEMENT_ARMED  # noqa: PLW0603
    _ORDER_PLACEMENT_ARMED = False
    logger.info("🔒 Order placement DISARMED.")


def _extract(response: dict, *, label: str = "API call") -> list[dict]:
    """Extract ``data`` from an OKX response, raising on errors."""
    code = response.get("code")
    if code != "0":
        msg = response.get("msg", "unknown error")
        raise RuntimeError(f"OKX {label} failed (code={code}): {msg}")
    return response.get("data", [])


class OKXClient:
    """Thin wrapper around the python-okx REST client."""

    def __init__(self) -> None:
        api_key = settings.OKX_API_KEY
        api_secret = settings.OKX_API_SECRET
        passphrase = settings.OKX_PASSPHRASE
        flag = settings.OKX_FLAG  # "0" live, "1" demo

        self.account_api = Account.AccountAPI(
            api_key, api_secret, passphrase, False, flag,
        )
        self.market_api = MarketData.MarketAPI(flag=flag)
        self.public_api = PublicData.PublicAPI(flag=flag)
        self.trade_api = Trade.TradeAPI(
            api_key, api_secret, passphrase, False, flag,
        )

        self.flag = flag
        logger.info(
            "OKXClient initialised (demo=%s)",
            "YES" if flag == "1" else "NO",
        )

    # -- Account -------------------------------------------------------------

    def get_balance(self, ccy: str = "") -> list[dict]:
        """Fetch account balance across all (or specified) currencies."""
        resp = self.account_api.get_account_balance(ccy=ccy)
        return _extract(resp, label="get_balance")

    def get_positions(
        self,
        inst_type: str = "SWAP",
        inst_id: str = "",
    ) -> list[dict]:
        """Fetch all open positions."""
        resp = self.account_api.get_positions(
            instType=inst_type, instId=inst_id,
        )
        return _extract(resp, label="get_positions")

    def get_account_config(self) -> list[dict]:
        """Fetch current account configuration."""
        resp = self.account_api.get_account_config()
        return _extract(resp, label="get_account_config")

    def get_fee_rates(
        self,
        inst_type: str = "SWAP",
        inst_id: str = "",
    ) -> list[dict]:
        """Fetch maker/taker fee rates."""
        resp = self.account_api.get_fee_rates(
            instType=inst_type, instId=inst_id,
        )
        return _extract(resp, label="get_fee_rates")

    def get_instruments(
        self,
        inst_type: str = "SWAP",
        inst_id: str = "",
    ) -> list[dict]:
        """Fetch public instrument constraints used before order submission."""
        resp = self.public_api.get_instruments(instType=inst_type, instId=inst_id)
        return _extract(resp, label="get_instruments")

    def get_interest_limits(self, ccy: str = "") -> list[dict]:
        """Fetch borrow interest rate and limit."""
        resp = self.account_api.get_interest_limits(ccy=ccy)
        return _extract(resp, label="get_interest_limits")

    # -- Market Data ---------------------------------------------------------

    def get_candles(
        self,
        inst_id: str,
        bar: str = "1m",
        limit: str = "100",
        after: str = "",
        before: str = "",
    ) -> list[list]:
        """Fetch recent candlestick data.

        Returns list of [ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm].
        """
        resp = self.market_api.get_candlesticks(
            instId=inst_id, bar=bar, limit=limit,
            after=after, before=before,
        )
        return _extract(resp, label="get_candles")

    def get_history_candles(
        self,
        inst_id: str,
        bar: str = "1m",
        limit: str = "100",
        after: str = "",
        before: str = "",
    ) -> list[list]:
        """Fetch historical candlestick data for analysis."""
        resp = self.market_api.get_history_candlesticks(
            instId=inst_id, bar=bar, limit=limit,
            after=after, before=before,
        )
        return _extract(resp, label="get_history_candles")

    def get_ticker(self, inst_id: str) -> list[dict]:
        """Fetch latest price snapshot for an instrument."""
        resp = self.market_api.get_ticker(instId=inst_id)
        return _extract(resp, label="get_ticker")

    # -- Order History (read-only) -------------------------------------------

    def get_order_history(
        self,
        inst_type: str = "SWAP",
        limit: str = "20",
    ) -> list[dict]:
        """Fetch completed order history (last 7 days)."""
        resp = self.trade_api.get_orders_history(
            instType=inst_type, limit=limit,
        )
        return _extract(resp, label="get_order_history")

    def get_fills(
        self,
        inst_type: str = "SWAP",
        limit: str = "100",
        after: str = "",
    ) -> list[dict]:
        """Fetch recent authenticated fills for restart-safe ingestion."""
        resp = self.trade_api.get_fills(
            instType=inst_type,
            limit=limit,
            after=after,
        )
        return _extract(resp, label="get_fills")

    def get_order(self, inst_id: str, order_id: str) -> list[dict]:
        """Fetch one authenticated order for pending-state reconciliation."""
        resp = self.trade_api.get_order(instId=inst_id, ordId=order_id)
        return _extract(resp, label="get_order")

    # -- Trading (PROTECTED) -------------------------------------------------

    def place_limit_order(
        self,
        inst_id: str,
        side: str,
        sz: str,
        px: str,
        td_mode: str = "cross",
        *,
        tp_trigger_px: str = "",
        tp_ord_px: str = "",
        sl_trigger_px: str = "",
        sl_ord_px: str = "",
        confirm: bool = False,
    ) -> dict:
        """Place a LIMIT order (限價單) with optional TP/SL.

        ⚠️  SAFETY GUARDS (all must pass):
        1. ``_ORDER_PLACEMENT_ARMED`` must be True
           (call ``arm_order_placement()`` or set env MAYBECH_ARM_ORDERS=1).
        2. ``confirm`` kwarg must be explicitly ``True``.
        3. ``side`` must be 'buy' or 'sell'.
        4. ``sz`` must be a positive number.
        5. ``px`` must be a positive number.

        Parameters
        ----------
        inst_id : e.g. "ETH-USDT"
        side    : "buy" or "sell"
        sz      : order size as string, e.g. "0.1"
        px      : limit price as string, e.g. "2700.5"
        td_mode : trade mode — "cash" (spot), "cross", "isolated"
        tp_trigger_px / tp_ord_px : take-profit trigger and order price
        sl_trigger_px / sl_ord_px : stop-loss trigger and order price
        confirm : must be True to actually send the order
        """
        # --- guard 1: global arm ---
        if not _ORDER_PLACEMENT_ARMED:
            raise PermissionError(
                "Order placement is DISARMED. "
                "Call arm_order_placement() or set MAYBECH_ARM_ORDERS=1 first."
            )

        # --- guard 2: explicit confirm ---
        if not confirm:
            raise ValueError(
                "You must pass confirm=True to place a real order. "
                "This is a safety measure to prevent accidental trades."
            )

        # --- guard 3: side validation ---
        side = side.lower()
        if side not in ("buy", "sell"):
            raise ValueError(f"side must be 'buy' or 'sell', got '{side}'")

        # --- guard 4 & 5: numeric validation ---
        if float(sz) <= 0:
            raise ValueError(f"sz must be positive, got '{sz}'")
        if float(px) <= 0:
            raise ValueError(f"px must be positive, got '{px}'")

        logger.warning(
            "📤 PLACING LIMIT ORDER: %s %s %s @ %s (mode=%s, tp=%s, sl=%s)",
            side.upper(), sz, inst_id, px, td_mode,
            tp_trigger_px or "none", sl_trigger_px or "none",
        )

        resp = self.trade_api.place_order(
            instId=inst_id,
            tdMode=td_mode,
            side=side,
            ordType="limit",
            sz=sz,
            px=px,
            tpTriggerPx=tp_trigger_px,
            tpOrdPx=tp_ord_px,
            slTriggerPx=sl_trigger_px,
            slOrdPx=sl_ord_px,
        )
        data = _extract(resp, label="place_limit_order")
        logger.info("✅ Order placed: %s", data)
        return data[0] if data else {}

    def cancel_order(self, inst_id: str, order_id: str) -> dict:
        """Cancel an existing order."""
        resp = self.trade_api.cancel_order(instId=inst_id, ordId=order_id)
        data = _extract(resp, label="cancel_order")
        return data[0] if data else {}

    def place_reduce_market_order(
        self,
        *,
        inst_id: str,
        position_side: str,
        sz: str,
        td_mode: str = "cross",
        pos_side: str = "",
        confirm: bool = False,
    ) -> dict:
        """Submit a reduce-only market order for an existing position."""
        if not _ORDER_PLACEMENT_ARMED:
            raise PermissionError(
                "Order placement is DISARMED. "
                "Call arm_order_placement() or set MAYBECH_ARM_ORDERS=1 first."
            )
        if not confirm:
            raise ValueError("confirm=True is required for a live reduce order")
        normalized_side = position_side.lower()
        if normalized_side not in {"long", "short"}:
            raise ValueError("position_side must be 'long' or 'short'")
        if float(sz) <= 0:
            raise ValueError("reduce order size must be positive")

        side = "sell" if normalized_side == "long" else "buy"
        response = self.trade_api.place_order(
            instId=inst_id,
            tdMode=td_mode,
            side=side,
            ordType="market",
            sz=sz,
            posSide=pos_side,
            reduceOnly="true",
        )
        data = _extract(response, label="place_reduce_market_order")
        return data[0] if data else {}
