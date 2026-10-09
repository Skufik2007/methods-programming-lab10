"""Общие фикстуры: RSA-ключи, выпуск токенов и поддельный Go-сервис на httpx.MockTransport."""

from __future__ import annotations

import base64
import datetime as dt
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

ISSUER = "lab10-go-api"
AUDIENCE = "lab10"


def _b64(n: int) -> str:
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


@dataclass
class SigningKey:
    kid: str
    private: rsa.RSAPrivateKey

    def jwk(self) -> dict[str, str]:
        pub = self.private.public_key().public_numbers()
        return {"kty": "RSA", "use": "sig", "alg": "RS256", "kid": self.kid, "n": _b64(pub.n), "e": _b64(pub.e)}

    def token(self, sub: str = "alice", role: str = "user", **overrides: Any) -> str:
        now = int(time.time())
        claims: dict[str, Any] = {
            "iss": ISSUER,
            "aud": [AUDIENCE],
            "sub": sub,
            "role": role,
            "iat": now,
            "nbf": now,
            "exp": now + 300,
            "jti": str(uuid.uuid4()),
        }
        claims.update(overrides)
        claims = {k: v for k, v in claims.items() if v is not None}
        return jwt.encode(claims, self.private, algorithm="RS256", headers={"kid": self.kid})


def new_key(kid: str) -> SigningKey:
    return SigningKey(kid, rsa.generate_private_key(public_exponent=65537, key_size=2048))


@pytest.fixture(scope="session")
def key() -> SigningKey:
    return new_key("test-kid-1")


@dataclass
class FakeGo:
    """Поддельный Go-сервис: JWKS и список заказов. Считает обращения."""

    keys: list[SigningKey]
    orders: list[dict[str, Any]] = field(default_factory=list)
    jwks_calls: int = 0
    jwks_down: bool = False
    orders_status: int = 200
    seen_headers: list[httpx.Headers] = field(default_factory=list)

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/.well-known/jwks.json":
            self.jwks_calls += 1
            if self.jwks_down:
                raise httpx.ConnectError("connection refused", request=request)
            return httpx.Response(200, json={"keys": [k.jwk() for k in self.keys]})
        if request.url.path == "/api/v1/orders":
            self.seen_headers.append(request.headers)
            if self.orders_status != 200:
                return httpx.Response(self.orders_status, json={"error": {"code": "x", "message": "x"}})
            return httpx.Response(200, json={"orders": self.orders, "count": len(self.orders)})
        return httpx.Response(404)


@pytest.fixture
def fake_go(key: SigningKey) -> FakeGo:
    return FakeGo(keys=[key])


@pytest.fixture
def settings() -> Settings:
    return Settings(go_api_url="http://go-api.test", jwt_issuer=ISSUER, jwt_audience=AUDIENCE)


@pytest.fixture
def client(settings: Settings, fake_go: FakeGo) -> Iterator[TestClient]:
    app = create_app(settings, transport=httpx.MockTransport(fake_go.handler))
    with TestClient(app) as c:  # контекст запускает lifespan
        yield c


def valid_order(**overrides: Any) -> dict[str, Any]:
    order: dict[str, Any] = {
        "customer": {"name": "Иван Петров", "email": "ivan@example.com", "phone": "+79991234567"},
        "items": [
            {"sku": "ABC-123", "quantity": 2, "price": 199.99},
            {"sku": "DEF-4567", "quantity": 1, "price": 50},
        ],
        "delivery": {
            "address": "Москва, ул. Пушкина, 1",
            "date": (dt.datetime.now(dt.UTC).date() + dt.timedelta(days=3)).isoformat(),
        },
    }
    order.update(overrides)
    return order
