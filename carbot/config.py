"""Все настройки бота. Значения берутся из .env, иначе — умолчания ниже."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import date

from dotenv import load_dotenv

load_dotenv()


def _s(name: str, default: str = "") -> str:
    """Значение из .env без хвостового комментария; пусто — значение по умолчанию.
    «VAR=   # комментарий» python-dotenv отдаёт как «# комментарий» — это тоже считаем пустым."""
    v = os.getenv(name)
    if v is None:
        return default
    v = re.split(r"(?:^|\s)#", v, maxsplit=1)[0].strip().strip('"').strip("'")
    return v if v else default


def _f(name: str, default: float) -> float:
    return float(_s(name, str(default)).replace(",", "."))


def _i(name: str, default: int) -> int:
    return int(float(_s(name, str(default)).replace(" ", "")))


def _b(name: str, default: bool) -> bool:
    return _s(name, str(default)).lower() in ("1", "true", "yes", "да")


def _list(name: str) -> list[str]:
    return [x.strip().lower() for x in _s(name).split(",") if x.strip()]


@dataclass
class Settings:
    # --- доступы ---
    telegram_token: str = field(default_factory=lambda: _s("TELEGRAM_BOT_TOKEN", ""))
    allowed_user_ids: list[int] = field(
        default_factory=lambda: [int(x) for x in re.findall(r"\d+", _s("ALLOWED_USER_IDS"))]
    )
    anthropic_model: str = field(default_factory=lambda: _s("ANTHROPIC_MODEL", "claude-opus-5"))
    autoteka_client_id: str = field(default_factory=lambda: _s("AUTOTEKA_CLIENT_ID", ""))
    autoteka_client_secret: str = field(default_factory=lambda: _s("AUTOTEKA_CLIENT_SECRET", ""))

    # --- цена закупки ---
    seller_discount: float = field(default_factory=lambda: _f("SELLER_DISCOUNT", 0.15))   # п.1: минус 15 %
    cash_factor: float = field(default_factory=lambda: _f("CASH_FACTOR", 0.86))           # п.7: цена за наличку при полном НДС
    default_full_vat: bool = field(default_factory=lambda: _b("DEFAULT_FULL_VAT", True))  # если в файле нет колонки НДС

    # --- этап 1: фильтры по самому файлу (бесплатно) ---
    min_year: int = field(default_factory=lambda: _i("MIN_YEAR", 2018))          # 0 — считать по MAX_AGE_YEARS
    max_age_years: int = field(default_factory=lambda: _i("MAX_AGE_YEARS", 8))
    max_mileage_km: int = field(default_factory=lambda: _i("MAX_MILEAGE_KM", 100_000))
    min_mileage_km: int = field(default_factory=lambda: _i("MIN_MILEAGE_KM", 2))  # 0–1 км или пусто — не заводится
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
    # free — бесплатно, бот сам собирает цены с площадок MARKET_SOURCES; claude — платно, Claude с веб-поиском
    market_source: str = field(default_factory=lambda: _s("MARKET_SOURCE", "free").strip().lower())
    market_sources: list[str] = field(default_factory=lambda: _list("MARKET_SOURCES") or ["drom", "autoru"])
    drom_region: str = field(default_factory=lambda: _s("DROM_REGION", ""))           # пусто — вся Россия
    autoru_region: str = field(default_factory=lambda: _s("AUTORU_REGION", "rossiya"))
    max_distance_km: int = field(default_factory=lambda: _i("MAX_DISTANCE_KM", 2000))  # от Москвы, примерно по дорогам
    road_factor: float = field(default_factory=lambda: _f("ROAD_FACTOR", 1.2))  # по прямой × 1,2 ≈ по дорогам
    detail_checks: int = field(default_factory=lambda: _i("DETAIL_CHECKS", 6))  # сколько дешёвых объявлений открыть
    skip_us_vin: bool = field(default_factory=lambda: _b("SKIP_US_VIN", True))  # VIN США/Канады/Мексики — аукционы
    us_vin_prefixes: str = field(default_factory=lambda: _s("US_VIN_PREFIXES", "12345"))
    sources_required: int = field(default_factory=lambda: _i("SOURCES_REQUIRED", 0))  # 0 — все из MARKET_SOURCES
    browser_headless: bool = field(default_factory=lambda: _b("BROWSER_HEADLESS", False))  # окно видно — капчу решаете вы
    # chrome — ваш установленный Chrome; chromium — встроенный браузер
    browser_channel: str = field(default_factory=lambda: "" if _s("BROWSER_CHANNEL", "chrome").lower() == "chromium"
                                 else _s("BROWSER_CHANNEL", "chrome"))
    browser_delay_min: float = field(default_factory=lambda: _f("BROWSER_DELAY_MIN", 2.0))
    browser_delay_max: float = field(default_factory=lambda: _f("BROWSER_DELAY_MAX", 5.0))
    captcha_wait_sec: int = field(default_factory=lambda: _i("CAPTCHA_WAIT_SEC", 180))
    autoru_max_pages: int = field(default_factory=lambda: _i("AUTORU_MAX_PAGES", 2))
    drom_max_pages: int = field(default_factory=lambda: _i("DROM_MAX_PAGES", 3))       # по 20 объявлений
    drom_delay_min: float = field(default_factory=lambda: _f("DROM_DELAY_MIN", 2.0))   # паузы между запросами, сек
    drom_delay_max: float = field(default_factory=lambda: _f("DROM_DELAY_MAX", 5.0))
    min_liquidity: int = field(default_factory=lambda: _i("MIN_LIQUIDITY", 6))            # 1..10, по объявлениям года по России
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
