"""Бесплатная оценка рынка: бот сам открывает поиск на Дроме и считает цены похожих машин.

Как работает:
1. Находит на Дроме адрес марки и модели (списки берутся с самого сайта и кэшируются в drom_slugs.json).
2. Открывает «б/у, этот год выпуска» по всей России: auto.drom.ru/<марка>/<модель>/year-<год>/used/
   и собирает цены и пробеги с первых страниц.
3. Берёт объявления с похожим пробегом, отбрасывает битые и выбросы, считает медиану.
4. Ликвидность оценивает по числу объявлений: чем больше таких машин продаётся, тем легче продать.

Запросы идут медленно, с паузами, как у человека в браузере. Если Дром показал капчу — останавливаемся.
Если страница не разобралась, её HTML сохраняется в debug/ — пришлите его, парсер поправим.
"""
from __future__ import annotations

import asyncio
import difflib
import json
import logging
import random
import re
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx
from bs4 import BeautifulSoup

from .config import Settings
from .models import Car, MarketReport

log = logging.getLogger(__name__)

BASE = "https://auto.drom.ru"
SLUGS_FILE = Path("drom_slugs.json")
DEBUG_DIR = Path("debug")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/128.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
}
BAD_LISTING = ("битый", "не на ходу", "требует ремонта", "на запчасти", "после дтп", "без документов")
TRANSLIT = str.maketrans({
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z", "и": "i",
    "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t",
    "у": "u", "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "",
    "э": "e", "ю": "yu", "я": "ya",
})
# Слова в названии модели из реестра, которых нет в названии модели на Дроме (тип кузова и т. п.)
MODEL_NOISE = ("седан", "лифтбек", "хэтчбек", "хетчбек", "универсал", "купе", "coupe", "sedan", "pickup",
               "пикап", "-class", " class")


class DromBlocked(RuntimeError):
    """Дром показал капчу или запретил доступ."""


@dataclass
class Listing:
    price: int
    mileage: Optional[int]
    text: str
    url: str = ""


def norm(s: str) -> str:
    s = s.lower().translate(TRANSLIT)
    return re.sub(r"[^a-z0-9]", "", s)


def _num(s: str) -> Optional[int]:
    d = re.sub(r"\D", "", s or "")
    return int(d) if d else None


def parse_mileage(text: str) -> Optional[int]:
    t = text.replace("\xa0", " ").lower()
    m = re.search(r"(\d[\d ]*)\s*тыс\.?\s*км", t)
    if m:
        return _num(m.group(1)) * 1000
    m = re.search(r"(\d[\d ]*)\s*км\b", t)
    if m:
        return _num(m.group(1))
    return None


def parse_total(html: str) -> Optional[int]:
    """«… – 508 объявлений на Дроме» — сначала в <title>, потом по всей странице."""
    title = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    for text in ([title.group(1)] if title else []) + [html]:
        m = re.search(r"(\d[\d\s\xa0]*)\s+объявлени", text)
        if m:
            return _num(m.group(1))
    return None


def parse_listings(html: str) -> list[Listing]:
    """Карточки объявлений. Сначала по разметке Дрома (data-ftid), иначе — по ссылкам на объявления."""
    soup = BeautifulSoup(html, "html.parser")
    out: list[Listing] = []

    cards = soup.select('[data-ftid="bulls-list_bull"]')
    for c in cards:
        price_el = c.select_one('[data-ftid="bull_price"]')
        price = _num(price_el.get_text()) if price_el else None
        text = c.get_text(" ", strip=True)
        if not price:
            continue
        a = c.find("a", href=True)
        out.append(Listing(price, parse_mileage(text), text, a["href"] if a else ""))
    if out:
        return out

    # запасной путь: ссылки вида .../123456789.html, текст вокруг — карточка
    seen = set()
    for a in soup.find_all("a", href=re.compile(r"/\d{6,}\.html")):
        href = a["href"]
        if href in seen:
            continue
        seen.add(href)
        box = a
        for _ in range(4):  # поднимаемся до контейнера карточки, где есть цена
            if box.parent is None:
                break
            box = box.parent
            if "₽" in box.get_text():
                break
        text = box.get_text(" ", strip=True)
        m = re.search(r"(\d{1,3}(?:[\s\xa0]\d{3})+)\s*₽", text)
        if m:
            out.append(Listing(_num(m.group(1)), parse_mileage(text), text, href))
    return out


