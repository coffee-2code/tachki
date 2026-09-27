"""Все настройки бота. Значения берутся из .env, иначе — умолчания ниже."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date

from dotenv import load_dotenv

load_dotenv()


def _f(name: str, default: float) -> float:
    return float(os.getenv(name, default))


def _i(name: str, default: int) -> int:
    return int(os.getenv(name, default))


def _b(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "да")


def _list(name: str) -> list[str]:
    return [x.strip().lower() for x in os.getenv(name, "").split(",") if x.strip()]


@dataclass
class Settings:
    # --- доступы ---
    telegram_token: str = field(default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", ""))
    allowed_user_ids: list[int] = field(
        default_factory=lambda: [int(x) for x in os.getenv("ALLOWED_USER_IDS", "").split(",") if x.strip()]
    )
    anthropic_model: str = field(default_factory=lambda: os.getenv("ANTHROPIC_MODEL", "claude-opus-5"))
    autoteka_client_id: str = field(default_factory=lambda: os.getenv("AUTOTEKA_CLIENT_ID", ""))
    autoteka_client_secret: str = field(default_factory=lambda: os.getenv("AUTOTEKA_CLIENT_SECRET", ""))

    # --- цена закупки ---
    seller_discount: float = field(default_factory=lambda: _f("SELLER_DISCOUNT", 0.15))   # п.1: минус 15 %
    cash_factor: float = field(default_factory=lambda: _f("CASH_FACTOR", 0.86))           # п.7: цена за наличку при полном НДС
    default_full_vat: bool = field(default_factory=lambda: _b("DEFAULT_FULL_VAT", True))  # если в файле нет колонки НДС

    # --- этап 1: фильтры по самому файлу (бесплатно) ---
    max_age_years: int = field(default_factory=lambda: _i("MAX_AGE_YEARS", 8))
    max_mileage_km: int = field(default_factory=lambda: _i("MAX_MILEAGE_KM", 150_000))
    max_km_per_year: int = field(default_factory=lambda: _i("MAX_KM_PER_YEAR", 30_000))
    blacklist: list[str] = field(default_factory=lambda: _list("BLACKLIST"))  # "марка" или "марка модель"

    # --- этап 2: рынок (Claude + веб-поиск) ---
    min_liquidity: int = field(default_factory=lambda: _i("MIN_LIQUIDITY", 6))            # 1..10
    sale_discount: float = field(default_factory=lambda: _f("SALE_DISCOUNT", 0.05))       # торг при продаже
    prep_cost_rub: int = field(default_factory=lambda: _i("PREP_COST_RUB", 40_000))       # подготовка, оформление
    min_profit_rub: int = field(default_factory=lambda: _i("MIN_PROFIT_RUB", 150_000))
    market_concurrency: int = field(default_factory=lambda: _i("MARKET_CONCURRENCY", 3))

    # --- этап 3: Автотека ---
    max_owners: int = field(default_factory=lambda: _i("MAX_OWNERS", 2))                  # 3+ владельцев — нет
    max_accidents: int = field(default_factory=lambda: _i("MAX_ACCIDENTS", 1))            # 2+ ДТП — нет
    mileage_tolerance_km: int = field(default_factory=lambda: _i("MILEAGE_TOLERANCE_KM", 3_000))

    @property
    def current_year(self) -> int:
        return date.today().year


settings = Settings()
