"""
OKX REST API client wrapper.

Wraps the `python-okx` SDK so the rest of the codebase never imports OKX
directly — makes it easy to mock in tests and swap exchanges later.
"""

from __future__ import annotations

import logging
import re

import okx.Account as Account
import okx.MarketData as MarketData
import okx.PublicData as PublicData
import okx.Trade as Trade

from src.config.settings import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Process-local safety flag set only after runtime live preflight.
# When False, place_limit_order() will refuse to execute.
# ---------------------------------------------------------------------------
_ORDER_PLACEMENT_ARMED = False
_ENTRY_ORDER_PLACEMENT_ENABLED = False
_CLIENT_ORDER_ID_PATTERN = re.compile(r"^[A-Za-z0-9]{1,32}$")


def arm_order_placement(*, preflight_passed: bool = False) -> None:
    """Arm order placement only after runtime preflight succeeds."""
    if not preflight_passed:
        raise PermissionError("Order placement can only be armed after live preflight")
    global _ORDER_PLACEMENT_ARMED  # noqa: PLW0603
    _ORDER_PLACEMENT_ARMED = True
    logger.warning("Order placement is ARMED after successful live preflight")


def enable_entry_order_placement() -> None:
    """Enable entries only while the process remains globally armed."""
    if not _ORDER_PLACEMENT_ARMED:
        raise PermissionError("Entry placement requires an armed live runtime")
    global _ENTRY_ORDER_PLACEMENT_ENABLED  # noqa: PLW0603
    _ENTRY_ORDER_PLACEMENT_ENABLED = True
    logger.warning("Strategy entry order placement is ENABLED")


def disable_entry_order_placement() -> None:
    """Disable entry orders without affecting reduce-only closes."""
    global _ENTRY_ORDER_PLACEMENT_ENABLED  # noqa: PLW0603
    _ENTRY_ORDER_PLACEMENT_ENABLED = False
    logger.warning("Strategy entry order placement is DISABLED")


def entry_order_placement_enabled() -> bool:
    return _ORDER_PLACEMENT_ARMED and _ENTRY_ORDER_PLACEMENT_ENABLED


def disarm_order_placement() -> None:
    """Disarm order placement — the default safe state."""
    global _ORDER_PLACEMENT_ARMED, _ENTRY_ORDER_PLACEMENT_ENABLED  # noqa: PLW0603
    _ORDER_PLACEMENT_ARMED = False
    _ENTRY_ORDER_PLACEMENT_ENABLED = False
    logger.info("🔒 Order placement DISARMED.")


def _extract(response: dict, *, label: str = "API call") -> list[dict]:
    """Extract ``data`` from an OKX response, raising on errors."""
    code = response.get("code")
    if code != "0":
        msg = response.get("msg", "unknown error")
        raise RuntimeError(f"OKX {label} failed (code={code}): {msg}")
    return response.get("data", [])


def _accepted_order_result(response: dict, *, label: str) -> dict:
    data = _extract(response, label=label)
    if len(data) != 1 or not isinstance(data[0], dict):
        raise RuntimeError(f"OKX {label} returned no single order result")
    result = data[0]
    status_code = str(result.get("sCode") or "")
    if status_code != "0":
        message = str(result.get("sMsg") or "unknown order error")
        raise RuntimeError(
            f"OKX {label} rejected order (sCode={status_code or 'missing'}): {message}"
        )
    if not str(result.get("ordId") or ""):
        raise RuntimeError(f"OKX {label} accepted response is missing ordId")
    return result


