"""Дром: бот сам открывает поиск и собирает объявления (обычные запросы, без браузера).

1. Находит на Дроме адрес марки и модели (списки берутся с сайта и кэшируются в drom_slugs.json).
2. Открывает «б/у, этот год выпуска, вся Россия, сначала дешёвые»:
   auto.drom.ru/<марка>/<модель>/year-<год>/used/?order=price — и собирает цены, пробеги и города.
3. Самые дешёвые похожие объявления открывает и читает описание (detail_text).
Запросы идут медленно, с паузами. Если страница не разобралась, её HTML сохраняется в debug/.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import re
from pathlib import Path
from typing import Optional

import httpx
from bs4 import BeautifulSoup

from .config import Settings
from .listings import (MODEL_NOISE, TRANSLIT, Listing, SiteUnavailable, SourceResult, cards_by_links,  # noqa: F401
                       liquidity_from_count, links_map, main_text, norm, parse_mileage, parse_total, pick_slug)
from .models import Car, MarketReport

log = logging.getLogger(__name__)
# для тестов и старых импортов
__all__ = ["DromMarket", "DromBlocked", "parse_listings", "parse_mileage", "parse_total", "pick_slug",
           "liquidity_from_count", "norm", "MODEL_NOISE"]

BASE = "https://auto.drom.ru"
SLUGS_FILE = Path("drom_slugs.json")
DEBUG_DIR = Path("debug")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/128.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
}


class DromBlocked(SiteUnavailable):
    """Дром показал капчу, запретил доступ или нет связи."""


def parse_listings(html: str) -> list[Listing]:
    """Карточки объявлений. Сначала по разметке Дрома (data-ftid), иначе — по ссылкам на объявления."""
    soup = BeautifulSoup(html, "html.parser")
    out: list[Listing] = []
    for c in soup.select('[data-ftid="bulls-list_bull"]'):
        price_el = c.select_one('[data-ftid="bull_price"]')
        price = int(re.sub(r"\D", "", price_el.get_text()) or 0) if price_el else 0
        if not price:
            continue
        text = c.get_text(" ", strip=True)
        a = c.find("a", href=True)
        title = c.select_one('[data-ftid="bull_title"]')
        year = re.search(r"\b(19|20)\d\d\b", title.get_text()) if title else None
        out.append(Listing(price, parse_mileage(text), text, a["href"] if a else "",
                           int(year.group()) if year else None))
    return out or cards_by_links(html, r"/\d{6,}\.html")


class DromMarket:
    name = "Дром"

    def __init__(self, s: Settings, http: httpx.AsyncClient | None = None):
        self.s = s
        self.http = http or httpx.AsyncClient(headers=HEADERS, timeout=30, follow_redirects=True)
        self._lock = asyncio.Lock()  # по одному запросу за раз — бережём Дром и себя от бана
        self._slugs: dict = json.loads(SLUGS_FILE.read_text("utf-8")) if SLUGS_FILE.exists() else {}
        self._pages: dict[str, SourceResult] = {}
        self._details: dict[str, str] = {}

    # ---------- сеть
    async def _get(self, url: str) -> str:
        async with self._lock:
            await asyncio.sleep(random.uniform(self.s.drom_delay_min, self.s.drom_delay_max))
            try:
                r = await self.http.get(url)
            except httpx.TransportError as exc:
                raise DromBlocked(f"Нет связи с Дромом ({type(exc).__name__}). Проверьте интернет.") from exc
        text = r.text
        if r.status_code in (403, 429) or ("captcha" in text.lower() and "bull" not in text):
            raise DromBlocked(f"Дром ограничил доступ ({r.status_code}). Подождите час и запустите снова.")
        if r.status_code == 404:
            return ""
        r.raise_for_status()
        return text

    def _save_slugs(self) -> None:
        SLUGS_FILE.write_text(json.dumps(self._slugs, ensure_ascii=False, indent=1), "utf-8")

    async def brand_slug(self, brand: str) -> str:
        brands = self._slugs.get("__brands__")
        if not brands:
            brands = links_map(await self._get(BASE + "/"), r"^(?:https?://auto\.drom\.ru)?/([a-z0-9_\-]+)/$")
            self._slugs["__brands__"] = brands
            self._save_slugs()
        slug = pick_slug(brand, brands)
        return slug or re.sub(r"[^a-z0-9]+", "_", brand.lower().translate(TRANSLIT)).strip("_")

    async def model_slug(self, brand_slug: str, model: str) -> Optional[str]:
        models = self._slugs.get(brand_slug)
        if not models:
            models = links_map(await self._get(f"{BASE}/{brand_slug}/"),
                               r"^(?:https?://auto\.drom\.ru)?/[a-z0-9_\-]+/([a-z0-9_\-]+)/$")
            self._slugs[brand_slug] = models
            self._save_slugs()
        return pick_slug(model, models, MODEL_NOISE)

    def search_url(self, brand_slug: str, model_slug: str, year: int, page: int = 1) -> str:
        region = f"/{self.s.drom_region}" if self.s.drom_region else ""
        url = f"{BASE}{region}/{brand_slug}/{model_slug}/year-{year}/used/"
        return url + (f"page{page}/" if page > 1 else "") + "?order=price"

    async def detail_text(self, url: str) -> str:
        """Текст страницы объявления: описание, характеристики, продавец."""
        if url.startswith("/"):
            url = BASE + url
        if url not in self._details:
            self._details[url] = main_text(await self._get(url))
        return self._details[url]

    # ---------- объявления
    async def fetch(self, car: Car) -> SourceResult:
        if not car.year:
            return SourceResult(self.name, "", error="нет года выпуска")
        b = await self.brand_slug(car.brand)
        m = await self.model_slug(b, car.model)
        if not m:
            return SourceResult(self.name, f"{BASE}/{b}/", error=f"не нашёл модель «{car.model}» в каталоге")
        key = f"{b}/{m}/{car.year}"
        if key in self._pages:
            return self._pages[key]
        res = SourceResult(self.name, self.search_url(b, m, car.year))
        for page in range(1, self.s.drom_max_pages + 1):
            html = await self._get(self.search_url(b, m, car.year, page))
            if not html:
                break
            if res.total is None:
                res.total = parse_total(html)
            found = parse_listings(html)
            if not found:
                if page == 1 and "₽" in html:
                    DEBUG_DIR.mkdir(exist_ok=True)
                    (DEBUG_DIR / f"drom_{b}_{m}_{car.year}.html").write_text(html, "utf-8")
                    log.warning("Не разобрал страницу Дрома %s — сохранил в debug/", key)
                break
            res.items.extend(found)
            if res.total is not None and len(res.items) >= res.total:
                break
        self._pages[key] = res
        return res

    # ---------- режим «только Дром»
    def group_key(self, car: Car) -> tuple:
        return (car.row,)  # страницы кэшируются по модели, похожие по пробегу считаются для каждой машины

    async def analyze(self, car: Car) -> MarketReport:
        from .multimarket import lowest_valid, summarize
        res = await self.fetch(car)
        if res.error:
            raise RuntimeError(f"Дром: {res.error}")
        sp = await lowest_valid(self, car, res, self.s.detail_checks, self.s.max_distance_km, self.s.road_factor)
        return summarize(car, [sp], required=1)

    async def aclose(self) -> None:
        await self.http.aclose()
