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
    min_year: int = field(default_factory=lambda: _i("MIN_YEAR", 2018))          # 0 — считать по MAX_AGE_YEARS
    max_age_years: int = field(default_factory=lambda: _i("MAX_AGE_YEARS", 8))
    max_mileage_km: int = field(default_factory=lambda: _i("MAX_MILEAGE_KM", 100_000))
    max_km_per_year: int = field(default_factory=lambda: _i("MAX_KM_PER_YEAR", 30_000))
    blacklist: list[str] = field(default_factory=lambda: _list("BLACKLIST"))  # "марка" или "марка модель"
    # Колонки реестра лизинговой компании (если их нет в файле — проверка пропускается)
    vehicle_types: list[str] = field(default_factory=lambda: _list("VEHICLE_TYPES") or ["легковой"])
    allow_statuses: list[str] = field(default_factory=lambda: _list("ALLOW_STATUSES") or ["в продаже"])
    skip_no_keys: bool = field(default_factory=lambda: _b("SKIP_NO_KEYS", True))  # без ключей не рассматриваем
    skip_rhd: bool = field(default_factory=lambda: _b("SKIP_RHD", True))  # правый руль не рассматриваем
    bad_conditions: list[str] = field(default_factory=lambda: _list("BAD_CONDITIONS") or ["hard", "удовлетвор"])
    bad_words: list[str] = field(default_factory=lambda: _list("BAD_WORDS") or [
        "тотал", "сгор", "погорел", "утоп", "перевертыш", "перевёртыш", "не на ходу", "хлам",
        "не продавать", "проблемное", "после дтп", "криминал", "арест", "розыск", "залог",
    ])

    # --- этап 2: рынок ---
    # drom — бесплатно, бот сам собирает цены с Дрома; claude — платно, Claude с веб-поиском и форумами
    market_source: str = field(default_factory=lambda: os.getenv("MARKET_SOURCE", "drom").strip().lower())
    drom_max_pages: int = field(default_factory=lambda: _i("DROM_MAX_PAGES", 3))       # по 20 объявлений
    drom_delay_min: float = field(default_factory=lambda: _f("DROM_DELAY_MIN", 2.0))   # паузы между запросами, сек
    drom_delay_max: float = field(default_factory=lambda: _f("DROM_DELAY_MAX", 5.0))
    min_liquidity: int = field(default_factory=lambda: _i("MIN_LIQUIDITY", 6))            # 1..10
    sale_discount: float = field(default_factory=lambda: _f("SALE_DISCOUNT", 0.05))       # торг при продаже
    prep_cost_rub: int = field(default_factory=lambda: _i("PREP_COST_RUB", 40_000))       # подготовка, оформление
    min_profit_rub: int = field(default_factory=lambda: _i("MIN_PROFIT_RUB", 400_000))
    market_concurrency: int = field(default_factory=lambda: _i("MARKET_CONCURRENCY", 3))
    usd_per_market_check: float = field(default_factory=lambda: _f("USD_PER_MARKET_CHECK", 0.4))  # для прикидки

    # --- этап 3: Автотека ---
    max_owners: int = field(default_factory=lambda: _i("MAX_OWNERS", 2))                  # 3+ владельцев — нет
    max_accidents: int = field(default_factory=lambda: _i("MAX_ACCIDENTS", 1))            # 2+ ДТП — нет
    mileage_tolerance_km: int = field(default_factory=lambda: _i("MILEAGE_TOLERANCE_KM", 3_000))

    @property
    def current_year(self) -> int:
        return date.today().year


settings = Settings()
