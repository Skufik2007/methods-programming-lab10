"""Сквозной тест с настоящим Go-сервисом: токен выпускает Go, проверяет Python.

Собирает go/cmd/server, запускает бинарь на свободном порту и гоняет сценарий
login (Go) -> /me (Python) -> заказ (Go) -> /summary (Python -> Go).
Пропускается, если нет компилятора Go.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from conftest import valid_order
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

pytestmark = pytest.mark.integration

GO_DIR = Path(__file__).resolve().parents[2] / "go"
USER, PASSWORD = "student", "student-pass-1"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def go_api(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    go = shutil.which("go")
    if go is None:
        pytest.skip("компилятор Go не найден")
    binary = tmp_path_factory.mktemp("bin") / ("server.exe" if sys.platform == "win32" else "server")
    subprocess.run([go, "build", "-o", str(binary), "./cmd/server"], cwd=GO_DIR, check=True)

    port = _free_port()
    env = {**os.environ, "ADDR": f"127.0.0.1:{port}", "DEMO_USERS": f"{USER}:{PASSWORD}:user", "LOG_FORMAT": "text"}
    proc = subprocess.Popen([str(binary)], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 10
        while True:
            try:
                if httpx.get(f"{url}/health/ready", timeout=0.5).status_code == 200:
                    break
            except httpx.TransportError:
                pass
            if time.monotonic() > deadline:
                pytest.fail("Go-сервис не поднялся за 10 с")
            time.sleep(0.1)
        yield url
    finally:
        proc.terminate()
        proc.wait(timeout=10)


@pytest.fixture
def py_client(go_api: str) -> Iterator[TestClient]:
    with TestClient(create_app(Settings(go_api_url=go_api))) as c:
        yield c


def test_go_token_verified_by_python(go_api: str, py_client: TestClient) -> None:
    login = httpx.post(f"{go_api}/auth/login", json={"username": USER, "password": PASSWORD})
    assert login.status_code == 200, login.text
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    me = py_client.get("/api/v1/me", headers=headers)
    assert me.status_code == 200, me.text
    assert me.json()["username"] == USER
    assert me.json()["verified_by"] == "python:jwks"

    # Испорченная подпись: Python отклоняет сам, не спрашивая Go.
    tampered = token[:-4] + ("AAAA" if not token.endswith("AAAA") else "BBBB")
    assert py_client.get("/api/v1/me", headers={"Authorization": f"Bearer {tampered}"}).status_code == 401

    created = httpx.post(f"{go_api}/api/v1/orders", json=valid_order(), headers=headers)
    assert created.status_code == 201, created.text

    summary = py_client.get("/api/v1/orders/summary", headers=headers)
    assert summary.status_code == 200, summary.text
    assert summary.json()["count"] == 1
    assert summary.json()["total_cents"] == 44998


def test_complex_json_roundtrip(go_api: str, py_client: TestClient) -> None:
    """Вложенный заказ проходит Python → Go → Python без искажений (средней сложности №5)."""
    token = httpx.post(f"{go_api}/auth/login", json={"username": USER, "password": PASSWORD}).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    order = valid_order(
        comment='Кириллица, «кавычки», эмодзи 📦 и "экранирование"',
        items=[
            {"sku": "ABC-123", "quantity": 3, "price": 0.1},
            {"sku": "XYZ-999999", "quantity": 100, "price": 999999.99},
        ],
    )

    created = py_client.post("/api/v1/orders", json=order, headers=headers)
    assert created.status_code == 201, created.text
    via_python = created.json()

    # Тот же заказ, запрошенный напрямую у Go, совпадает с тем, что вернул Python.
    via_go = httpx.get(f"{go_api}/api/v1/orders/{via_python['id']}", headers=headers).json()
    for key in ("customer", "items", "delivery", "comment", "total_cents", "owner"):
        assert via_python[key] == via_go[key], key
    assert via_go["items"] == order["items"]
    assert via_go["comment"] == order["comment"]
    assert via_go["total_cents"] == 3 * 10 + 100 * 99999999  # копейки без ошибок float

    summary = py_client.get("/api/v1/orders/summary", headers=headers).json()
    xyz = next(s for s in summary["by_sku"] if s["sku"] == "XYZ-999999")
    assert xyz == {"sku": "XYZ-999999", "quantity": 100, "total_cents": 9999999900}
    assert summary["last_order"]["id"] == via_python["id"]


def test_same_validation_rules(go_api: str, py_client: TestClient) -> None:
    """Один и тот же некорректный заказ даёт одинаковые поля и правила в Go и Python."""
    bad = valid_order(
        customer={"name": "И", "email": "nope"},
        items=[{"sku": "bad", "quantity": 0, "price": 1.999}],
    )
    go = httpx.post(f"{go_api}/api/v1/orders/validate", json=bad)
    py = py_client.post("/api/v1/orders/validate", json=bad)
    assert go.status_code == py.status_code == 422

    def rules(resp: httpx.Response) -> set[tuple[str, str]]:
        return {(d["field"], d["rule"]) for d in resp.json()["error"]["details"]}

    assert rules(go) == rules(py)
