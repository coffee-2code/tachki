"""Конвейер: файл → рынок → Автотека. Каждый следующий этап дороже, поэтому до него доходят только выжившие."""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, Optional

from . import pricing
from .autoteka import AutotekaClient, is_valid_vin
from .config import Settings
from .filters import file_stage_reasons
from .market import MarketAnalyzer
from .models import Car, Evaluation

log = logging.getLogger(__name__)
Progress = Callable[[str], Awaitable[None]]
HARD_FLAGS = ("залог", "розыск", "ограничен", "арест", "тотал", "такси", "каршеринг")


def _fmt(x: float) -> str:
    return f"{x:,.0f}".replace(",", " ")


async def evaluate(
    cars: list[Car],
    s: Settings,
    market: MarketAnalyzer,
    autoteka: Optional[AutotekaClient],
    progress: Optional[Progress] = None,
) -> list[Evaluation]:
    async def say(msg: str) -> None:
        if progress:
            await progress(msg)

    # ---- этап 1: файл
    evals = []
    for car in cars:
        e = Evaluation(car=car, purchase_price=pricing.purchase_price(car, s), cash_price=pricing.cash_price(car, s))
        reasons = file_stage_reasons(car, s)
        if reasons:
            e.stage = "file"
            e.reasons = reasons
        else:
            e.stage = "market"
        evals.append(e)
    to_market = [e for e in evals if e.stage == "market"]
    await say(f"Этап 1 — файл: {len(cars)} машин, отсеяно {len(cars) - len(to_market)} "
              f"(старые, большой пробег, чёрный список). На анализ рынка: {len(to_market)}.")

    # ---- этап 2: рынок
    sem = asyncio.Semaphore(s.market_concurrency)
    done = 0

    async def run_market(e: Evaluation) -> None:
        nonlocal done
        async with sem:
            try:
                e.market = await market.analyze(e.car)
            except Exception as exc:  # noqa: BLE001 — одна машина не должна ронять весь файл
                log.exception("market failed for row %s", e.car.row)
                e.reject(f"Не удалось оценить рынок: {exc}")
                return
            finally:
                done += 1
                if done % 5 == 0 or done == len(to_market):
                    await say(f"Рынок: {done}/{len(to_market)}")
        m = e.market
        e.expected_sale = pricing.expected_sale(m, s)
        e.profit = pricing.profit(e.car, m, s)
        if m.listings_found == 0:
            e.reject("На площадках не нашлось похожих объявлений — оценить нельзя")
        if m.liquidity < s.min_liquidity:
            e.reject(f"Неликвид: {m.liquidity}/10, продажа ~{m.days_to_sell} дн. {m.demand_notes[:200]}")
        if e.profit < s.min_profit_rub:
            e.reject(f"Мало прибыли: {_fmt(e.profit)} ₽ (платим {_fmt(e.cash_price)}, "
                     f"продадим ~{_fmt(e.expected_sale)})")
        if not e.reasons:
            e.stage = "autoteka"

    await asyncio.gather(*(run_market(e) for e in to_market))
    to_history = [e for e in to_market if e.stage == "autoteka"]
    await say(f"Этап 2 — рынок: перспективных {len(to_history)} из {len(to_market)}.")

    # ---- этап 3: Автотека
    if autoteka is None or not autoteka.enabled:
        for e in to_history:
            e.passed = True
            e.stage = "ok"
            e.reasons.append("Автотека не подключена — пробейте VIN вручную перед покупкой")
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
            e.reasons.append("Обратить внимание: " + "; ".join(soft))
        if not any(r for r in e.reasons if not r.startswith("Обратить внимание")):
            e.passed = True
            e.stage = "ok"

    ok = sum(e.passed for e in evals)
    await say(f"Этап 3 — Автотека: берём {ok}.")
    return evals
