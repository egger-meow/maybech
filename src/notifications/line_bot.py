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
import re
from linebot.v3.messaging import (
    MessagingApi,
    ApiClient,
    Configuration,
    PushMessageRequest,
    ReplyMessageRequest,
    TextMessage,
)

from src.config.settings import settings
from src.notifications.repeat_cooldown import RepeatCooldownTracker

logger = logging.getLogger(__name__)


class LineBotNotifier:
    """Sends push notifications via LINE Messaging API."""

    def __init__(self) -> None:
        self.last_error = ""
        self.suppressed_by_cooldown = False
        self._cooldown = RepeatCooldownTracker(max(0, settings.NOTIFICATION_COOLDOWN_SECONDS))
        self._last_fingerprint: str | None = None
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
            self.last_error = type(e).__name__
            self.enabled = False

    def send(self, message: str) -> bool:
        """Send a text message to the configured LINE user.

        Returns True on success, False otherwise.
        """
        self.suppressed_by_cooldown = False
        if not self.enabled:
            return False

        fingerprint = re.sub(r"\s+", " ", message.strip()).casefold()
        if not self._cooldown.ready(fingerprint):
            logger.info("Equivalent LINE notification suppressed by cooldown.")
            self.suppressed_by_cooldown = True
            return False

        try:
            request = PushMessageRequest(
                to=self._user_id,
                messages=[TextMessage(text=message)]
            )
            self._api.push_message(request)
            self._cooldown.record_sent(fingerprint)
            self._last_fingerprint = fingerprint
            self.last_error = ""
            logger.info("LINE message sent successfully.")
            return True
        except Exception as e:
            # Avoid logging the sensitive tokens/ID which might be in the exception repr
            logger.error(f"Failed to send LINE message: {e}")
            self.last_error = type(e).__name__
            return False

    def mute_last_repeat(self) -> bool:
        """Stop the most recently delivered message from repeating again.

        Scoped to the exact message content (not the whole channel), so an
        unrelated later alert still comes through normally. Returns False if
        nothing has been sent yet this process to mute.
        """
        if self._last_fingerprint is None:
            return False
        self._cooldown.mute(self._last_fingerprint)
        return True

    def reply(self, reply_token: str, message: str) -> bool:
        """Reply to an inbound message using its reply token (valid ~1 minute).

        Unlike send(), this is not subject to the push cooldown — replies are
        synchronous 1:1 responses to a specific inbound event, not repeated alerts.
        """
        if not self.enabled:
            return False
        try:
            request = ReplyMessageRequest(
                reply_token=reply_token,
                messages=[TextMessage(text=message)],
            )
            self._api.reply_message(request)
            self.last_error = ""
            return True
        except Exception as e:
            logger.error(f"Failed to reply to LINE message: {e}")
            self.last_error = type(e).__name__
            return False
