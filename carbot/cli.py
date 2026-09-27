"""Оценка файла без Telegram:  python -m carbot.cli реестр.xlsx  [--only-list]

Результат сохраняется рядом: Оценка_<имя файла>.xlsx
"""
from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from .autoteka import AutotekaClient
from .config import settings as s
from .drom import DromMarket
from .excel_io import is_not_passenger, read_cars, write_result
from .market import MarketAnalyzer
from .pipeline import evaluate_rest, stage_file


async def run(path: Path, only_list: bool) -> Path:
    cars = read_cars(path, s)
    evals = stage_file(cars, s)
    passenger = [e for e in evals if not is_not_passenger(e)]
    cands = [e for e in evals if e.stage == "candidate"]
    print(f"Строк с ценой: {len(cars)}, легковых: {len(passenger)}, кандидатов после бесплатного отсева: {len(cands)}")

    if not only_list and cands:
        market = DromMarket(s) if s.market_source == "drom" else MarketAnalyzer(s)
        autoteka = AutotekaClient(s)

        async def progress(text: str) -> None:
            print(text, flush=True)

        try:
            evals = await evaluate_rest(evals, s, market, autoteka, progress)
        finally:
            await autoteka.aclose()
            if hasattr(market, "aclose"):
                await market.aclose()

    out = path.with_name(f"Оценка_{path.stem}.xlsx")
    write_result(evals, out, s)
    good = sorted([e for e in evals if e.passed], key=lambda e: e.profit or 0, reverse=True)
    print(f"\nБерём: {len(good)}")
    for e in good[:15]:
        print(f"  {e.car.title:<40} прибыль ~{e.profit:>12,.0f} ₽".replace(",", " "))
    print(f"\nОтчёт: {out}")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Оценка машин из Excel")
    ap.add_argument("file", type=Path, help="Excel-файл (.xlsx)")
    ap.add_argument("--only-list", action="store_true", help="только бесплатный отсев, без рынка")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    asyncio.run(run(args.file, args.only_list))


if __name__ == "__main__":
    main()
