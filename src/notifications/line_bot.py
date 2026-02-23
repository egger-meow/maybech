"""
LINE Bot notification integration.

=== SETUP GUIDE ===

1. Go to https://developers.line.biz/console/ and create a LINE account / login.

2. Create a new **Provider** (e.g. "Maybech Trading").

3. Under the provider, create a new **Messaging API Channel**.
   - Fill in channel name, description, category, etc.

4. On the channel's **Basic settings** tab, copy:
   - Channel secret  → paste into .env as LINE_CHANNEL_SECRET

5. On the **Messaging API** tab:
   - Issue a **Channel access token (long-lived)** → paste into .env as LINE_CHANNEL_ACCESS_TOKEN

6. To get your user ID:
   - On the **Basic settings** tab, find **Your user ID** → paste into .env as LINE_USER_ID
   - Alternatively, add the bot as a friend (scan QR code on the Messaging API tab),
     send it any message, and use the webhook to capture your user ID.

7. (Optional) Disable auto-reply messages and greeting messages
   in the LINE Official Account Manager to keep the bot clean.

8. Install the SDK: `pip install line-bot-sdk` (already in requirements.txt).

That's it! The bot will push messages directly to your LINE_USER_ID.
No webhook server is needed for push-only notifications.

=====================
"""

import logging
from linebot.v3.messaging import MessagingApi, ApiClient, Configuration, PushMessageRequest, TextMessage

from src.config.settings import settings

logger = logging.getLogger(__name__)


class LineBotNotifier:
    """Sends push notifications via LINE Messaging API."""

    def __init__(self) -> None:
        self.enabled = bool(
            settings.LINE_CHANNEL_ACCESS_TOKEN
            and settings.LINE_CHANNEL_SECRET
            and settings.LINE_USER_ID
        )
        if not self.enabled:
            logger.warning("LINE Bot credentials not fully configured. Notifications disabled.")
            return

        try:
            config = Configuration(access_token=settings.LINE_CHANNEL_ACCESS_TOKEN)
            self._api = MessagingApi(ApiClient(config))
            self._user_id = settings.LINE_USER_ID
        except Exception as e:
            logger.error(f"Failed to initialize LINE Bot API: {e}")
            self.enabled = False

    def send(self, message: str) -> bool:
        """Send a text message to the configured LINE user.

        Returns True on success, False otherwise.
        """
        if not self.enabled:
            return False

        try:
            request = PushMessageRequest(
                to=self._user_id,
                messages=[TextMessage(text=message)]
            )
            self._api.push_message(request)
            logger.info("LINE message sent successfully.")
            return True
        except Exception as e:
            # Avoid logging the sensitive tokens/ID which might be in the exception repr
            logger.error(f"Failed to send LINE message: {e}")
            return False

    def send_trade_alert(self, inst_id: str, direction: str, entry: float, sl: float, tp: float) -> bool:
        """Send a formatted trade alert message in Traditional Chinese."""
        direction_tw = "多 (Long)" if direction.upper() == "LONG" else "空 (Short)"
        msg = (
            f"🔔 交易訊號通知\n"
            f"交易對: {inst_id}\n"
            f"方向: {direction_tw}\n"
            f"進場價: {entry:,.2f}\n"
            f"止損: {sl:,.2f} | 止盈: {tp:,.2f}"
        )
        return self.send(msg)

    def send_level_alert(self, 
                         inst_id: str, 
                         timeframe: str, 
                         price: float, 
                         kind: str, 
                         distance: float,
                         level_price: float,
                         significance: float,
                         count: int,
                         purity: float) -> bool:
        """Send an enhanced price proximity alert in Traditional Chinese."""
        # Mapping kind to Trad. Chinese
        kind_map = {
            "peak": "壓力位 (Resistance)",
            "valley": "支撐位 (Support)",
            "mixed": "壓力/支撐(混合)區"
        }
        kind_tw = kind_map.get(kind, kind)
        icon = "📈" if kind == "peak" else "📉" if kind == "valley" else "⚖️"

        # Significance level mapping
        if significance >= 0.8:
            sig_tw = "極高 (Very High)"
        elif significance >= 0.6:
            sig_tw = "高 (High)"
        elif significance >= 0.4:
            sig_tw = "中 (Medium)"
        else:
            sig_tw = "低 (Low)"

        msg = (
            f"{icon} 價格預警: {inst_id} ({timeframe})\n"
            f"當前價格正接近重要的 {kind_tw}\n\n"
            f"📍 當前價格: ${price:,.2f}\n"
            f"🎯 目標位: ${level_price:,.2f}\n"
            f"📏 距離: ${distance:,.2f}\n"
            f"📊 重要性: {sig_tw} ({significance:.2f})\n"
            f"🔢 確認次數: {count}\n"
            f"✨ 純度: {purity:.1%}"
        )
        return self.send(msg)

    def send_fluctuation_alert(self, 
                             inst_id: str, 
                             minutes: int, 
                             pct_change: float, 
                             threshold: float, 
                             direction: str, 
                             start_price: float, 
                             end_price: float) -> bool:
        """Send a rapid price fluctuation alert in Traditional Chinese."""
        direction_tw = "急漲 (Surge) 🚀" if direction == "up" else "急跌 (Plunge) 💥"
        
        msg = (
            f"⚠️ 價格劇烈波動預警: {inst_id}\n"
            f"觸發條件: {minutes} 分鐘內波動超過 {threshold}%\n\n"
            f"方向: {direction_tw}\n"
            f"實際幅寬: {pct_change:+.2f}%\n"
            f"起始價格: ${start_price:,.2f}\n"
            f"當前價格: ${end_price:,.2f}"
        )
        return self.send(msg)

    def send_performance_update(self, win_rate: float, total_return: float) -> bool:
        """Send a periodic performance summary in Traditional Chinese."""
        msg = (
            f"📊 績效更新報告\n"
            f"勝率: {win_rate:.1%}\n"
            f"總投報率: {total_return:.2%}"
        )
        return self.send(msg)
