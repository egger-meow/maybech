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

from src.config.settings import settings


class LineBotNotifier:
    """Sends push notifications via LINE Messaging API."""

    def __init__(self) -> None:
        self.enabled = bool(settings.LINE_CHANNEL_ACCESS_TOKEN)
        # TODO: initialise linebot.v3.messaging.ApiClient

    def send(self, message: str) -> None:
        """Send a text message to the configured LINE user.

        Does nothing if LINE credentials are not configured.
        """
        if not self.enabled:
            return
        raise NotImplementedError

    def send_trade_alert(self, inst_id: str, direction: str, entry: float, sl: float, tp: float) -> None:
        """Send a formatted trade alert message."""
        msg = (
            f"🔔 Trade Alert\n"
            f"Pair: {inst_id}\n"
            f"Direction: {direction}\n"
            f"Entry: {entry}\n"
            f"SL: {sl} | TP: {tp}"
        )
        self.send(msg)

    def send_performance_update(self, win_rate: float, total_return: float) -> None:
        """Send a periodic performance summary."""
        msg = (
            f"📊 Performance Update\n"
            f"Win rate: {win_rate:.1%}\n"
            f"Return: {total_return:.2%}"
        )
        self.send(msg)
