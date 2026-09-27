from __future__ import annotations

from .config import Settings
from .models import Car, MarketReport


def purchase_price(car: Car, s: Settings) -> float:
    """П.1: компания-продавец сразу даёт 15 %."""
    return car.price * (1 - s.seller_discount)


def cash_price(car: Car, s: Settings) -> float:
    """П.7: для машин с полным НДС после скидки умножаем на 0,86 — цена за наличку."""
    p = purchase_price(car, s)
    return p * s.cash_factor if car.full_vat else p


def expected_sale(market: MarketReport, s: Settings) -> float:
    """За сколько реально продадим: медиана рынка минус торг."""
    return market.median_price * (1 - s.sale_discount)


def profit(car: Car, market: MarketReport, s: Settings) -> float:
    return expected_sale(market, s) - cash_price(car, s) - s.prep_cost_rub
