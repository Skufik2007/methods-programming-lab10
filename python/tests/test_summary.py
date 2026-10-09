"""Python -> Go: сводка заказов с пробросом токена и X-Request-ID."""

from __future__ import annotations

from conftest import FakeGo, SigningKey
from fastapi.testclient import TestClient


def test_summary_aggregates_go_orders(client: TestClient, key: SigningKey, fake_go: FakeGo) -> None:
    fake_go.orders = [
        {"id": "1", "total_cents": 44998, "items": [{}, {}]},
        {"id": "2", "total_cents": 1000, "items": [{}]},
    ]
    token = key.token(sub="alice")
    r = client.get("/api/v1/orders/summary", headers={"Authorization": f"Bearer {token}", "X-Request-ID": "req-42"})
    assert r.status_code == 200, r.text
    assert r.json() == {"username": "alice", "count": 2, "total_cents": 45998, "total": 459.98, "items": 3}

    sent = fake_go.seen_headers[-1]
    assert sent["authorization"] == f"Bearer {token}"
    assert sent["x-request-id"] == "req-42"


def test_summary_upstream_errors(client: TestClient, key: SigningKey, fake_go: FakeGo) -> None:
    fake_go.orders_status = 500
    r = client.get("/api/v1/orders/summary", headers={"Authorization": f"Bearer {key.token()}"})
    assert r.status_code == 502
    assert r.json()["error"]["code"] == "upstream_error"


def test_summary_requires_token(client: TestClient, fake_go: FakeGo) -> None:
    assert client.get("/api/v1/orders/summary").status_code == 401
    assert fake_go.seen_headers == []  # без токена до Go даже не доходим
