"""Авто.ру через браузер: б/у этого года по всей России, цены и пробеги с первых страниц."""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Optional

from bs4 import BeautifulSoup

from .browser import Browser
from .config import Settings
from .listings import (MODEL_NOISE, TRANSLIT, Listing, SourceResult, cards_by_links, links_map, parse_mileage,
                       parse_price, parse_total, parse_year, pick_slug)
from .models import Car

log = logging.getLogger(__name__)
BASE = "https://auto.ru"
SLUGS_FILE = Path("autoru_slugs.json")
DEBUG_DIR = Path("debug")


def is_captcha(url: str, html: str) -> bool:
    low = html.lower()
    return "showcaptcha" in url or "checkcaptcha" in url or "smartcaptcha" in low or "вы не робот" in low


def parse_listings(html: str) -> list[Listing]:
    soup = BeautifulSoup(html, "html.parser")
    out: list[Listing] = []
    for c in soup.select(".ListingItem"):
        price_el = c.select_one(".ListingItemPrice__content, .ListingItemPrice")
        price = parse_price(price_el.get_text(" ") if price_el else c.get_text(" "))
        if not price:
            continue
        km = c.select_one(".ListingItem__kmAge")
        yr = c.select_one(".ListingItem__year")
        a = c.select_one("a.ListingItemTitle__link, a[href*='/sale/']")
        text = c.get_text(" ", strip=True)
        out.append(Listing(price, parse_mileage(km.get_text(" ") if km else text), text,
                           a["href"] if a else "", parse_year(yr.get_text()) if yr else parse_year(text)))
    return out or cards_by_links(html, r"/cars/used/sale/[^/]+/[^/]+/[\w-]+")


class AutoRuMarket:
    name = "Авто.ру"

    def __init__(self, s: Settings, browser: Browser):
        self.s = s
        self.browser = browser
        self._slugs: dict = json.loads(SLUGS_FILE.read_text("utf-8")) if SLUGS_FILE.exists() else {}
        self._pages: dict[str, SourceResult] = {}

    async def _get(self, url: str) -> str:
        return await self.browser.get(self.name, url, is_captcha)

    def _save(self) -> None:
        SLUGS_FILE.write_text(json.dumps(self._slugs, ensure_ascii=False, indent=1), "utf-8")

    async def brand_slug(self, brand: str) -> str:
        brands = self._slugs.get("__brands__")
        if not brands:
            brands = links_map(await self._get(f"{BASE}/rossiya/cars/all/"),
                               r"^(?:https?://auto\.ru)?(?:/[a-z_\-]+)?/cars/([a-z0-9_\-]+)/all/?$")
            brands.pop("all", None)
            self._slugs["__brands__"] = brands
            self._save()
        return pick_slug(brand, brands) or re.sub(r"[^a-z0-9]+", "_", brand.lower().translate(TRANSLIT)).strip("_")

    async def model_slug(self, b: str, model: str) -> Optional[str]:
        models = self._slugs.get(b)
        if not models:
            models = links_map(await self._get(f"{BASE}/rossiya/cars/{b}/all/"),
                               r"^(?:https?://auto\.ru)?(?:/[a-z_\-]+)?/cars/[a-z0-9_\-]+/([a-z0-9_\-]+)/all/?$")
            models.pop("all", None)
            self._slugs[b] = models
            self._save()
        return pick_slug(model, models, MODEL_NOISE)

    def search_url(self, b: str, m: str, year: int, page: int = 1) -> str:
        return f"{BASE}/rossiya/cars/{b}/{m}/{year}-year/used/" + (f"?page={page}" if page > 1 else "")

    async def fetch(self, car: Car) -> SourceResult:
        if not car.year:
            return SourceResult(self.name, "", error="нет года выпуска")
        b = await self.brand_slug(car.brand)
        m = await self.model_slug(b, car.model)
        if not m:
            return SourceResult(self.name, f"{BASE}/rossiya/cars/{b}/all/",
                                error=f"не нашёл модель «{car.model}» в каталоге")
        key = f"{b}/{m}/{car.year}"
        if key in self._pages:
            return self._pages[key]
        res = SourceResult(self.name, self.search_url(b, m, car.year))
        for page in range(1, self.s.autoru_max_pages + 1):
            html = await self._get(self.search_url(b, m, car.year, page))
            if res.total is None:
                res.total = parse_total(html)
            found = [x for x in parse_listings(html) if x.year in (None, car.year)]
            if not found:
                if page == 1 and "₽" in html and not res.total == 0:
                    DEBUG_DIR.mkdir(exist_ok=True)
                    (DEBUG_DIR / f"autoru_{b}_{m}_{car.year}.html").write_text(html, "utf-8")
                    log.warning("Не разобрал страницу Авто.ру %s — сохранил в debug/", key)
                break
            res.items.extend(found)
            if res.total is not None and len(res.items) >= res.total:
                break
        self._pages[key] = res
        return res
