"""Рынок по Дрому и Авто.ру: самые низкие цены среди подходящих объявлений в радиусе от Москвы.

Для каждой площадки:
1. Берём объявления того же года с похожим пробегом по всей России, по возрастанию цены.
2. Где стоит машина: дальше MAX_DISTANCE_KM от Москвы (по дорогам, примерно) — отбрасываем сразу.
3. Самые дешёвые из оставшихся открываем и читаем описание: учёт только РФ (не Беларусь, Казахстан…),
   машина в наличии (не «под заказ», «в пути»), не битая. Не подошло — отбрасываем с причиной.
3. Цена площадки — самое дешёвое подходящее объявление (и второе для контроля).
Рыночная цена — самая низкая из площадок. Вывод о выгоде — только когда проверены все площадки.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict
from typing import Awaitable, Callable, Optional

from .browser import Browser
from .config import Settings
from .geo import FOREIGN, locate
from .listings import (RUSSIA_STEPS, SiteUnavailable, SourcePrice, SourceResult, comparable, liquidity_from_count,
                       listing_flags)
from .models import Car, MarketReport

log = logging.getLogger(__name__)


class MultiMarketReport(MarketReport):
    by_source: list[dict] = []  # SourcePrice по каждой площадке


class IncompleteMarket(RuntimeError):
    """Проверены не все обязательные площадки — вывод о выгоде не делаем."""


def _short(x: Optional[int]) -> str:
    return "—" if not x else f"{x / 1_000_000:.2f} млн".replace(".", ",")


def place_flags(text: str, max_km: int, road_factor: float) -> tuple[list[str], str]:
    """(причины не брать, «город, ~км») по месту, где стоит машина."""
    loc = locate(text, road_factor)
    if loc is None:
        return [], ""
    name, km = loc
    place = f"{name}, ~{km} км" if km else name
    if name in FOREIGN:
        return [f"не РФ: {name}"], place
    if km > max_km:
        return [f"далеко: {place}"], place
    return [], place


async def lowest_valid(src, car: Car, res: SourceResult, max_checks: int,
                       max_km: int = 2000, road_factor: float = 1.2) -> SourcePrice:
    """Самые дешёвые подходящие объявления площадки: каждое открываем и читаем описание."""
    sp = SourcePrice(res.name, res.url, total=res.total if res.total is not None else len(res.items))
    if res.error:
        sp.status = f"не проверено: {res.error.splitlines()[0][:160]}"
        return sp
    same_year = [x for x in res.items if x.year in (None, car.year)]
    comps = comparable(res.items, car.year, car.mileage)
    if not same_year:
        sp.status = "нет объявлений"
        return sp
    if not comps:
        sp.status = "нет похожих по пробегу"
        return sp
    valid = []
    for x in comps:
        if len(valid) == 2 or sp.checked >= max_checks:
            break
        # сначала по карточке в выдаче — далёкие и явно «не те» не открываем
        pflags, x.place = place_flags(x.text, max_km, road_factor)
        flags = pflags + listing_flags(x.text)
        if not flags and x.url and hasattr(src, "detail_text"):
            sp.checked += 1
            try:
                detail = await src.detail_text(x.url)
                flags = listing_flags(detail)
                if not x.place:  # в карточке города не было — ищем на странице объявления
                    pflags, x.place = place_flags(detail, max_km, road_factor)
                    flags = pflags + flags
            except SiteUnavailable:
                raise
            except Exception as exc:  # noqa: BLE001 — не открылось одно объявление
                log.warning("%s: не открылось объявление %s: %s", res.name, x.url, exc)
                flags = ["не открылось"]
        if not flags and not x.place:
            flags = ["город не определён"]
        if flags:
            sp.skipped.append(f"{x.price:,} ₽ — {', '.join(flags)}".replace(",", " "))
        else:
            valid.append(x)
    if not valid:
        sp.status = "нет подходящих" if sp.checked < max_checks else f"нет подходящих среди {max_checks} дешёвых"
        return sp
    sp.low, sp.low_url, sp.low_place = valid[0].price, valid[0].url, valid[0].place
    if len(valid) > 1:
        sp.second, sp.second_url = valid[1].price, valid[1].url
    return sp


def summarize(car: Car, by: list[SourcePrice], required: int) -> MultiMarketReport:
    checked = [b for b in by if not b.status.startswith("не проверено")]
    if len(checked) < required:
        missing = "; ".join(f"{b.name} — {b.status.removeprefix('не проверено: ')}" for b in by
                            if b.status.startswith("не проверено"))
        raise IncompleteMarket(f"Проверены не все площадки ({len(checked)} из {required}): {missing}")

    lows = [b.low for b in checked if b.low]
    notes = " · ".join(
        f"{b.name}: {b.total if b.total is not None else '—'} объявл. по России, "
        + (f"мин. {_short(b.low)} ({b.low_place})" if b.low else b.status)
        + (f"; отброшено дешевле: {len(b.skipped)}" if b.skipped else "") for b in by)
    total = max((b.total or 0) for b in checked) if checked else 0
    if not lows:
        return MultiMarketReport(median_price=0, min_price=0, max_price=0, listings_found=0, liquidity=1,
                                 days_to_sell=180, demand_notes=notes, known_issues="",
                                 sources=[b.url for b in by], by_source=[asdict(b) for b in by])
    low = min(lows)
    liq, days = liquidity_from_count(total, low, RUSSIA_STEPS)
    return MultiMarketReport(
        median_price=low,  # рыночная цена = самая низкая подходящая
        min_price=low, max_price=max(lows), listings_found=total, liquidity=liq, days_to_sell=days,
        demand_notes=notes, known_issues="", sources=[b.url for b in by], by_source=[asdict(b) for b in by],
    )


class MultiMarket:
    def __init__(self, s: Settings, sources: Optional[list] = None):
        self.s = s
        self.browser = Browser(s)
        self.notify: Optional[Callable[[str], Awaitable[None]]] = None
        self.browser.notify = self._say
        if sources is None:
            from .autoru import AutoRuMarket
            from .drom import DromMarket
            make = {"drom": lambda: DromMarket(s), "autoru": lambda: AutoRuMarket(s, self.browser)}
            sources = [make[k]() for k in s.market_sources if k in make]
        self.sources = sources
        self.required = min(s.sources_required or len(sources), len(sources))
        self.down: dict[str, str] = {}  # площадки, которые отказали, — дальше их не мучаем

    async def _say(self, text: str) -> None:
        if self.notify:
            await self.notify(text)

    def group_key(self, car: Car) -> tuple:
        return (car.row,)  # страницы кэшируются по модели внутри площадок, похожие по пробегу — на каждую машину

    async def _down(self, name: str, exc: Exception) -> str:
        first = name not in self.down  # параллельные машины получают тот же отказ — сообщаем один раз
        self.down.setdefault(name, str(exc))
        if first:
            await self._say(f"{name} недоступен: {str(exc).rstrip('.')}. "
                            "Машины без этой площадки останутся в «Кандидатах».")
        return self.down[name]

    async def _one(self, src, car: Car) -> SourcePrice:
        if src.name in self.down:
            return await lowest_valid(src, car, SourceResult(src.name, "", error=self.down[src.name]), 0)
        try:
            res = await src.fetch(car)
            return await lowest_valid(src, car, res, self.s.detail_checks, self.s.max_distance_km, self.s.road_factor)
        except SiteUnavailable as exc:
            err = await self._down(src.name, exc)
        except Exception as exc:  # noqa: BLE001 — сбой одной площадки на одной машине
            log.exception("%s failed for row %s", src.name, car.row)
            err = f"ошибка: {exc}"
        return await lowest_valid(src, car, SourceResult(src.name, "", error=err), 0)

    async def analyze(self, car: Car) -> MultiMarketReport:
        by = await asyncio.gather(*(self._one(src, car) for src in self.sources))  # площадки параллельно
        return summarize(car, list(by), self.required)

    async def aclose(self) -> None:
        for src in self.sources:
            if hasattr(src, "aclose"):
                await src.aclose()
        await self.browser.close()
