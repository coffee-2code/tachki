"""Конвейер: файл → рынок → Автотека. Каждый следующий этап дороже, поэтому до него доходят только выжившие."""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, Optional

from . import pricing
from .autoteka import AutotekaClient, is_valid_vin
from .config import Settings
from .listings import SiteUnavailable
from .multimarket import IncompleteMarket
from .filters import file_stage_reasons
from .market import MarketAnalyzer
from .models import Car, Evaluation

log = logging.getLogger(__name__)
Progress = Callable[[str], Awaitable[None]]
HARD_FLAGS = ("залог", "розыск", "ограничен", "арест", "тотал", "такси", "каршеринг")


def _fmt(x: float) -> str:
    return f"{x:,.0f}".replace(",", " ")


def stage_file(cars: list[Car], s: Settings) -> list[Evaluation]:
    """Этап 1 — бесплатный. Возвращает оценки всех машин; прошедшие имеют stage == "candidate"."""
    evals = []
    for car in cars:
        e = Evaluation(car=car, purchase_price=pricing.purchase_price(car, s), cash_price=pricing.cash_price(car, s))
        reasons = file_stage_reasons(car, s)
        e.stage = "file" if reasons else "candidate"
        e.reasons = reasons
        if not reasons and not car.mileage:
            e.notes.append("Пробег в файле не указан — смотреть по Автотеке")
        evals.append(e)
    return evals


def market_groups(evals: list[Evaluation]) -> int:
    """Сколько разных моделей (марка/модель/год/модификация) уйдёт на анализ рынка."""
    return len({e.car.market_key for e in evals if e.stage == "candidate"})


async def evaluate(
    cars: list[Car],
    s: Settings,
    market: MarketAnalyzer,
    autoteka: Optional[AutotekaClient],
    progress: Optional[Progress] = None,
) -> list[Evaluation]:
    evals = stage_file(cars, s)
    return await evaluate_rest(evals, s, market, autoteka, progress)


async def evaluate_rest(
    evals: list[Evaluation],
    s: Settings,
    market: MarketAnalyzer,
    autoteka: Optional[AutotekaClient],
    progress: Optional[Progress] = None,
) -> list[Evaluation]:
    async def say(msg: str) -> None:
        if progress:
            await progress(msg)

    if hasattr(market, "notify"):
        market.notify = say  # сообщения площадок (капча и т. п.) — в Telegram/консоль
    to_market = [e for e in evals if e.stage == "candidate"]
    for e in to_market:
        e.stage = "market"

    # ---- этап 2: рынок
    sem = asyncio.Semaphore(s.market_concurrency)
    groups: dict[tuple, asyncio.Task] = {}
    done = 0

    blocked: list[str] = []

    async def analyze_group(car: Car):
        nonlocal done
        async with sem:
            if blocked:
                raise SiteUnavailable(blocked[0])
            try:
                return await market.analyze(car)
            finally:
                done += 1
                if done % 5 == 0 or done == len(groups):
                    await say(f"Рынок: {done}/{len(groups)}")

    key = getattr(market, "group_key", None) or (lambda c: c.market_key)
    for e in to_market:  # одинаковые машины — один запрос
        if key(e.car) not in groups:
            groups[key(e.car)] = asyncio.ensure_future(analyze_group(e.car))

    async def run_market(e: Evaluation) -> None:
        try:
            e.market = await groups[key(e.car)]
        except IncompleteMarket as exc:  # не все площадки проверены — вывод не делаем, машина остаётся кандидатом
            e.stage = "candidate"
            e.notes.append(str(exc))
            return
        except SiteUnavailable as exc:  # площадка встала совсем: непроверенные — в кандидаты, отчёт отдаём
            if not blocked:
                blocked.append(str(exc))
            e.stage = "candidate"
            e.notes.append(f"Рынок не проверен: {blocked[0]}")
            return
        except Exception as exc:  # noqa: BLE001 — одна машина не должна ронять весь файл
            log.exception("market failed for row %s", e.car.row)
            e.reject(f"Не удалось оценить рынок: {exc}")
            return
        m = e.market
        e.expected_sale = pricing.expected_sale(m, s)
        e.profit = pricing.profit(e.car, m, s)
        if m.listings_found == 0:
            e.reject("На площадках не нашлось похожих объявлений — оценить нельзя")
        if m.liquidity < s.min_liquidity:
            e.reject(f"Неликвид: {m.liquidity}/10, продажа ~{m.days_to_sell} дн.")
        if e.profit < s.min_profit_rub:
            e.reject(f"Мало прибыли: {_fmt(e.profit)} ₽ при пороге {_fmt(s.min_profit_rub)} ₽")
        if not e.reasons:
            e.stage = "autoteka"

    await asyncio.gather(*(run_market(e) for e in to_market))
    if blocked:
        await say(f"{blocked[0]} Уже проверенные машины — в отчёте, остальные — на листе «Кандидаты».")
    to_history = [e for e in to_market if e.stage == "autoteka"]
    unchecked = sum(e.stage == "candidate" for e in to_market)
    await say(f"Этап 2 — рынок: перспективных {len(to_history)} из {len(to_market)}"
              + (f", не проверено на всех площадках: {unchecked}." if unchecked else "."))

    # ---- этап 3: Автотека
    if autoteka is None or not autoteka.enabled:
        for e in to_history:
            e.passed = True
            e.stage = "ok"
            e.notes.append("Автотека: пробить VIN вручную")
        if to_history:
            await say("Автотека не подключена: перспективные отмечены как «проверить вручную».")
        return evals

    for i, e in enumerate(to_history, 1):
        if not is_valid_vin(e.car.vin):
            e.reject(f"Нет корректного VIN («{e.car.vin or 'пусто'}») — Автотеку не тратим")
            continue
        await say(f"Автотека {i}/{len(to_history)}: {e.car.title}, {e.car.vin}")
        try:
            h = await autoteka.check(e.car.vin)
        except Exception as exc:  # noqa: BLE001
            log.exception("autoteka failed for %s", e.car.vin)
            e.reject(f"Автотека: ошибка — {exc}")
            continue
        e.history = h
        if h.owners and h.owners > s.max_owners:
            e.reject(f"Владельцев: {h.owners} (максимум {s.max_owners})")
        if h.accidents > s.max_accidents:
            e.reject(f"ДТП: {h.accidents} (максимум {s.max_accidents})")
        if h.mileage_rollback:
            e.reject("Скрученный пробег по истории записей")
        if h.last_known_mileage and e.car.mileage and h.last_known_mileage > e.car.mileage + s.mileage_tolerance_km:
            e.reject(f"В истории пробег {_fmt(h.last_known_mileage)} км больше заявленного {_fmt(e.car.mileage)}")
        if h.last_known_mileage and h.last_known_mileage > s.max_mileage_km:
            e.reject(f"Реальный пробег большой: {_fmt(h.last_known_mileage)} км")
        hard = [f for f in h.other_flags if any(w in f.lower() for w in HARD_FLAGS)]
        if hard:
            e.reject("Юридические/эксплуатационные риски: " + "; ".join(hard))
        soft = [f for f in h.other_flags if f not in hard]
        if soft:
            e.notes.append("Обратить внимание: " + "; ".join(soft))
        if not e.reasons:
            e.passed = True
            e.stage = "ok"

    ok = sum(e.passed for e in evals)
    await say(f"Этап 3 — Автотека: берём {ok}.")
    return evals
