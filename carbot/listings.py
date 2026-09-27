"""Общее для всех площадок: объявление, разбор карточек, подбор похожих и медиана."""
from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field
from typing import Optional

from bs4 import BeautifulSoup

BAD_LISTING = ("битый", "не на ходу", "требует ремонта", "на запчасти", "после дтп", "без документов",
               "аварийн", "под восстановление")
TRANSLIT = str.maketrans({
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z", "и": "i",
    "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t",
    "у": "u", "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "",
    "э": "e", "ю": "yu", "я": "ya",
})
# Слова в названии модели из реестра, которых нет в названии модели на площадках (тип кузова и т. п.)
MODEL_NOISE = ("седан", "лифтбек", "хэтчбек", "хетчбек", "универсал", "купе", "coupe", "sedan", "pickup",
               "пикап", "-class", " class")
# Слова-«версии»: если их нет у нашей машины, а в объявлении есть — это другая модель (Tiggo 7 Pro ≠ Pro Max)
VERSION_WORDS = {"max", "plus", "pro", "cross", "coupe", "m", "amg", "rs", "travel", "kingkong"}
BODY_WORDS = ("седан", "лифтбек", "хэтчбек", "хетчбек", "универсал", "-class", " class")


class SiteUnavailable(RuntimeError):
    """Площадка недоступна: капча, бан, нет интернета."""


@dataclass
class Listing:
    price: int
    mileage: Optional[int]
    text: str
    url: str = ""
    year: Optional[int] = None


@dataclass
class SourceResult:
    name: str                       # Дром | Авто.ру | Авито
    url: str                        # поиск, по которому смотрели
    items: list[Listing] = field(default_factory=list)
    total: Optional[int] = None     # сколько объявлений нашла площадка
    error: str = ""                 # не удалось проверить — почему


@dataclass
class SourcePrice:
    name: str
    url: str
    median: Optional[int] = None
    comps: int = 0
    total: Optional[int] = None
    status: str = "ok"              # ok | мало объявлений | нет объявлений | не проверено: …


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower().translate(TRANSLIT))


def tokens(s: str) -> list[str]:
    s = s.lower().replace("класс", "class").replace("klasse", "class").translate(TRANSLIT)
    return re.findall(r"[a-z0-9]+", s)


def num(s: str) -> Optional[int]:
    d = re.sub(r"\D", "", s or "")
    return int(d) if d else None


def parse_mileage(text: str) -> Optional[int]:
    t = text.replace("\xa0", " ").replace(" ", " ").lower()
    m = re.search(r"(\d[\d ]*)\s*тыс\.?\s*км", t)
    if m:
        return num(m.group(1)) * 1000
    m = re.search(r"(\d{1,3}(?: \d{3})+|\d+)\s*км\b", t)
    if m:
        return num(m.group(1))
    return None


def parse_price(text: str, min_price: int = 100_000) -> Optional[int]:
    """Первая сумма с ₽ не меньше min_price (пропускаем «от 25 000 ₽/мес»)."""
    t = text.replace("\xa0", " ").replace(" ", " ")
    for m in re.finditer(r"(\d{1,3}(?: \d{3})+|\d{6,})\s*₽", t):
        v = num(m.group(1))
        if v and v >= min_price:
            return v
    return None


def parse_year(text: str) -> Optional[int]:
    m = re.search(r"(?:^|[\s,])((?:19[89]|20[0-4])\d)(?:[\s,]|$)", text)
    return int(m.group(1)) if m else None


def parse_total(html: str) -> Optional[int]:
    """«– 508 объявлений», «Найдено 539 предложений» — сначала в <title>, потом по странице."""
    title = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    for text in ([title.group(1)] if title else []) + [html]:
        m = re.search(r"(\d[\d\s\xa0 ]*)\s+(?:объявлени|предложени)", text)
        if m:
            return num(m.group(1))
    return None


def cards_by_links(html: str, href_re: str, max_up: int = 6) -> list[Listing]:
    """Универсальный разбор: ссылки на объявления → поднимаемся до контейнера с ценой → цена, пробег, год."""
    soup = BeautifulSoup(html, "html.parser")
    out: list[Listing] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=re.compile(href_re)):
        href = a["href"].split("?")[0]
        if href in seen:
            continue
        box = a
        for _ in range(max_up):
            if parse_price(box.get_text(" ")) or box.parent is None:
                break
            box = box.parent
        text = box.get_text(" ", strip=True)
        price = parse_price(text)
        if not price:
            continue
        seen.add(href)
        title = a.get("title") or a.get_text(" ", strip=True)
        out.append(Listing(price, parse_mileage(text), f"{title} | {text}", href, parse_year(title) or parse_year(text)))
    return out


