"""Авито через браузер: поиск «марка модель год» по всей России, из выдачи берём только ту же модель и год.

Заголовки на Авито вида «Chery Tiggo 7 Pro Max 1.6 AMT, 2023, 45 000 км» — из них берём год и пробег.
"""
from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import urlencode

from bs4 import BeautifulSoup

from .browser import Browser
from .config import Settings
from .listings import (Listing, SourceResult, cards_by_links, model_matches, parse_mileage, parse_price, parse_year,
                       tokens)
from .models import Car

log = logging.getLogger(__name__)
BASE = "https://www.avito.ru"
DEBUG_DIR = Path("debug")


def is_captcha(url: str, html: str) -> bool:
    low = html.lower()
    return ("firewall" in url or "доступ ограничен" in low or "подтвердите, что вы не робот" in low
            or ("captcha" in low and 'data-marker="item"' not in html))


def parse_listings(html: str) -> list[Listing]:
    soup = BeautifulSoup(html, "html.parser")
    out: list[Listing] = []
    for c in soup.select('[data-marker="item"]'):
        a = c.select_one('[data-marker="item-title"], a[itemprop="url"]')
        title = (a.get("title") or a.get_text(" ", strip=True)) if a else ""
        meta = c.select_one('meta[itemprop="price"]')
        price = int(meta["content"]) if meta and str(meta.get("content", "")).isdigit() else None
        text = c.get_text(" ", strip=True)
        price = price or parse_price(text)
        if not price:
            continue
        params = c.select_one('[data-marker="item-specific-params"]')
        mileage = parse_mileage(title) or parse_mileage(params.get_text(" ") if params else text)
        out.append(Listing(price, mileage, f"{title} | {text}", a["href"] if a and a.has_attr("href") else "",
                           parse_year(title)))
    return out or cards_by_links(html, r"/avtomobili/[^/?]+_\d{6,}")


class AvitoMarket:
    name = "Авито"

    def __init__(self, s: Settings, browser: Browser):
        self.s = s
        self.browser = browser
        self._pages: dict[str, SourceResult] = {}

    def search_url(self, car: Car, page: int = 1) -> str:
        q = {"q": f"{car.brand} {car.model} {car.year}"}
        if page > 1:
            q["p"] = page
        return f"{BASE}/rossiya/avtomobili?{urlencode(q)}"

    async def fetch(self, car: Car) -> SourceResult:
        if not car.year:
            return SourceResult(self.name, "", error="нет года выпуска")
        key = f"{car.brand}|{car.model}|{car.year}".lower()
        if key in self._pages:
            return self._pages[key]
        res = SourceResult(self.name, self.search_url(car))
        brand = set(t for t in tokens(car.brand) if t not in ("benz",))
        seen = 0
        for page in range(1, self.s.avito_max_pages + 1):
            html = await self.browser.get(self.name, self.search_url(car, page), is_captcha)
            found = parse_listings(html)
            seen += len(found)
            if not found:
                if page == 1 and "₽" in html:
                    DEBUG_DIR.mkdir(exist_ok=True)
                    (DEBUG_DIR / f"avito_{key.replace('|', '_').replace(' ', '_')}.html").write_text(html, "utf-8")
                    log.warning("Не разобрал страницу Авито %s — сохранил в debug/", key)
                break
            for x in found:
                title = x.text.split(" | ")[0]
                if x.year == car.year and brand <= set(tokens(title)) and model_matches(title, car.model):
                    res.items.append(x)
            if len(found) < 30:  # последняя страница выдачи
                break
        res.total = len(res.items)  # у текстового поиска общий счётчик шумный — считаем только подходящие
        self._pages[key] = res
        return res