def _accepted_algo_result(response: dict, *, label: str) -> dict:
    data = _extract(response, label=label)
    if len(data) != 1 or not isinstance(data[0], dict):
        raise RuntimeError(f"OKX {label} returned no single algo-order result")
    result = data[0]
    status_code = str(result.get("sCode") or "")
    if status_code != "0":
        message = str(result.get("sMsg") or "unknown algo-order error")
        raise RuntimeError(
            f"OKX {label} rejected algo order "
            f"(sCode={status_code or 'missing'}): {message}"
        )
    if not str(result.get("algoId") or ""):
        raise RuntimeError(f"OKX {label} accepted response is missing algoId")
    return result


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

    def get_leverage(self, inst_id: str, mgn_mode: str = "cross") -> list[dict]:
        """Fetch configured leverage for an instrument and margin mode."""
        resp = self.account_api.get_leverage(instId=inst_id, mgnMode=mgn_mode)
        return _extract(resp, label="get_leverage")

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

    def get_pending_orders(self, inst_type: str = "SWAP") -> list[dict]:
        """Fetch all incomplete orders used by account exposure checks."""
        resp = self.trade_api.get_order_list(instType=inst_type)
        return _extract(resp, label="get_pending_orders")

    def get_pending_algo_orders(
        self,
        *,
        inst_id: str = "",
        ord_type: str = "conditional",
    ) -> list[dict]:
        """Fetch active algo orders used to prove exchange-side protection."""
        resp = self.trade_api.order_algos_list(
            ordType=ord_type,
            instType="SWAP",
            instId=inst_id,
        )
        return _extract(resp, label="get_pending_algo_orders")

    def get_fills_history(
        self,
        inst_type: str = "SWAP",
        limit: str = "100",
        after: str = "",
    ) -> list[dict]:
        """Fetch up to three months of fills, paginated by OKX bill ID."""
        resp = self.trade_api.get_fills_history(
            instType=inst_type,
            limit=limit,
            after=after,
        )
        return _extract(resp, label="get_fills_history")

    def get_order(
        self,
        inst_id: str,
        order_id: str = "",
        client_order_id: str = "",
    ) -> list[dict]:
        """Fetch one authenticated order for pending-state reconciliation."""
        if not order_id and not client_order_id:
            raise ValueError("order_id or client_order_id is required")
        params = {"instId": inst_id}
        if order_id:
            params["ordId"] = order_id
        else:
            _validate_client_order_id(client_order_id)
            params["clOrdId"] = client_order_id
        resp = self.trade_api.get_order(**params)
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
        client_order_id: str = "",
        attach_algo_client_order_id: str = "",
        order_type: str = "limit",
        confirm: bool = False,
    ) -> dict:
        """Place a guarded limit or fill-or-kill entry with optional TP/SL.

        Safety guards (all must pass):
        1. ``_ORDER_PLACEMENT_ARMED`` must be True after live preflight.
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
        td_mode : trade mode, such as "cross" or "isolated"
        tp_trigger_px / tp_ord_px : take-profit trigger and order price
        sl_trigger_px / sl_ord_px : stop-loss trigger and order price
        confirm : must be True to actually send the order
        """
        # --- guard 1: global arm ---
        if not _ORDER_PLACEMENT_ARMED:
            raise PermissionError(
                "Order placement is DISARMED. "
                "Start the runtime with --live and pass live preflight first."
            )
        if not _ENTRY_ORDER_PLACEMENT_ENABLED:
            raise PermissionError("Strategy entry order placement is disabled")

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
        _validate_client_order_id(client_order_id)
        if order_type not in {"limit", "fok"}:
            raise ValueError("order_type must be 'limit' or 'fok'")
        if any((tp_trigger_px, sl_trigger_px)):
            _validate_client_order_id(attach_algo_client_order_id)

        attachments = []
        if tp_trigger_px or sl_trigger_px:
            attachment = {"attachAlgoClOrdId": attach_algo_client_order_id}
            if tp_trigger_px:
                attachment.update(
                    {
                        "tpTriggerPx": tp_trigger_px,
                        "tpOrdPx": tp_ord_px,
                        "tpTriggerPxType": "last",
                    }
                )
            if sl_trigger_px:
                attachment.update(
                    {
                        "slTriggerPx": sl_trigger_px,
                        "slOrdPx": sl_ord_px,
                        "slTriggerPxType": "last",
                    }
                )
            attachments.append(attachment)

        logger.warning(
            "PLACING %s ORDER: %s %s %s @ %s (mode=%s, tp=%s, sl=%s)",
            order_type.upper(), side.upper(), sz, inst_id, px, td_mode,
            tp_trigger_px or "none", sl_trigger_px or "none",
        )

        resp = self.trade_api.place_order(
            instId=inst_id,
            tdMode=td_mode,
            side=side,
            ordType=order_type,
            clOrdId=client_order_id,
            sz=sz,
            px=px,
            stpMode="cancel_taker",
            attachAlgoOrds=attachments or None,
        )
        data = [_accepted_order_result(resp, label="place_limit_order")]
        logger.info("Order placed: %s", data)
        return data[0]

    def cancel_order(self, inst_id: str, order_id: str) -> dict:
        """Cancel an existing order."""
        resp = self.trade_api.cancel_order(instId=inst_id, ordId=order_id)
        return _accepted_order_result(resp, label="cancel_order")

    def place_reduce_market_order(
        self,
        *,
        inst_id: str,
        position_side: str,
        sz: str,
        td_mode: str = "cross",
        pos_side: str = "",
        client_order_id: str = "",
        confirm: bool = False,
    ) -> dict:
        """Submit a reduce-only market order for an existing position."""
        if not _ORDER_PLACEMENT_ARMED:
            raise PermissionError(
                "Order placement is DISARMED. "
                "Start the runtime with --live and pass live preflight first."
            )
        if not confirm:
            raise ValueError("confirm=True is required for a live reduce order")
        normalized_side = position_side.lower()
        if normalized_side not in {"long", "short"}:
            raise ValueError("position_side must be 'long' or 'short'")
        if float(sz) <= 0:
            raise ValueError("reduce order size must be positive")
        _validate_client_order_id(client_order_id)

        side = "sell" if normalized_side == "long" else "buy"
        response = self.trade_api.place_order(
            instId=inst_id,
            tdMode=td_mode,
            side=side,
            ordType="market",
            clOrdId=client_order_id,
            sz=sz,
            posSide=pos_side,
            reduceOnly="true",
        )
        return _accepted_order_result(response, label="place_reduce_market_order")

    def place_position_stop(
        self,
        *,
        inst_id: str,
        position_side: str,
        sz: str,
        stop_trigger_px: str,
        algo_client_order_id: str,
        td_mode: str = "cross",
        pos_side: str = "net",
        confirm: bool = False,
    ) -> dict:
        """Place a reduce-only conditional market stop for existing exposure."""
        if not _ORDER_PLACEMENT_ARMED:
            raise PermissionError(
                "Order placement is DISARMED. Start the runtime with --live "
                "and pass live preflight first."
            )
        if not confirm:
            raise ValueError("confirm=True is required for exchange protection")
        normalized_side = position_side.lower()
        if normalized_side not in {"long", "short"}:
            raise ValueError("position_side must be 'long' or 'short'")
        if float(sz) <= 0:
            raise ValueError("stop size must be positive")
        if float(stop_trigger_px) <= 0:
            raise ValueError("stop trigger price must be positive")
        _validate_client_order_id(algo_client_order_id)

        response = self.trade_api.place_algo_order(
            instId=inst_id,
            tdMode=td_mode,
            side="sell" if normalized_side == "long" else "buy",
            ordType="conditional",
            sz=sz,
            posSide=pos_side,
            reduceOnly="true",
            slTriggerPx=stop_trigger_px,
            slOrdPx="-1",
            slTriggerPxType="last",
            algoClOrdId=algo_client_order_id,
        )
        return _accepted_algo_result(response, label="place_position_stop")

    def amend_position_stop(
        self,
        *,
        inst_id: str,
        algo_id: str,
        sz: str,
        stop_trigger_px: str,
        confirm: bool = False,
    ) -> dict:
        """Resize or reprice a known protective stop without replacing its identity."""
        if not _ORDER_PLACEMENT_ARMED:
            raise PermissionError(
                "Order placement is DISARMED. Start the runtime with --live "
                "and pass live preflight first."
            )
        if not confirm:
            raise ValueError("confirm=True is required to amend exchange protection")
        if not algo_id:
            raise ValueError("algo_id is required")
        if float(sz) <= 0 or float(stop_trigger_px) <= 0:
            raise ValueError("stop size and trigger price must be positive")
        response = self.trade_api.amend_algo_order(
            instId=inst_id,
            algoId=algo_id,
            newSz=sz,
            newSlTriggerPx=stop_trigger_px,
            newSlOrdPx="-1",
            newSlTriggerPxType="last",
        )
        return _accepted_algo_result(response, label="amend_position_stop")


def _validate_client_order_id(client_order_id: str) -> None:
    if not _CLIENT_ORDER_ID_PATTERN.fullmatch(client_order_id):
        raise ValueError("client_order_id must be 1-32 ASCII alphanumeric characters")
