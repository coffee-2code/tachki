import asyncio
from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook

from carbot import pricing
from carbot.autoteka import detect_rollback
from carbot.config import Settings
from carbot.excel_io import parse_vat, read_cars, split_title, write_result
from carbot.filters import file_stage_reasons
from carbot.models import Car, HistoryReport, MarketReport
from carbot.pipeline import evaluate


@pytest.fixture
def s():
    st = Settings()
    st.autoteka_client_id = st.autoteka_client_secret = ""
    st.blacklist = []
    return st


def car(**kw):
    base = dict(row=2, brand="Kia", model="K5", year=Settings().current_year - 3, mileage=45_000,
                price=3_000_000, vin="XWEG2417BN0012345", full_vat=True)
    base.update(kw)
    return Car(**base)


def test_prices(s):
    c = car(price=1_000_000)
    assert pricing.purchase_price(c, s) == pytest.approx(850_000)
    assert pricing.cash_price(c, s) == pytest.approx(731_000)          # 850 000 × 0,86
    assert pricing.cash_price(car(price=1_000_000, full_vat=False), s) == pytest.approx(850_000)


def test_file_filters(s):
    assert file_stage_reasons(car(), s) == []
    assert "Старая" in file_stage_reasons(car(year=2010), s)[0]
    assert "Большой пробег" in file_stage_reasons(car(mileage=260_000), s)[0]
    assert "км/год" in file_stage_reasons(car(year=s.current_year - 2, mileage=110_000), s)[0]
    s.blacklist = ["lada"]
    assert file_stage_reasons(car(brand="Lada", model="Granta"), s)


def test_rollback():
    assert not detect_rollback([("2021-01-01", 10_000), ("2022-01-01", 30_000)], 3000)
    assert detect_rollback([("2021-01-01", 90_000), ("2023-05-01", 60_000)], 3000)
    assert not detect_rollback([("2021-01-01", 50_000), ("2021-02-01", 48_500)], 3000)  # погрешность


def test_parse_helpers():
    assert parse_vat("С НДС 20%", False) is True
    assert parse_vat("без НДС", True) is False
    assert split_title("Land Rover Defender 110") == ("Land Rover", "Defender 110")
    assert split_title("Toyota Camry 2.5") == ("Toyota", "Camry 2.5")


def test_read_messy_excel(tmp_path, s):
    wb = Workbook()
    ws = wb.active
    ws.append(["Реестр ТС на реализацию"])  # шапка-заголовок над таблицей
    ws.append([])
    ws.append(["№", "Наименование ТС", "Год выпуска", "Пробег, км", "VIN", "Стоимость, руб. с НДС", "НДС"])
    ws.append([1, "Toyota Camry", 2021, "68 000", "XW7BF4FK30S123456", "3 150 000 ₽", "полный"])
    ws.append([2, "Land Rover Discovery Sport", 2019, 90000, "SALCA2BN5KH812345", 2500000, "без НДС"])
    ws.append([None, "Итого", None, None, None, None, None])
    p = tmp_path / "in.xlsx"
    wb.save(p)

    cars = read_cars(p, s)
    assert len(cars) == 2
    a, b = cars
    assert (a.brand, a.model, a.year, a.mileage, a.price, a.full_vat) == ("Toyota", "Camry", 2021, 68000, 3150000, True)
    assert a.vin == "XW7BF4FK30S123456"
    assert (b.brand, b.model, b.full_vat) == ("Land Rover", "Discovery Sport", False)


class FakeMarket:
    def __init__(self, reports):
        self.reports = reports

    async def analyze(self, c):
        return self.reports[c.row]


class FakeAutoteka:
    enabled = True

    def __init__(self, hist):
        self.hist = hist

    async def check(self, vin):
        return self.hist[vin]


def mr(median, liq=8, n=20):
    return MarketReport(median_price=median, min_price=median - 200_000, max_price=median + 200_000,
                        listings_found=n, liquidity=liq, days_to_sell=20, demand_notes="спрос есть",
                        known_issues="", sources=["https://auto.ru/x"])


def test_pipeline(tmp_path, s):
    y = s.current_year
    cars = [
        car(row=2, vin="AAAAAAAAAAAAAAAA1", price=2_000_000),                  # хорошая
        car(row=3, vin="AAAAAAAAAAAAAAAA2", year=2009),                        # старая
        car(row=4, vin="AAAAAAAAAAAAAAAA3", price=2_000_000),                  # неликвид
        car(row=5, vin="AAAAAAAAAAAAAAAA4", price=2_000_000),                  # мало прибыли
        car(row=6, vin="AAAAAAAAAAAAAAAA5", price=2_000_000),                  # 3 владельца
        car(row=7, vin="AAAAAAAAAAAAAAAA6", price=2_000_000, mileage=40_000),  # скрутка
    ]
    # платим 2 000 000 × 0,85 × 0,86 = 1 462 000
    market = FakeMarket({2: mr(2_000_000), 4: mr(2_000_000, liq=3), 5: mr(1_600_000),
                         6: mr(2_000_000), 7: mr(2_000_000)})
    hist = {
        "AAAAAAAAAAAAAAAA1": HistoryReport(owners=1, accidents=0, last_known_mileage=45_000),
        "AAAAAAAAAAAAAAAA5": HistoryReport(owners=3, accidents=0),
        "AAAAAAAAAAAAAAAA6": HistoryReport(owners=1, accidents=0, mileage_rollback=True, last_known_mileage=120_000),
    }
    evals = asyncio.run(evaluate(cars, s, market, FakeAutoteka(hist)))
    by = {e.car.row: e for e in evals}
    assert by[2].passed and by[2].profit == pytest.approx(2_000_000 * 0.95 - 1_462_000 - s.prep_cost_rub)
    assert by[3].stage == "file" and not by[3].passed
    assert "Неликвид" in by[4].reasons[0]
    assert "Мало прибыли" in by[5].reasons[0]
    assert "Владельцев" in by[6].reasons[0]
    assert any("Скрученный" in r for r in by[7].reasons)
    assert [e.car.row for e in evals if e.passed] == [2]

    out = tmp_path / "out.xlsx"
    write_result(evals, out, s)
    wb = load_workbook(out)
    assert wb.sheetnames == ["Берём", "Отсеяно", "Условия"]
    assert wb["Берём"].max_row == 2 and wb["Отсеяно"].max_row == 6
