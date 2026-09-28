import asyncio
import http.server
import threading

import pytest

from carbot import autoru
from carbot.autoteka import detect_rollback, extract_heuristic
from carbot.config import Settings
from carbot.listings import Listing, SiteUnavailable, SourceResult, listing_flags, model_matches, parse_price
from carbot.models import Car
from carbot.multimarket import IncompleteMarket, MultiMarket, lowest_valid, summarize

CAR = Car(row=2, brand="Chery", model="Tiggo 7 Pro Max", year=2023, mileage=40_000, price=2_085_000)


def L(price, km, city="Москва", year=2023, url=None):
    return Listing(price, km, f"Chery Tiggo 7 Pro Max, {year} {km} км {city}", url or f"/{price}.html", year)


# ---------------------------------------------------------------- разбор страниц
def test_autoru_cards():
    html = "".join(
        f'<div class="ListingItem"><a class="ListingItemTitle__link" href="https://auto.ru/cars/used/sale/chery/'
        f'tiggo_7_pro_max/11{i}-ab{i}/">Chery Tiggo 7 Pro Max I</a><div class="ListingItem__year">2023</div>'
        f'<div class="ListingItem__kmAge">{30 + i}\xa0000\xa0км</div>'
        f'<div class="ListingItemPrice__content">2\xa0{100 + i}\xa0000\xa0₽</div>'
        f'<div>от 45\xa0000 ₽/мес.</div><span>Москва</span></div>' for i in range(3))
    items = autoru.parse_listings(html)
    assert [(x.price, x.mileage, x.year) for x in items] == [
        (2_100_000, 30_000, 2023), (2_101_000, 31_000, 2023), (2_102_000, 32_000, 2023)]


def test_model_matches():
    assert model_matches("Chery Tiggo 7 Pro Max 1.6 AMT, 2023", "Tiggo 7 Pro Max")
    assert not model_matches("Chery Tiggo 7 Pro Max 1.6 AMT, 2023", "Tiggo 7 Pro")
    assert model_matches("Mercedes-Benz GLE-класс 450 4MATIC, 2023", "GLE-CLASS")
    assert model_matches("Kia K5 2.5 AT GT Line, 2023", "K5")


def test_parse_price_skips_credit():
    assert parse_price("от 25 000 ₽/мес · 2 150 000 ₽") == 2_150_000


def test_listing_flags():
    assert listing_flags("Один владелец, учёт РФ, в наличии в Москве") == []
    assert listing_flags("Авто из Беларуси, учет РБ") == ["учёт в Беларуси"]
    assert listing_flags("Под заказ, в пути из Китая") == ["под заказ", "в пути"]
    assert listing_flags("Не растаможен") == ["не растаможен"]
    assert listing_flags("Номера РБ, ПТС оригинал") == ["учёт в Беларуси"]
    assert listing_flags("Продаю, битый") == ["битая/ремонт"]


# ---------------------------------------------------------------- самые низкие цены и вывод
class FakeSite:
    def __init__(self, name, items=(), total=None, details=None, exc=None):
        self.name, self.items, self.total = name, list(items), total
        self.details, self.exc, self.calls, self.opened = details or {}, exc, 0, []

    async def fetch(self, car):
        self.calls += 1
        if self.exc:
            raise self.exc
        return SourceResult(self.name, f"https://{self.name}/search", list(self.items), self.total)

    async def detail_text(self, url):
        self.opened.append(url)
        return self.details.get(url, "Описание: один владелец, учёт РФ")


def test_lowest_valid_picks_cheapest_suitable():
    drom = FakeSite("Дром", [
        L(1_700_000, 41_000, "Новосибирск"),                 # далеко
        L(1_750_000, 38_000, url="/zakaz.html"),             # в описании «под заказ»
        L(1_800_000, 150_000),                               # пробег не похож
        L(1_950_000, 45_000, "Тула"),
        L(2_050_000, 39_000),
    ], total=320, details={"/zakaz.html": "Машина в пути, под заказ"})
    sp = asyncio.run(lowest_valid(drom, CAR, asyncio.run(drom.fetch(CAR)), max_checks=6))
    assert (sp.low, sp.second) == (1_950_000, 2_050_000)
    assert sp.low_place.startswith("Тула")
    assert "/1700000.html" not in drom.opened  # далёкую не открывали
    assert any("далеко: Новосибирск" in x for x in sp.skipped) and any("под заказ" in x for x in sp.skipped)


