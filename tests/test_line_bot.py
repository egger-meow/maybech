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


def test_line_bot_mute_last_repeat_silences_only_that_message():
    with patch("src.notifications.line_bot.settings") as mock_settings:
        mock_settings.NOTIFICATION_COOLDOWN_SECONDS = 100
        notifier = LineBotNotifier()
        notifier.enabled = True
        notifier._api = MagicMock()
        notifier._user_id = "u123"

        assert notifier.send("執行環境／安全失敗：錯誤 A") is True
        assert notifier.mute_last_repeat() is True

        clock = {"t": 0.0}
        with patch(
            "src.notifications.repeat_cooldown.time.monotonic",
            side_effect=lambda: clock["t"],
        ):
            clock["t"] = 10_000
            # The muted message never comes back, no matter how much time passes.
            assert notifier.send("執行環境／安全失敗：錯誤 A") is False
            # A different message is unaffected.
            assert notifier.send("執行環境／安全失敗：錯誤 B") is True

        assert notifier._api.push_message.call_count == 2


def test_mute_last_repeat_with_nothing_sent_yet_returns_false():
    with patch("src.notifications.line_bot.settings") as mock_settings:
        mock_settings.NOTIFICATION_COOLDOWN_SECONDS = 100
        notifier = LineBotNotifier()
        notifier.enabled = True
        notifier._api = MagicMock()
        notifier._user_id = "u123"

        assert notifier.mute_last_repeat() is False


def test_line_bot_escalates_gap_for_a_persistently_repeating_message():
    with patch("src.notifications.line_bot.settings") as mock_settings:
        mock_settings.NOTIFICATION_COOLDOWN_SECONDS = 100
        notifier = LineBotNotifier()
        notifier.enabled = True
        notifier._api = MagicMock()
        notifier._user_id = "u123"

        clock = {"t": 0.0}
        with patch(
            "src.notifications.repeat_cooldown.time.monotonic",
            side_effect=lambda: clock["t"],
        ):
            for clock["t"] in (0, 100, 200):
                assert notifier.send("執行環境／安全失敗") is True

            # A 4th identical alert right at the plain 100s cooldown is still
            # suppressed; the gap has escalated to 200s (100 * 2) by now.
            clock["t"] = 300
            assert notifier.send("執行環境／安全失敗") is False
            clock["t"] = 400
            assert notifier.send("執行環境／安全失敗") is True

        assert notifier._api.push_message.call_count == 4
