from src.market.open_interest_history import fetch_open_interest_history, parse_open_interest_history


def test_parse_open_interest_history_sorts_ascending_and_types_fields():
    raw_rows = [
        ["1700000600000", "200", "20.0", "1300000.0"],
        ["1700000000000", "100", "10.0", "650000.0"],
    ]

    points = parse_open_interest_history(raw_rows)

    assert [p["oi_contracts"] for p in points] == [100.0, 200.0]
    assert points[0]["observed_at"] < points[1]["observed_at"]
    assert points[0]["oi_ccy"] == 10.0
    assert points[0]["oi_usd"] == 650000.0


def test_parse_open_interest_history_skips_malformed_rows():
    raw_rows = [
        ["1700000000000", "100", "10.0", "650000.0"],
        ["not-a-timestamp", "100", "10.0", "650000.0"],
        ["1700000600000"],  # too short
        "not-a-row",
    ]

    points = parse_open_interest_history(raw_rows)

    assert len(points) == 1


def test_parse_open_interest_history_handles_empty_input():
    assert parse_open_interest_history([]) == []


class _FakeClient:
    def __init__(self, rows):
        self._rows = rows
        self.call_args = None

    def get_open_interest_history(self, inst_id, *, period, limit):
        self.call_args = (inst_id, period, limit)
        return self._rows


def test_fetch_open_interest_history_passes_through_and_parses():
    client = _FakeClient([["1700000000000", "100", "10.0", "650000.0"]])

    points = fetch_open_interest_history(client, "ETH-USDT-SWAP", period="1D", limit=50)

    assert client.call_args == ("ETH-USDT-SWAP", "1D", "50")
    assert len(points) == 1
    assert points[0]["oi_contracts"] == 100.0
