import asyncio
import http.server
import threading

import pytest

from carbot import autoru, avito
from carbot.config import Settings
from carbot.listings import Listing, SiteUnavailable, SourceResult, model_matches, parse_price, price_for
from carbot.models import Car
from carbot.multimarket import IncompleteMarket, MultiMarket, summarize

CAR = Car(row=2, brand="Chery", model="Tiggo 7 Pro Max", year=2023, mileage=40_000, price=2_085_000)


def L(price, km, year=2023, text="Chery Tiggo 7 Pro Max"):
    return Listing(price, km, text, "", year)


# ---------------------------------------------------------------- разбор страниц
def test_autoru_cards():
    html = "".join(
        f'<div class="ListingItem"><a class="ListingItemTitle__link" href="https://auto.ru/cars/used/sale/chery/'
        f'tiggo_7_pro_max/11{i}-ab{i}/">Chery Tiggo 7 Pro Max I</a><div class="ListingItem__year">2023</div>'
        f'<div class="ListingItem__kmAge">{30 + i}\xa0000\xa0км</div>'
        f'<div class="ListingItemPrice__content">2\xa0{100 + i}\xa0000\xa0₽</div>'
        f'<div>от 45\xa0000 ₽/мес.</div></div>' for i in range(3))
    items = autoru.parse_listings(html)
    assert [(x.price, x.mileage, x.year) for x in items] == [
        (2_100_000, 30_000, 2023), (2_101_000, 31_000, 2023), (2_102_000, 32_000, 2023)]


def test_avito_cards_and_model_filter():
    def card(title, price):
        return (f'<div data-marker="item"><a data-marker="item-title" href="/moskva/avtomobili/x_12345678{price % 7}" '
                f'title="{title}">{title}</a><meta itemprop="price" content="{price}"/>'
                f'<p>{price:,} ₽</p></div>').replace(",", " ")
    html = card("Chery Tiggo 7 Pro Max 1.6 AMT, 2023, 45 000 км", 2_150_000) + \
        card("Chery Tiggo 7 Pro 1.5 CVT, 2023, 30 000 км", 1_700_000) + \
        card("Chery Tiggo 7 Pro Max 1.6 AMT, 2021, 80 000 км", 1_800_000)
    items = avito.parse_listings(html)
    assert [(x.price, x.mileage, x.year) for x in items] == [
        (2_150_000, 45_000, 2023), (1_700_000, 30_000, 2023), (1_800_000, 80_000, 2021)]
    # «Tiggo 7 Pro» ≠ «Tiggo 7 Pro Max»
    assert model_matches("Chery Tiggo 7 Pro Max 1.6 AMT, 2023", "Tiggo 7 Pro Max")
    assert not model_matches("Chery Tiggo 7 Pro Max 1.6 AMT, 2023", "Tiggo 7 Pro")
    assert model_matches("Mercedes-Benz GLE-класс 450 4MATIC, 2023", "GLE-CLASS")
    assert model_matches("Kia K5 2.5 AT GT Line, 2023", "K5")


def test_parse_price_skips_credit():
    assert parse_price("от 25 000 ₽/мес · 2 150 000 ₽") == 2_150_000


# ---------------------------------------------------------------- вывод по площадкам
def test_summary_needs_all_sites():
    ok = [L(2_000_000 + i * 10_000, 35_000 + i * 1000) for i in range(6)]
    drom = SourceResult("Дром", "https://drom", ok, total=500)
    autoru_r = SourceResult("Авто.ру", "https://autoru", [L(x.price + 100_000, x.mileage) for x in ok], total=540)
    avito_r = SourceResult("Авито", "https://avito", [L(x.price - 50_000, x.mileage) for x in ok], total=6)

    rep = summarize(CAR, [drom, autoru_r, avito_r], required=3)
    meds = {b["name"]: b["median"] for b in rep.by_source}
    assert meds == {"Дром": 2_025_000, "Авто.ру": 2_125_000, "Авито": 1_975_000}
    assert rep.median_price == 2_025_000  # медиана трёх площадок
    assert rep.listings_found == 540 and rep.liquidity == 9

    blocked = SourceResult("Авито", "", error="капчу не решили вовремя")
    with pytest.raises(IncompleteMarket, match="Авито — капчу не решили вовремя"):
        summarize(CAR, [drom, autoru_r, blocked], required=3)
    assert summarize(CAR, [drom, autoru_r, blocked], required=2).median_price == 2_075_000


def test_price_for_similar_mileage():
    items = [L(2_000_000, 30_000), L(2_050_000, 40_000), L(1_990_000, 45_000), L(1_500_000, 150_000),
             L(700_000, 40_000, text="битый")]
    med, n, status = price_for(items, 40_000)
    assert (med, n, status) == (2_000_000, 3, "ok")


class FakeSite:
    def __init__(self, name, result=None, exc=None):
        self.name, self.result, self.exc, self.calls = name, result, exc, 0

    async def fetch(self, car):
        self.calls += 1
        if self.exc:
            raise self.exc
        return self.result


def test_multimarket_marks_site_down_once():
    s = Settings()
    ok = [L(2_000_000 + i * 10_000, 40_000) for i in range(5)]
    drom = FakeSite("Дром", SourceResult("Дром", "u", ok, 100))
    autoru_s = FakeSite("Авто.ру", SourceResult("Авто.ру", "u", ok, 100))
    avito_s = FakeSite("Авито", exc=SiteUnavailable("капчу не решили вовремя"))
    mm = MultiMarket(s, sources=[drom, autoru_s, avito_s])
    said = []

    async def notify(t):
        said.append(t)
    mm.notify = notify

    async def run():
        for _ in range(2):
            with pytest.raises(IncompleteMarket):
                await mm.analyze(CAR)
    asyncio.run(run())
    assert avito_s.calls == 1  # после отказа площадку больше не дёргаем
    assert len(said) == 1 and "Авито недоступен" in said[0]


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
