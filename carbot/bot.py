"""Telegram-бот: присылаете Excel — получаете Excel с машинами, которые стоит брать."""
from __future__ import annotations

import asyncio
import html
import logging
import tempfile
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import FSInputFile, Message

from .autoteka import AutotekaClient
from .config import settings as s
from .excel_io import read_cars, write_result
from .market import MarketAnalyzer
from .pipeline import evaluate

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("carbot")

dp = Dispatcher()
_busy: set[int] = set()

HELP = (
    "Пришлите Excel-файл (.xlsx) со списком машин — я отберу ликвидные с хорошей маржой.\n\n"
    "Нужны колонки: <b>Марка</b> и <b>Модель</b> (или одна «Наименование»), <b>Год</b>, <b>Пробег</b>, "
    "<b>Цена</b>, <b>VIN</b>. Колонка <b>НДС</b> — по желанию.\n\n"
    "Как считаю:\n"
    f"1. Цена − {s.seller_discount:.0%} (скидка продавца), для полного НДС ещё × {s.cash_factor} — цена за наличку.\n"
    f"2. Выкидываю старше {s.max_age_years} лет и с пробегом больше {s.max_mileage_km:,} км.\n".replace(",", " ") +
    "3. Ищу цены и спрос на Авито, Авто.ру, Дроме и форумах; неликвид и малую маржу убираю.\n"
    f"4. Только оставшиеся пробиваю в Автотеке: больше {s.max_owners} владельцев, больше {s.max_accidents} ДТП "
    "или скрутка — мимо.\n\n"
    "/settings — текущие пороги"
)


def _allowed(m: Message) -> bool:
    return not s.allowed_user_ids or (m.from_user and m.from_user.id in s.allowed_user_ids)


@dp.message(Command("start", "help"))
async def cmd_start(m: Message) -> None:
    if not _allowed(m):
        await m.answer(f"Нет доступа. Ваш id: {m.from_user.id} — передайте его владельцу бота.")
        return
    await m.answer(HELP)


@dp.message(Command("settings"))
async def cmd_settings(m: Message) -> None:
    if not _allowed(m):
        return
    await m.answer(
        f"Скидка продавца: {s.seller_discount:.0%}\nКоэф. «нал» при полном НДС: {s.cash_factor}\n"
        f"Возраст до: {s.max_age_years} лет\nПробег до: {s.max_mileage_km} км ({s.max_km_per_year} км/год)\n"
        f"Ликвидность от: {s.min_liquidity}/10\nТорг при продаже: {s.sale_discount:.0%}\n"
        f"Подготовка: {s.prep_cost_rub} ₽\nПрибыль от: {s.min_profit_rub} ₽\n"
        f"Владельцев до: {s.max_owners}\nДТП до: {s.max_accidents}\n"
        f"Автотека: {'подключена' if s.autoteka_client_id else 'не подключена'}\n\n"
        "Меняются в файле .env, потом перезапуск бота."
    )


@dp.message(F.document)
async def on_document(m: Message, bot: Bot) -> None:
    if not _allowed(m):
        return
    doc = m.document
    if not (doc.file_name or "").lower().endswith((".xlsx", ".xlsm")):
        await m.answer("Нужен файл .xlsx. Если у вас .xls или .csv — пересохраните в Excel как .xlsx.")
        return
    uid = m.from_user.id
    if uid in _busy:
        await m.answer("Ещё обрабатываю прошлый файл, дождитесь результата.")
        return
    _busy.add(uid)
    status = await m.answer("Принял файл, читаю…")

    async def progress(text: str) -> None:
        try:
            await status.edit_text(html.escape(text))
        except Exception:  # noqa: BLE001 — Telegram ругается на одинаковый текст
            pass
        log.info(text)

    try:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "input.xlsx"
            await bot.download(doc, destination=src)
            try:
                cars = read_cars(src, s)
            except ValueError as exc:
                await m.answer(html.escape(str(exc)))
                return
            if not cars:
                await m.answer("В файле не нашлось ни одной строки с ценой.")
                return

            market = MarketAnalyzer(s)
            autoteka = AutotekaClient(s)
            try:
                evals = await evaluate(cars, s, market, autoteka, progress)
            finally:
                await autoteka.aclose()

            out = Path(tmp) / f"Оценка_{Path(doc.file_name).stem}.xlsx"
            write_result(evals, out, s)
            good = sorted([e for e in evals if e.passed], key=lambda e: e.profit or 0, reverse=True)
            lines = [f"Готово. Из {len(evals)} машин берём <b>{len(good)}</b>."]
            for e in good[:10]:
                lines.append(f"• {html.escape(e.car.title)} — прибыль ~{e.profit:,.0f} ₽, ликвидность {e.market.liquidity}/10"
                             .replace(",", " "))
            await m.answer_document(FSInputFile(out), caption="\n".join(lines)[:1000])
    except Exception as exc:  # noqa: BLE001
        log.exception("processing failed")
        await m.answer(f"Ошибка при обработке: {html.escape(str(exc))}")
    finally:
        _busy.discard(uid)


@dp.message()
async def fallback(m: Message) -> None:
    if _allowed(m):
        await m.answer("Пришлите .xlsx со списком машин или /help.")


async def main() -> None:
    if not s.telegram_token:
        raise SystemExit("Не задан TELEGRAM_BOT_TOKEN в .env")
    from aiogram.client.default import DefaultBotProperties
    bot = Bot(s.telegram_token, default=DefaultBotProperties(parse_mode="HTML"))
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
