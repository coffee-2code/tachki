"""Telegram-бот: присылаете Excel — получаете Excel с машинами, которые стоит брать."""
from __future__ import annotations

import asyncio
import html
import logging
import tempfile
from collections import Counter
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import CallbackQuery, FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup, Message

from .autoteka import AutotekaClient
from .config import settings as s
from .excel_io import read_cars, write_result
from .market import MarketAnalyzer
from .pipeline import evaluate_rest, market_groups, stage_file

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("carbot")

dp = Dispatcher()
_busy: set[int] = set()
_pending: dict[int, tuple[list, str]] = {}  # файл ждёт подтверждения платного этапа

HELP = (
    "Пришлите Excel-файл (.xlsx) со списком машин — я отберу ликвидные с хорошей маржой.\n\n"
    "Нужны колонки: <b>Марка</b> и <b>Модель</b> (или одна «Наименование»), <b>Год</b>, <b>Пробег</b>, "
    "<b>Цена</b>, <b>VIN</b>. Колонка <b>НДС</b> — по желанию.\n\n"
    "Как считаю:\n"
    f"1. Цена − {s.seller_discount:.0%} (скидка продавца), для полного НДС ещё × {s.cash_factor} — цена за наличку.\n"
    f"2. Выкидываю: не легковые, в резерве/отгрузке, HARD и «удовлетворительное», тотал/ДТП/«не на ходу» "
    f"в комментариях, старше {s.min_year or s.current_year - s.max_age_years} г., "
    f"пробег больше {s.max_mileage_km:,} км.\n".replace(",", " ") +
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
        f"Год от: {s.min_year or s.current_year - s.max_age_years}\nПробег до: {s.max_mileage_km} км ({s.max_km_per_year} км/год)\n"
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
    await m.answer("Принял файл, читаю…")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "input.xlsx"
            await bot.download(doc, destination=src)
            try:
                cars = await asyncio.to_thread(read_cars, src, s)
            except ValueError as exc:
                await m.answer(html.escape(str(exc)))
                return
    except Exception as exc:  # noqa: BLE001
        log.exception("read failed")
        await m.answer(f"Не смог прочитать файл: {html.escape(str(exc))}")
        return
    if not cars:
        await m.answer("В файле не нашлось ни одной строки с ценой.")
        return

    # Этап 1 бесплатный — делаем сразу и спрашиваем, запускать ли платный рынок
    evals = stage_file(cars, s)
    to_market = [e for e in evals if e.stage == "candidate"]
    groups = market_groups(evals)
    _pending[uid] = (evals, Path(doc.file_name).stem)

    top = Counter(e.reasons[0].split(":")[0] for e in evals if e.stage == "file" and e.reasons).most_common(6)
    lines = [
        f"Строк с ценой: <b>{len(cars)}</b>.",
        f"Отсеяно на этапе 1: <b>{len(evals) - len(to_market)}</b>",
        *[f"  • {html.escape(k)}: {n}" for k, n in top],
        "",
        f"Кандидатов: <b>{len(to_market)}</b> — это <b>{groups}</b> разных моделей для анализа рынка.",
        f"Прикидка стоимости анализа: ~${groups * s.usd_per_market_check:.0f} (API Claude).",
    ]
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"Анализ рынка ({groups})", callback_data="run")],
        [InlineKeyboardButton(text="Только список кандидатов", callback_data="list")],
        [InlineKeyboardButton(text="Отмена", callback_data="cancel")],
    ])
    await m.answer("\n".join(lines), reply_markup=kb if to_market else None)
    if not to_market:
        _pending.pop(uid, None)


async def _send_result(m: Message, evals: list, stem: str, caption: str) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / f"Оценка_{stem}.xlsx"
        await asyncio.to_thread(write_result, evals, out, s)
        await m.answer_document(FSInputFile(out), caption=caption[:1000])


@dp.callback_query(F.data.in_({"run", "list", "cancel"}))
async def on_choice(cb: CallbackQuery) -> None:
    uid = cb.from_user.id
    if s.allowed_user_ids and uid not in s.allowed_user_ids:
        return
    job = _pending.pop(uid, None)
    await cb.answer()
    await cb.message.edit_reply_markup(reply_markup=None)
    if not job:
        await cb.message.answer("Этот файл уже обработан или устарел — пришлите его заново.")
        return
    evals, stem = job
    if cb.data == "cancel":
        await cb.message.answer("Отменил.")
        return
    if cb.data == "list":
        n = sum(e.stage == "candidate" for e in evals)
        await _send_result(cb.message, evals, stem, f"Кандидаты после бесплатного отсева: {n}. Рынок не проверялся.")
        return
    if uid in _busy:
        await cb.message.answer("Ещё обрабатываю прошлый файл, дождитесь результата.")
        return

    _busy.add(uid)
    status = await cb.message.answer("Запускаю анализ рынка…")

    async def progress(text: str) -> None:
        try:
            await status.edit_text(html.escape(text))
        except Exception:  # noqa: BLE001 — Telegram ругается на одинаковый текст
            pass
        log.info(text)

    try:
        market = MarketAnalyzer(s)
        autoteka = AutotekaClient(s)
        try:
            evals = await evaluate_rest(evals, s, market, autoteka, progress)
        finally:
            await autoteka.aclose()
        good = sorted([e for e in evals if e.passed], key=lambda e: e.profit or 0, reverse=True)
        lines = [f"Готово. Из {len(evals)} берём <b>{len(good)}</b>."]
        for e in good[:10]:
            lines.append(f"• {html.escape(e.car.title)} — прибыль ~{e.profit:,.0f} ₽, ликвидность {e.market.liquidity}/10"
                         .replace(",", " "))
        await _send_result(cb.message, evals, stem, "\n".join(lines))
    except Exception as exc:  # noqa: BLE001
        log.exception("processing failed")
        await cb.message.answer(f"Ошибка при обработке: {html.escape(str(exc))}")
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