def pick_slug(name: str, options: dict[str, str], noise: tuple[str, ...] = ()) -> Optional[str]:
    """options: нормализованное имя → slug. Возвращает slug, лучше всего подходящий под name."""
    if norm(name) in options:  # «GLE-CLASS» → gle-class, если на Дроме так
        return options[norm(name)]
    low = name.lower()
    for w in noise:
        low = low.replace(w, " ")
    n = norm(low)
    if not n:
        return None
    if n in options:  # «GLE-CLASS» → gle
        return options[n]
    # «VESTA SW» → vesta, «GRANTA Лифтбек» → granta: самое длинное имя Дрома, которым начинается наше
    prefixes = [k for k in options if n.startswith(k) and len(k) >= 2]
    if prefixes:
        return options[max(prefixes, key=len)]
    # «4X4» → 4x4_2121: самое короткое имя Дрома, которое начинается с нашего
    longer = [k for k in options if k.startswith(n)]
    if longer:
        return options[min(longer, key=len)]
    close = difflib.get_close_matches(n, list(options), n=1, cutoff=0.8)
    return options[close[0]] if close else None


def liquidity_from_count(total: int, median_price: float) -> tuple[int, int]:
    """Ликвидность 1..10 и примерный срок продажи по числу объявлений того же года на всём Дроме."""
    steps = [(300, 9), (150, 8), (80, 7), (40, 6), (20, 5), (10, 4)]
    score = next((sc for n, sc in steps if total >= n), 3)
    if median_price >= 15_000_000:
        score -= 2  # очень дорогие машины покупают единицы
    elif median_price >= 8_000_000:
        score -= 1
    score = max(1, min(10, score))
    days = {10: 10, 9: 14, 8: 21, 7: 30, 6: 45, 5: 60, 4: 90, 3: 120, 2: 150, 1: 180}[score]
    return score, days


