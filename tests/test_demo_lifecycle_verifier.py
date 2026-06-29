from types import SimpleNamespace

import scripts.verify_okx_demo_lifecycle as verifier


class FakeClient:
    def get_order(self, inst_id, order_id="", client_order_id=""):
        return [
            {
                "ordId": order_id,
                "clOrdId": "demo-client-a",
                "state": "filled",
                "accFillSz": "0.02",
                "avgPx": "60000.1",
            }
        ]


class FakeAllocator:
    def __init__(self):
        self.fills = []

    def ingest(self, fill):
        self.fills.append(fill)
        return SimpleNamespace(execution_status="filled")


def test_demo_verifier_recovers_lagging_fill_from_authenticated_order(monkeypatch):
    monkeypatch.setattr(
        verifier,
        "_wait_for_fills",
        lambda client, order_id: (_ for _ in ()).throw(TimeoutError()),
    )
    allocator = FakeAllocator()

    statuses = verifier._ingest_order_fills(
        FakeClient(),
        allocator,
        "order-a",
    )

    assert statuses == ["filled"]
    assert len(allocator.fills) == 1
    fill = allocator.fills[0]
    assert fill.fill_id == "order-recovery:order-a"
    assert fill.exchange_order_id == "order-a"
    assert fill.quantity == 0.02
    assert fill.price == 60000.1
    assert fill.confirmation_source == "recovery"
