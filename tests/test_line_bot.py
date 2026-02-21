"""
Tests for the real v3 LINE Bot implementation.
"""

from unittest.mock import MagicMock, patch
import pytest
from src.notifications.line_bot import LineBotNotifier


def test_line_bot_initialization_disabled():
    """Verify notifier is disabled when credentials are missing."""
    with patch("src.notifications.line_bot.settings") as mock_settings:
        mock_settings.LINE_CHANNEL_ACCESS_TOKEN = ""
        mock_settings.LINE_CHANNEL_SECRET = ""
        mock_settings.LINE_USER_ID = ""
        
        notifier = LineBotNotifier()
        assert not notifier.enabled


def test_line_bot_initialization_enabled():
    """Verify notifier initializes API when credentials are present."""
    with patch("src.notifications.line_bot.settings") as mock_settings:
        mock_settings.LINE_CHANNEL_ACCESS_TOKEN = "test_token"
        mock_settings.LINE_CHANNEL_SECRET = "test_secret"
        mock_settings.LINE_USER_ID = "test_user"
        
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
        
        with patch("src.notifications.line_bot.MessagingApi") as mock_api_class:
            mock_api = mock_api_class.return_value
            mock_api.push_message.side_effect = Exception("API Error")
            
            notifier = LineBotNotifier()
            result = notifier.send("Hello")
            
            assert result is False
            assert "Failed to send LINE message" in caplog.text


def test_line_bot_format_level_alert():
    """Verify the formatting of concentration/proximity alerts."""
    with patch("src.notifications.line_bot.settings"):
        notifier = LineBotNotifier()
        notifier.enabled = True
        notifier._api = MagicMock()
        notifier._user_id = "u123"
        
        notifier.send_level_alert(
            inst_id="BTC-USDT",
            timeframe="1H",
            price=50000.0,
            kind="peak",
            distance=10.5,
            level_price=50010.5,
            significance=0.95,
            count=10,
            purity=0.8
        )
        
        # Check that send was called with formatted string
        call_args = notifier._api.push_message.call_args[0][0]
        text = call_args.messages[0].text
        
        assert "BTC-USDT (1H)" in text
        assert "當前價格: $50,000.00" in text
        assert "目標位: $50,010.50" in text
        assert "重要性: 極高" in text
        assert "壓力位" in text