def model_matches(title: str, model: str) -> bool:
    """Все слова нашей модели есть в заголовке, и нет «лишних» версий (Pro Max при поиске Pro)."""
    low = model.lower()
    for w in BODY_WORDS:
        low = low.replace(w, " ")
    ours = set(tokens(low)) - {"class"}
    theirs = set(tokens(title))
    if not ours or not ours <= theirs:
        return False
    return not ((theirs & VERSION_WORDS) - ours)


def price_for(items: list[Listing], mileage: Optional[int], min_comps: int = 3) -> tuple[Optional[int], int, str]:
    """Медиана по похожим: без битых, пробег ±35 % (не уже ±25 тыс.), срез 10 % крайних цен."""
    clean = [x for x in items if not any(w in x.text.lower() for w in BAD_LISTING)]
    if not clean:
        return None, 0, "нет объявлений"
    comps = clean
    if mileage:
        width = max(25_000, mileage * 0.35)
        near = [x for x in clean if x.mileage is not None and abs(x.mileage - mileage) <= width]
        if len(near) >= min_comps:
            comps = near
    prices = sorted(x.price for x in comps)
    if len(prices) >= 10:
        k = len(prices) // 10
        prices = prices[k:-k]
    if len(prices) < min_comps:
        return int(statistics.median(prices)), len(prices), "мало объявлений"
    return int(statistics.median(prices)), len(prices), "ok"


def liquidity_from_count(total: int, median_price: float) -> tuple[int, int]:
    """Ликвидность 1..10 и примерный срок продажи по числу объявлений того же года."""
    steps = [(300, 9), (150, 8), (80, 7), (40, 6), (20, 5), (10, 4)]
    score = next((sc for n, sc in steps if total >= n), 3)
    if median_price >= 15_000_000:
        score -= 2  # очень дорогие машины покупают единицы
    elif median_price >= 8_000_000:
        score -= 1
    score = max(1, min(10, score))
    days = {10: 10, 9: 14, 8: 21, 7: 30, 6: 45, 5: 60, 4: 90, 3: 120, 2: 150, 1: 180}[score]
    return score, days


def pick_slug(name: str, options: dict[str, str], noise: tuple[str, ...] = ()) -> Optional[str]:
    """options: нормализованное имя → slug. Возвращает slug, лучше всего подходящий под name."""
    import difflib

    if norm(name) in options:  # «GLE-CLASS» → gle-class, если на площадке так
        return options[norm(name)]
    low = name.lower()
    for w in noise:
        low = low.replace(w, " ")
    n = norm(low)
    if not n:
        return None
    if n in options:  # «GLE-CLASS» → gle
        return options[n]
    # «VESTA SW» → vesta, «GRANTA Лифтбек» → granta: самое длинное имя площадки, которым начинается наше
    prefixes = [k for k in options if n.startswith(k) and len(k) >= 2]
    if prefixes:
        return options[max(prefixes, key=len)]
    # «4X4» → 4x4_2121: самое короткое имя площадки, которое начинается с нашего
    longer = [k for k in options if k.startswith(n)]
    if longer:
        return options[min(longer, key=len)]
    close = difflib.get_close_matches(n, list(options), n=1, cutoff=0.8)
    return options[close[0]] if close else None


def links_map(html: str, pattern: str, group: int = 1) -> dict[str, str]:
    """Ссылки каталога (марки или модели): нормализованное имя/текст ссылки → slug."""
    soup = BeautifulSoup(html, "html.parser")
    pat = re.compile(pattern)
    res: dict[str, str] = {}
    for a in soup.find_all("a", href=True):
        m = pat.match(a["href"])
        if not m:
            continue
        slug = m.group(group)
        res.setdefault(norm(slug), slug)
        txt = a.get_text(" ", strip=True)
        if txt and not re.search(r"\d{3,}", txt):  # «Tiggo 7 Pro Max», но не «1 393 объявления»
            res.setdefault(norm(txt), slug)
    return res
