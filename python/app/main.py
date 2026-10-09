"""FastAPI-сервис: проверяет JWT от Go-сервиса и обращается к нему с этим же токеном.

Swagger UI — /docs, схема OpenAPI — /openapi.json.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse

from . import errors
from .auth import JWKSVerifier, require_claims
from .config import Settings
from .middleware import BodyLimitMiddleware, RequestContextMiddleware
from .models import OrderRequest, OrdersSummary, UserInfo, ValidationResult

log = logging.getLogger("lab10")

Claims = Annotated[dict[str, Any], Depends(require_claims)]


def create_app(settings: Settings | None = None, transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    """Фабрика приложения. transport позволяет подменить HTTP к Go-сервису в тестах."""
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Один клиент на всё приложение: пул соединений к Go-сервису переиспользуется.
        client = httpx.AsyncClient(base_url=settings.go_api_url, timeout=settings.upstream_timeout, transport=transport)
        app.state.client = client
        app.state.verifier = JWKSVerifier(
            client, settings.jwks_url, settings.jwt_issuer, settings.jwt_audience, settings.jwks_cache_ttl
        )
        app.state.ready = True
        log.info("сервис запущен, Go API: %s", settings.go_api_url)
        try:
            yield
        finally:
            # Сюда uvicorn приходит после того, как все активные запросы завершены.
            await client.aclose()
            log.info("ресурсы освобождены, сервис остановлен")

    app = FastAPI(
        title="Lab10 Python service",
        description="FastAPI-сервис: проверка JWT Go-сервиса по JWKS, валидация заказов на Pydantic.",
        version="1.0.0",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.ready = False
    errors.install(app)
    app.add_middleware(BodyLimitMiddleware)
    app.add_middleware(RequestContextMiddleware)

    @app.get("/ping", tags=["service"])
    async def ping() -> dict[str, str]:
        return {"message": "pong"}

    @app.get("/health/live", tags=["service"])
    async def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready", tags=["service"], responses={503: {"description": "Сервис останавливается"}})
    async def ready(request: Request) -> JSONResponse:
        if not request.app.state.ready:
            return JSONResponse({"status": "shutting_down"}, status_code=503)
        return JSONResponse({"status": "ready"})

    @app.post("/api/v1/orders/validate", tags=["orders"], response_model=ValidationResult)
    async def validate_order(order: OrderRequest) -> ValidationResult:
        """Только проверяет заказ (те же правила, что в Go). Используется в сравнении производительности."""
        cents = order.total_cents()
        return ValidationResult(valid=True, items=len(order.items), total_cents=cents, total=cents / 100)

    @app.get("/api/v1/me", tags=["auth"], response_model=UserInfo)
    async def me(claims: Claims) -> UserInfo:
        """Данные из токена, подпись которого проверил Python по ключу Go-сервиса."""
        return UserInfo(
            username=claims["sub"],
            role=claims.get("role", ""),
            expires_at=claims["exp"],
            verified_by="python:jwks",
        )

    @app.get("/api/v1/orders/summary", tags=["orders"], response_model=OrdersSummary)
    async def orders_summary(request: Request, claims: Claims) -> OrdersSummary:
        """Сводка по заказам пользователя: токен проверяется здесь и передаётся Go-сервису."""
        client: httpx.AsyncClient = request.app.state.client
        headers = {
            "Authorization": request.headers["authorization"],
            "X-Request-ID": request.state.request_id,
        }
        try:
            resp = await client.get("/api/v1/orders", headers=headers)
        except httpx.HTTPError as exc:
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY, {"code": "upstream_unavailable", "message": f"Go-сервис недоступен: {exc}"}
            ) from exc
        if resp.status_code != 200:
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                {"code": "upstream_error", "message": f"Go-сервис ответил {resp.status_code}"},
            )
        orders = resp.json()["orders"]
        cents = sum(o["total_cents"] for o in orders)
        return OrdersSummary(
            username=claims["sub"],
            count=len(orders),
            total_cents=cents,
            total=cents / 100,
            items=sum(len(o["items"]) for o in orders),
        )

    return app
