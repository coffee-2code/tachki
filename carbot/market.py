"""Этап 2: рынок и спрос. Claude ищет в интернете объявления (Авито, Авто.ру, Дром)
и обсуждения на форумах/в отзывах, затем мы вытаскиваем из его разбора структурированный отчёт."""
from __future__ import annotations

import logging


from .config import Settings
from .models import Car, MarketReport

log = logging.getLogger(__name__)

RESEARCH_SYSTEM = """Ты — оценщик подержанных автомобилей для перекупщика в России. \
Твоя задача — трезво понять, за сколько и как быстро эту машину реально продать на вторичке, \
и стоит ли её вообще брать.

Как работать:
- Ищи живые объявления на auto.ru, drom.ru, avito.ru по той же марке, модели, поколению, \
году (±1 год), мотору/коробке и близкому пробегу. Отбрасывай явный мусор: битые, «под восстановление», \
без документов, объявления с ценой сильно ниже рынка без объяснения.
- Смотри, сколько таких объявлений висит и как долго (если видно), насколько часто цена снижалась.
- Ищи отзывы и обсуждения на форумах (drive2.ru, drom.ru/reviews, профильные клубы) — \
репутация модели, типичные болячки конкретного мотора и коробки, стоимость их устранения, \
спрос у покупателей.
- Будь консервативен: если данных мало, прямо так и скажи и занижай ликвидность.
Отвечай по-русски. В конце дай сводку: медиана/мин/макс цены, количество найденных объявлений, \
ликвидность 1–10, срок продажи в днях, спрос, болячки, ссылки."""

# Служебные колонки реестра, которые для оценки рынка не нужны
SKIP_COLUMNS = ("код", "фото", "изт", "дата", "адрес", "аккредит", "гос номер", "номер", "статус",
                "ключ", "дней", "vin", "ндс", "птс")

EXTRACT_SYSTEM = "Перенеси данные из разбора оценщика в поля схемы. Ничего не выдумывай: если чего-то нет, ставь 0 или пустую строку."


def _car_prompt(car: Car) -> str:
    lines = [
        f"Автомобиль: {car.brand} {car.model}",
        f"Год: {car.year or 'не указан'}",
        f"Пробег: {car.mileage if car.mileage is not None else 'не указан'} км",
    ]
    for k, v in car.extra.items():
        if v in (None, "") or any(w in str(k).lower() for w in SKIP_COLUMNS):
            continue
        lines.append(f"{k}: {str(v)[:300]}")
    return "\n".join(lines) + (
        "\n\nМашина изъята у лизингополучателя и будет перепродаваться на вторичке. "
        "Оцени рыночную цену, ликвидность и спрос. Учитывай регион и состояние, если указаны."
    )


class MarketAnalyzer:
    def __init__(self, s: Settings, client=None):
        self.s = s
        if client is None:
            try:
                import anthropic
            except ImportError as exc:
                raise RuntimeError("для MARKET_SOURCE=claude нужен pip install -r requirements-claude.txt") from exc
            client = anthropic.AsyncAnthropic()
        self.client = client

    async def _research(self, car: Car) -> str:
        messages = [{"role": "user", "content": _car_prompt(car)}]
        for _ in range(5):  # pause_turn: сервер упёрся в лимит итераций поиска — продолжаем
            resp = await self.client.beta.messages.create(
                model=self.s.anthropic_model,
                max_tokens=16000,
                system=RESEARCH_SYSTEM,
                thinking={"type": "adaptive"},
                output_config={"effort": "medium"},
                tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses": 8,
                        "user_location": {"type": "approximate", "country": "RU"}}],
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                messages=messages,
            )
            if resp.stop_reason == "refusal":
                raise RuntimeError("Модель отказалась анализировать эту машину")
            if resp.stop_reason == "pause_turn":
                messages = [messages[0], {"role": "assistant", "content": resp.content}]
                continue
            return "\n".join(b.text for b in resp.content if b.type == "text")
        raise RuntimeError("Поиск не завершился за 5 продолжений")

    async def analyze(self, car: Car) -> MarketReport:
        research = await self._research(car)
        resp = await self.client.messages.parse(
            model=self.s.anthropic_model,
            max_tokens=4000,
            system=EXTRACT_SYSTEM,
            output_config={"effort": "low"},
            messages=[{"role": "user", "content": f"{car.title}\n\nРазбор оценщика:\n{research}"}],
            output_format=MarketReport,
        )
        if resp.parsed_output is None:
            raise RuntimeError(f"Не удалось разобрать отчёт по рынку (stop_reason={resp.stop_reason})")
        return resp.parsed_output
