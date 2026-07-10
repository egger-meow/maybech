from src.api.line_commands import ParsedCommand, parse_command


def test_parse_read_commands_case_insensitive():
    assert parse_command("status") == ParsedCommand("status")
    assert parse_command("  STATUS  ") == ParsedCommand("status")
    assert parse_command("health") == ParsedCommand("status")
    assert parse_command("Strategies") == ParsedCommand("strategies")


def test_parse_toggle_commands_capture_argument():
    assert parse_command("enable strategy-a") == ParsedCommand("enable", "strategy-a")
    assert parse_command("disable   strategy-b  ") == ParsedCommand("disable", "strategy-b")


def test_parse_toggle_command_without_argument():
    assert parse_command("enable") == ParsedCommand("enable", None)


def test_parse_unrecognized_text_returns_none():
    assert parse_command("hello there") is None
    assert parse_command("") is None
    assert parse_command("   ") is None