class DromMarket:
    def __init__(self, s: Settings, http: httpx.AsyncClient | None = None):
        self.s = s
        self.http = http or httpx.AsyncClient(headers=HEADERS, timeout=30, follow_redirects=True)
        self._lock = asyncio.Lock()  # по одному запросу за раз — бережём Дром и себя от бана
        self._slugs: dict = json.loads(SLUGS_FILE.read_text("utf-8")) if SLUGS_FILE.exists() else {}
        self._pages: dict[str, tuple[list[Listing], Optional[int]]] = {}

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

    async def _links(self, url: str, depth: int) -> dict[str, str]:
        """Ссылки вида auto.drom.ru/<a>/ (depth=1) или auto.drom.ru/<brand>/<b>/ (depth=2): норм. имя → slug."""
        html = await self._get(url)
        soup = BeautifulSoup(html, "html.parser")
        pat = re.compile(r"^(?:https?://auto\.drom\.ru)?/" + r"([a-z0-9_\-]+)/" * depth + r"$")
        res: dict[str, str] = {}
        for a in soup.find_all("a", href=True):
            m = pat.match(a["href"])
            if not m:
                continue
            slug = m.group(depth)
            res.setdefault(norm(slug), slug)
            txt = a.get_text(" ", strip=True)
            if txt and not re.search(r"\d{3,}", txt):  # «Tiggo 7 Pro Max», но не «1 393 объявления»
                res.setdefault(norm(txt), slug)
        return res

    async def brand_slug(self, brand: str) -> Optional[str]:
        brands = self._slugs.get("__brands__")
        if not brands:
            brands = await self._links(BASE + "/", 1)
            self._slugs["__brands__"] = brands
            self._save_slugs()
        slug = pick_slug(brand, brands)
        if not slug:  # запасной вариант: транслит
            slug = re.sub(r"[^a-z0-9]+", "_", brand.lower().translate(TRANSLIT)).strip("_")
        return slug

    async def model_slug(self, brand_slug: str, model: str) -> Optional[str]:
        models = self._slugs.get(brand_slug)
        if not models:
            models = await self._links(f"{BASE}/{brand_slug}/", 2)
            self._slugs[brand_slug] = models
            self._save_slugs()
        return pick_slug(model, models, MODEL_NOISE)

    def search_url(self, brand_slug: str, model_slug: str, year: int, page: int = 1) -> str:
        url = f"{BASE}/{brand_slug}/{model_slug}/year-{year}/used/"
        return url + (f"page{page}/" if page > 1 else "")

    async def listings(self, brand_slug: str, model_slug: str, year: int) -> tuple[list[Listing], Optional[int]]:
        key = f"{brand_slug}/{model_slug}/{year}"
        if key in self._pages:
            return self._pages[key]
        items: list[Listing] = []
        total = None
        for page in range(1, self.s.drom_max_pages + 1):
            html = await self._get(self.search_url(brand_slug, model_slug, year, page))
            if not html:
                break
            if total is None:
                total = parse_total(html)
            found = parse_listings(html)
            if not found:
                if page == 1 and "₽" in html:
                    DEBUG_DIR.mkdir(exist_ok=True)
                    (DEBUG_DIR / f"{brand_slug}_{model_slug}_{year}.html").write_text(html, "utf-8")
                    log.warning("Не разобрал страницу Дрома %s — сохранил в debug/", key)
                break
            items.extend(found)
            if total is not None and len(items) >= total:
                break
        self._pages[key] = (items, total)
        return items, total

    # ---------- оценка
    def group_key(self, car: Car) -> tuple:
        return (car.row,)  # страницы кэшируются по модели, а похожие по пробегу считаются для каждой машины

    async def analyze(self, car: Car) -> MarketReport:
        b = await self.brand_slug(car.brand)
        m = await self.model_slug(b, car.model)
        if not m:
            raise RuntimeError(f"Не нашёл модель «{car.model}» у марки {car.brand} на Дроме")
        if not car.year:
            raise RuntimeError("Нет года выпуска")
        items, total = await self.listings(b, m, car.year)
        url = self.search_url(b, m, car.year)
        clean = [x for x in items if not any(w in x.text.lower() for w in BAD_LISTING)]
        if not clean:
            return MarketReport(median_price=0, min_price=0, max_price=0, listings_found=0, liquidity=1,
                                days_to_sell=180, demand_notes=f"На Дроме нет объявлений {car.year} г.",
                                known_issues="", sources=[url])

        # похожие по пробегу: ±35 % (но не уже ±25 тыс. км); если таких мало — берём все
        comps = clean
        note = ""
        if car.mileage:
            width = max(25_000, car.mileage * 0.35)
            near = [x for x in clean if x.mileage is not None and abs(x.mileage - car.mileage) <= width]
            if len(near) >= 5:
                comps = near
            else:
                note = f" Похожих по пробегу мало ({len(near)}), взяты все объявления года."
        prices = sorted(x.price for x in comps)
        if len(prices) >= 10:  # срезаем по 10 % с краёв: «срочно», «битая», «в кредит от…»
            k = len(prices) // 10
            prices = prices[k:-k]
        med = int(statistics.median(prices))
        count = total or len(clean)
        liq, days = liquidity_from_count(count, med)
        return MarketReport(
            median_price=med, min_price=prices[0], max_price=prices[-1], listings_found=count,
            liquidity=liq, days_to_sell=days,
            demand_notes=(f"Дром: {count} объявлений {car.year} г. по России, для цены взято {len(prices)} "
                          f"похожих.{note}"),
            known_issues="", sources=[url],
        )

    async def aclose(self) -> None:
        await self.http.aclose()
