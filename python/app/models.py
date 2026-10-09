"""Pydantic-модели заказа — те же правила, что и в Go (go/internal/orders).

Совпадение правил важно: на эндпоинте /api/v1/orders/validate сравнивается
производительность валидации FastAPI/Pydantic и Gin/validator.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt, field_validator
from pydantic_core import PydanticCustomError

from .contract import GoOrder

MAX_DELIVERY_DAYS = 90

# Регулярные выражения синхронизированы с Go: SKU — validation.go, телефон — правило e164.
SKU_PATTERN = r"^[A-Z]{3}-\d{3,6}$"
E164_PATTERN = r"^\+[1-9]\d{1,14}$"
# Упрощённая проверка email без внешних зависимостей: локальная часть, @, домен с точкой.
EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


def today() -> dt.date:
    """Вынесено в функцию, чтобы тесты могли подменить «сегодня»."""
    return dt.datetime.now(dt.UTC).date()


class _Strict(BaseModel):
    # Как DisallowUnknownFields в Go: лишние поля — ошибка, а не тихий пропуск.
    model_config = ConfigDict(extra="forbid")


class Customer(_Strict):
    name: Annotated[str, Field(min_length=2, max_length=100)]
    email: Annotated[str, Field(max_length=254, pattern=EMAIL_PATTERN)]
    phone: Annotated[str, Field(pattern=E164_PATTERN)] | None = None


class Item(_Strict):
    sku: Annotated[str, Field(pattern=SKU_PATTERN)]
    # Strict: строка "2" не превращается в число молча — так же ведёт себя Go.
    quantity: Annotated[StrictInt, Field(ge=1, le=100)]
    price: Annotated[StrictFloat | StrictInt, Field(gt=0, le=1_000_000)]

    @field_validator("price")
    @classmethod
    def two_decimals(cls, v: float) -> float:
        cents = v * 100
        if abs(cents - round(cents)) > 1e-6:
            # Тип ошибки "money" совпадает с именем правила в Go.
            raise PydanticCustomError("money", "не больше двух знаков после запятой")
        return v


class Delivery(_Strict):
    address: Annotated[str, Field(min_length=5, max_length=300)]
    date: dt.date

    @field_validator("date")
    @classmethod
    def within_window(cls, v: dt.date) -> dt.date:
        start = today()
        if not start <= v <= start + dt.timedelta(days=MAX_DELIVERY_DAYS):
            raise PydanticCustomError(
                "delivery_date", "дата YYYY-MM-DD от сегодняшнего дня до +{days} дней", {"days": MAX_DELIVERY_DAYS}
            )
        return v


class OrderRequest(_Strict):
    customer: Customer
    items: Annotated[list[Item], Field(min_length=1, max_length=50)]
    delivery: Delivery
    comment: Annotated[str, Field(max_length=500)] = ""

    @field_validator("items")
    @classmethod
    def unique_skus(cls, items: list[Item]) -> list[Item]:
        seen: dict[str, int] = {}
        for i, item in enumerate(items):
            if item.sku in seen:
                raise PydanticCustomError(
                    "unique_sku",
                    "items[{i}].sku: SKU повторяется (совпадает с позицией items[{first}])",
                    {"i": i, "first": seen[item.sku]},
                )
            seen[item.sku] = i
        return items

    def total_cents(self) -> int:
        return sum(round(item.price * 100) * item.quantity for item in self.items)


class ValidationResult(BaseModel):
    valid: bool
    items: int
    total_cents: int
    total: float


class UserInfo(BaseModel):
    username: str
    role: str
    expires_at: dt.datetime
    verified_by: str = Field(description="Кто проверил подпись токена")


class SkuTotal(BaseModel):
    sku: str
    quantity: int
    total_cents: int


class OrdersSummary(BaseModel):
    username: str
    count: int
    total_cents: int
    total: float
    items: int = Field(description="Число позиций во всех заказах")
    by_sku: list[SkuTotal] = Field(description="Агрегация по вложенным позициям, по убыванию суммы")
    last_order: GoOrder | None = Field(description="Последний заказ целиком, как его вернул Go")
