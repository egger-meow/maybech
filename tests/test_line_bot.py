"""
Tests for the real v3 LINE Bot implementation.
"""

from unittest.mock import MagicMock, patch
from src.notifications.line_bot import LineBotNotifier


def test_line_bot_initialization_disabled():
    """Verify notifier is disabled when credentials are missing."""
    with patch("src.notifications.line_bot.settings") as mock_settings:
        mock_settings.LINE_CHANNEL_ACCESS_TOKEN = ""
        mock_settings.LINE_CHANNEL_SECRET = ""
        mock_settings.LINE_USER_ID = ""
        mock_settings.NOTIFICATION_COOLDOWN_SECONDS = 300
        
        notifier = LineBotNotifier()
        assert not notifier.enabled


def test_line_bot_initialization_enabled():
    """Verify notifier initializes API when credentials are present."""
    with patch("src.notifications.line_bot.settings") as mock_settings:
        mock_settings.LINE_CHANNEL_ACCESS_TOKEN = "test_token"
        mock_settings.LINE_CHANNEL_SECRET = "test_secret"
        mock_settings.LINE_USER_ID = "test_user"
        mock_settings.NOTIFICATION_COOLDOWN_SECONDS = 300
        
        with patch("src.notifications.line_bot.ApiClient"), \
             patch("src.notifications.line_bot.MessagingApi") as mock_api:
            notifier = LineBotNotifier()
            assert notifier.enabled
            assert notifier._user_id == "test_user"


def test_line_bot_send_failure(caplog):
    """Verify send returns False and logs error on API exception."""
    with patch("src.notifications.line_bot.settings") as mock_settings:
        mock_settings.LINE_CHANNEL_ACCESS_TOKEN = "tok"
        mock_settings.LINE_CHANNEL_SECRET = "sec"
        mock_settings.LINE_USER_ID = "usr"
        mock_settings.NOTIFICATION_COOLDOWN_SECONDS = 300
        
        with patch("src.notifications.line_bot.MessagingApi") as mock_api_class:
            mock_api = mock_api_class.return_value
            mock_api.push_message.side_effect = Exception("API Error")
            
            notifier = LineBotNotifier()
            result = notifier.send("Hello")
            
            assert result is False
            assert "Failed to send LINE message" in caplog.text


def test_line_bot_suppresses_equivalent_messages_during_cooldown():
    with patch("src.notifications.line_bot.settings") as mock_settings:
        mock_settings.NOTIFICATION_COOLDOWN_SECONDS = 300
        notifier = LineBotNotifier()
        notifier.enabled = True
        notifier._api = MagicMock()
        notifier._user_id = "u123"

        first = notifier.send("策略 breakout 已封鎖\n原因：風險上限")
        duplicate = notifier.send("  策略 breakout 已封鎖   原因：風險上限 ")

        assert first is True
        assert duplicate is False
        assert notifier._api.push_message.call_count == 1
