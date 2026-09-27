"""Рынок по трём площадкам: Дром + Авто.ру + Авито. Вывод — только когда проверены все (SOURCES_REQUIRED)."""
from __future__ import annotations

import asyncio
import logging
import statistics
from dataclasses import asdict
from typing import Awaitable, Callable, Optional

from .browser import Browser
from .config import Settings
from .listings import SiteUnavailable, SourcePrice, SourceResult, liquidity_from_count, price_for
from .models import Car, MarketReport

log = logging.getLogger(__name__)


class MultiMarketReport(MarketReport):
    by_source: list[dict] = []  # SourcePrice по каждой площадке


class IncompleteMarket(RuntimeError):
    """Проверены не все обязательные площадки — вывод о выгоде не делаем."""


def _short(x: Optional[int]) -> str:
    return "—" if not x else f"{x / 1_000_000:.2f} млн".replace(".", ",")


def summarize(car: Car, results: list[SourceResult], required: int) -> MultiMarketReport:
    by: list[SourcePrice] = []
    for r in results:
        if r.error:
            by.append(SourcePrice(r.name, r.url, status=f"не проверено: {r.error}"))
            continue
        items = [x for x in r.items if x.year in (None, car.year)]
        med, n, status = price_for(items, car.mileage)
        by.append(SourcePrice(r.name, r.url, med, n, r.total if r.total is not None else len(items), status))

    checked = [b for b in by if not b.status.startswith("не проверено")]
    if len(checked) < required:
        missing = "; ".join(f"{b.name} — {b.status.removeprefix('не проверено: ')}" for b in by
                            if b.status.startswith("не проверено"))
        raise IncompleteMarket(f"Проверены не все площадки ({len(checked)} из {required}): {missing}")

    # итоговая цена — медиана медиан площадок (с тремя площадками это «средняя» из трёх, выбросы не тянут)
    good = [b.median for b in checked if b.median and b.status == "ok"]
    usable = good or [b.median for b in checked if b.median]
    notes = " · ".join(f"{b.name}: {b.total if b.total is not None else '—'} объявл., медиана {_short(b.median)}"
                       + ("" if b.status == "ok" else f" ({b.status})") for b in by)
    if not usable:
        return MultiMarketReport(median_price=0, min_price=0, max_price=0, listings_found=0, liquidity=1,
                                 days_to_sell=180, demand_notes=notes, known_issues="",
                                 sources=[b.url for b in by], by_source=[asdict(b) for b in by])
    med = int(statistics.median(usable))
    total = max((b.total or 0) for b in checked)
    liq, days = liquidity_from_count(total, med)
    return MultiMarketReport(
        median_price=med, min_price=min(usable), max_price=max(usable), listings_found=total,
        liquidity=liq, days_to_sell=days, demand_notes=notes, known_issues="",
        sources=[b.url for b in by], by_source=[asdict(b) for b in by],
    )


class MultiMarket:
    def __init__(self, s: Settings, sources: Optional[list] = None):
        self.s = s
        self.browser = Browser(s)
        self.notify: Optional[Callable[[str], Awaitable[None]]] = None
        self.browser.notify = self._say
        if sources is None:
            from .autoru import AutoRuMarket
            from .avito import AvitoMarket
            from .drom import DromMarket
            make = {"drom": lambda: DromMarket(s), "autoru": lambda: AutoRuMarket(s, self.browser),
                    "avito": lambda: AvitoMarket(s, self.browser)}
            sources = [make[k]() for k in s.market_sources if k in make]
        self.sources = sources
        self.required = min(s.sources_required or len(sources), len(sources))
        self.down: dict[str, str] = {}  # площадки, которые отказали, — дальше их не мучаем

    async def _say(self, text: str) -> None:
        if self.notify:
            await self.notify(text)

    def group_key(self, car: Car) -> tuple:
        return (car.row,)  # страницы кэшируются по модели внутри площадок, похожие по пробегу — на каждую машину

    async def _one(self, src, car: Car) -> SourceResult:
        if src.name in self.down:
            return SourceResult(src.name, "", error=self.down[src.name])
        try:
            return await src.fetch(car)
        except SiteUnavailable as exc:
            self.down[src.name] = str(exc)
            await self._say(f"{src.name} недоступен: {exc}. Машины без этой площадки останутся в «Кандидатах».")
            return SourceResult(src.name, "", error=str(exc))
        except Exception as exc:  # noqa: BLE001 — сбой одной площадки на одной машине
            log.exception("%s failed for row %s", src.name, car.row)
            return SourceResult(src.name, "", error=f"ошибка: {exc}")

    async def analyze(self, car: Car) -> MultiMarketReport:
        results = await asyncio.gather(*(self._one(src, car) for src in self.sources))  # площадки параллельно
        return summarize(car, list(results), self.required)

    async def aclose(self) -> None:
        for src in self.sources:
            if hasattr(src, "aclose"):
                await src.aclose()
        await self.browser.close()
