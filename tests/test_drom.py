import asyncio

import httpx
import pytest

from carbot import drom
from carbot.config import Settings
from carbot.drom import (DromBlocked, DromMarket, liquidity_from_count, parse_listings, parse_mileage, parse_total,
                         pick_slug)
from carbot.models import Car


def card(price, km, extra="", city="Москва"):
    return (f'<div data-ftid="bulls-list_bull"><a href="https://auto.drom.ru/chery/tiggo_7_pro_max/'
            f'5{price}.html"><h3 data-ftid="bull_title">Chery Tiggo 7 Pro Max, 2023</h3></a>'
            f'<span data-ftid="bull_description-item">1.6 л (186 л.с.), бензин,</span>'
            f'<span data-ftid="bull_description-item">{km} тыс. км{extra}</span>'
            f'<span data-ftid="bull_price">{price:,}</span> ₽<span data-ftid="bull_location">{city}</span></div>'
            ).replace(",", "\xa0")


def listing_page(items, total=508):
    cards = "".join(card(*it) for it in items)
    return (f"<html><head><title>Купить Чери Тигго 7 Про Макс б/у 2023 от 1 434 000 рублей – {total} объявлений "
            f"на Дроме</title></head><body>{cards}</body></html>")


def test_parse_helpers():
    assert parse_mileage("1.6 л, 45 тыс. км, 4WD") == 45_000
    assert parse_mileage("новый, 120 км") == 120
    assert parse_total(listing_page([])) == 508
    assert liquidity_from_count(508, 2_000_000) == (9, 14)
    assert liquidity_from_count(12, 2_000_000) == (4, 90)
    assert liquidity_from_count(508, 20_000_000)[0] == 7


def test_parse_cards_both_layouts():
    items = parse_listings(listing_page([(2_100_000, 30, ""), (1_950_000, 55, "")]))
    assert [(x.price, x.mileage) for x in items] == [(2_100_000, 30_000), (1_950_000, 55_000)]
    fallback = ('<div><a href="https://auto.drom.ru/moscow/lada/vesta/555123456.html">Lada Vesta, 2022</a>'
                '<span>1.6 л, 40 тыс. км</span><span>1 250 000 ₽</span></div>')
    (x,) = parse_listings(fallback)
    assert (x.price, x.mileage) == (1_250_000, 40_000)


@pytest.mark.parametrize("name,expected", [
    ("Tiggo 7 Pro Max", "tiggo_7_pro_max"),
    ("VESTA Седан", "vesta"),
    ("VESTA SW", "vesta"),
    ("GRANTA Лифтбек", "granta"),
    ("GLE-CLASS", "gle"),
    ("4X4", "4x4_2121"),
    ("MAZDA 6", "mazda6"),
    ("UNI-K", "uni-k"),
])
def test_pick_model_slug(name, expected):
    options = {drom.norm(s): s for s in ["tiggo_7_pro_max", "tiggo_7_pro", "vesta", "granta", "gle", "gls",
                                         "4x4_2121", "mazda6", "uni-k", "uni-v"]}
    assert pick_slug(name, options, drom.MODEL_NOISE) == expected


def test_pick_brand_slug_from_link_text():
    options = {"mercedesbenz": "mercedes-benz", "landrover": "land_rover", "moskvich": "moskvich", "lada": "lada"}
    assert pick_slug("Mercedes-Benz", options) == "mercedes-benz"
    assert pick_slug("Land Rover", options) == "land_rover"
    assert pick_slug("Москвич", options) == "moskvich"
    assert pick_slug("LADA", options) == "lada"


def make_market(tmp_path, monkeypatch, routes, status=200):
    monkeypatch.chdir(tmp_path)  # drom_slugs.json и debug/ — во временной папке
    seen = []

    def handler(req):
        seen.append(str(req.url))
        body = routes.get(str(req.url))
        return httpx.Response(status if body is not None else 404, text=body or "")

    s = Settings()
    s.drom_delay_min = s.drom_delay_max = 0
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return DromMarket(s, http), seen


def test_analyze_end_to_end(tmp_path, monkeypatch):
    """Самая низкая цена — только среди подходящих: не битые, не далеко, не под заказ, учёт РФ."""
    base = "https://auto.drom.ru"
    items = [
        (700_000, 30, ", битый"),                  # битая — отбрасываем по карточке
        (1_800_000, 32, "", "Новосибирск"),        # далеко — отбрасываем, не открывая
        (1_850_000, 28, ""),                       # в описании «под заказ» — отбрасываем после открытия
        (1_870_000, 30, "", "Минск"),              # не РФ
        (1_900_000, 35, "", "Казань"),             # подходит — самая низкая
        (1_950_000, 25, ""),                       # подходит — вторая
        (2_000_000, 29, ""),
        (1_500_000, 150, ""),                      # пробег не похож — не сравниваем
    ]
    detail = "<html><body><main>Chery Tiggo 7 Pro Max. Автомобиль под заказ из Китая, срок 30 дней.</main></body></html>"
    routes = {
        f"{base}/": '<a href="https://auto.drom.ru/chery/">Chery</a><a href="https://auto.drom.ru/lada/">Лада</a>',
        f"{base}/chery/": '<a href="https://auto.drom.ru/chery/tiggo_7_pro_max/">Tiggo 7 Pro Max</a>'
                          '<a href="https://auto.drom.ru/chery/tiggo_4_pro/">Tiggo 4 Pro</a>',
        f"{base}/chery/tiggo_7_pro_max/year-2023/used/?order=price": listing_page(items, total=8),
        f"{base}/chery/tiggo_7_pro_max/51850000.html": detail,
    }
    market, seen = make_market(tmp_path, monkeypatch, routes)
    car = Car(row=5, brand="Chery", model="Tiggo 7 Pro Max", year=2023, mileage=30_000, price=2_085_000)
    rep = asyncio.run(market.analyze(car))
    (site,) = rep.by_source
    assert rep.median_price == 1_900_000 and site["low"] == 1_900_000 and site["second"] == 1_950_000
    assert site["low_place"].startswith("Казань")
    assert site["low_url"].endswith("51900000.html")
    why = "\n".join(site["skipped"])
    assert "битая" in why and "далеко: Новосибирск" in why and "под заказ" in why and "не РФ: Минск" in why
    assert not any("51800000" in u for u in seen)  # далёкую даже не открывали
    assert rep.listings_found == 8
    # вторая машина той же модели — поиск из кэша, новых обращений к поиску нет
    n = sum("used" in u for u in seen)
    asyncio.run(market.analyze(Car(row=6, brand="Chery", model="Tiggo 7 Pro Max", year=2023, mileage=28_000,
                                   price=2_000_000)))
    assert sum("used" in u for u in seen) == n
    assert (tmp_path / "drom_slugs.json").exists()


def test_blocked(tmp_path, monkeypatch):
    market, _ = make_market(tmp_path, monkeypatch, {"https://auto.drom.ru/": "captcha"}, status=403)
    with pytest.raises(DromBlocked):
        asyncio.run(market.analyze(Car(row=2, brand="Chery", model="Tiggo", year=2023, mileage=1, price=1)))