def test_summary_takes_lowest_and_needs_all_sites():
    drom = FakeSite("Дром", [L(1_950_000, 40_000), L(2_000_000, 42_000)], total=320)
    ar = FakeSite("Авто.ру", [L(1_900_000, 39_000, "Казань"), L(2_100_000, 41_000)], total=150)

    async def run(*sites):
        return [await lowest_valid(x, CAR, await x.fetch(CAR), 6) for x in sites]
    rep = summarize(CAR, asyncio.run(run(drom, ar)), required=2)
    assert rep.median_price == 1_900_000  # рынок — самая низкая подходящая
    assert {b["name"]: b["low"] for b in rep.by_source} == {"Дром": 1_950_000, "Авто.ру": 1_900_000}
    assert rep.listings_found == 320 and rep.liquidity == 9

    broken = FakeSite("Авто.ру", exc=None)
    by = asyncio.run(run(drom))
    by.append(asyncio.run(lowest_valid(broken, CAR, SourceResult("Авто.ру", "", error="капчу не решили"), 0)))
    with pytest.raises(IncompleteMarket, match="Авто.ру — капчу не решили"):
        summarize(CAR, by, required=2)


def test_multimarket_marks_site_down_once():
    s = Settings()
    drom = FakeSite("Дром", [L(2_000_000 + i * 10_000, 40_000) for i in range(3)], total=100)
    ar = FakeSite("Авто.ру", exc=SiteUnavailable("капчу не решили вовремя."))
    mm = MultiMarket(s, sources=[drom, ar])
    said = []

    async def notify(t):
        said.append(t)
    mm.notify = notify

    async def one():
        with pytest.raises(IncompleteMarket):
            await mm.analyze(CAR)

    async def run():
        await asyncio.gather(one(), one(), one())  # три машины одновременно, как в боевом режиме
        await one()
    asyncio.run(run())
    assert ar.calls <= 3  # после отказа площадку больше не дёргаем
    assert len(said) == 1 and "Авто.ру недоступен" in said[0] and ".." not in said[0]


def test_distance_radius():
    from carbot.multimarket import place_flags
    assert place_flags("Екатеринбург", 2000, 1.2)[0] == []
    assert place_flags("Тюмень", 2000, 1.2)[0][0].startswith("далеко: Тюмень")
    assert place_flags("Минск", 2000, 1.2)[0] == ["не РФ: Минск"]
    assert place_flags("Екатеринбург", 1500, 1.2)[0][0].startswith("далеко")


def test_autoteka_heuristic():
    raw = {"result": {"report": {"status": "success", "data": {
        "ownersCount": 3, "accidents": [{"date": "2023-01-02"}, {"date": "2024-03-01"}],
        "mileages": [{"date": "2022-05-01", "mileage": 30000}, {"date": "2023-06-01", "mileage": 25000}],
        "pledges": [], "taxi": {"isTaxi": True}, "leasing": {"isLeasing": False}}}}}
    ex = extract_heuristic(raw)
    assert (ex.owners, ex.accidents, ex.flags) == (3, 2, ["такси"])
    assert detect_rollback([(m.date, m.km) for m in ex.mileage_records], 3000)
    assert extract_heuristic({"unknown": 1}) is None


# ---------------------------------------------------------------- настоящий браузер на локальных страницах
@pytest.fixture
def local_site():
    pages = {
        "/list": "<html><body>" + "".join(
            f'<div class="ListingItem"><a class="ListingItemTitle__link" href="/cars/used/sale/chery/t/{i}-a/">'
            f'Chery</a><div class="ListingItem__year">2023</div><div class="ListingItem__kmAge">40 000 км</div>'
            f'<div class="ListingItemPrice__content">2 000 000 ₽</div></div>' for i in range(3)) + "</body></html>",
        "/captcha": "<html><body>Вы не робот? SmartCaptcha</body></html>",
    }

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = pages.get(self.path, "").encode()
            self.send_response(200 if body else 404)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_browser_real(local_site, tmp_path, monkeypatch):
    pytest.importorskip("playwright")
    monkeypatch.chdir(tmp_path)
    from carbot.browser import Browser
    s = Settings()
    s.browser_headless, s.browser_channel = True, ""
    s.browser_delay_min = s.browser_delay_max = 0

    async def run():
        b = Browser(s)
        try:
            html = await b.get("Авто.ру", local_site + "/list", autoru.is_captcha)
            assert len(autoru.parse_listings(html)) == 3
            with pytest.raises(SiteUnavailable, match="капчу"):
                await b.get("Авто.ру", local_site + "/captcha", autoru.is_captcha)
        finally:
            await b.close()
    try:
        asyncio.run(run())
    except SiteUnavailable as exc:
        if "не запустился браузер" in str(exc):
            pytest.skip("нет браузера Chromium")
        raise
