"""Этап 3: история по VIN через API Автотеки (бизнес-доступ через Avito API).

Вызываем только для машин, прошедших рынок, — каждый отчёт стоит денег.

Схема вызовов (Avito API для бизнеса, раздел «Автотека»):
  POST /token                         — OAuth client_credentials
  POST /autoteka/v1/previews {vin}    — превью (бесплатно), даёт previewId
  POST /autoteka/v1/reports {previewId} — покупка отчёта, даёт reportId
  GET  /autoteka/v1/reports/{id}      — готовый отчёт (ждём status=success)
Пути вынесены в константы: если в вашем договоре они отличаются — поправьте здесь.
Сырые отчёты сохраняются в reports/, чтобы по ним можно было перепроверить разбор.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx
from pydantic import BaseModel, Field

from .config import Settings
from .models import HistoryReport

log = logging.getLogger(__name__)

BASE_URL = "https://api.avito.ru"
TOKEN_PATH = "/token"
PREVIEW_PATH = "/autoteka/v1/previews"
REPORT_PATH = "/autoteka/v1/reports"
REPORTS_DIR = Path("reports")


class _MileageRecord(BaseModel):
    date: str = Field(description="Дата записи, ГГГГ-ММ-ДД")
    km: int = Field(description="Пробег, км")


class _HistoryExtract(BaseModel):
    owners: int = Field(description="Число владельцев по ПТС/ГИБДД; 0 если неизвестно")
    accidents: int = Field(description="Число ДТП (записи ГИБДД и страховые случаи с повреждениями)")
    mileage_records: list[_MileageRecord] = Field(description="Все записи пробега из отчёта")
    flags: list[str] = Field(description="Прочие риски: залог, розыск, ограничения, такси, каршеринг, лизинг, "
                                         "тотал, отзывные кампании не пройдены. Пусто, если ничего нет")


def detect_rollback(records: list[tuple[str, int]], tolerance: int) -> bool:
    """Скрутка: пробег по более поздней записи меньше, чем по более ранней (с допуском)."""
    rs = sorted(records, key=lambda x: x[0])
    peak = 0
    for _, km in rs:
        if km + tolerance < peak:
            return True
        peak = max(peak, km)
    return False


FLAG_KEYS = {"pledge": "залог", "restrict": "ограничения", "wanted": "розыск", "taxi": "такси",
             "carshar": "каршеринг", "leasing": "лизинг", "total": "тотал", "arrest": "арест"}


def _walk(obj, path=""):
    """Все пары (путь.ключ, значение) на любой глубине."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}".lower()
            yield p, v
            yield from _walk(v, p)
    elif isinstance(obj, list):
        for v in obj:
            if isinstance(v, dict):
                yield path, v  # элемент списка — тоже запись (пробег, ДТП)
            yield from _walk(v, path)


def extract_heuristic(raw: dict) -> Optional[_HistoryExtract]:
    """Разбор отчёта без нейросети: ищем по названиям полей владельцев, ДТП, пробеги и ограничения.
    None — если в отчёте не нашлось ни владельцев, ни пробегов (формат неизвестен)."""
    owners = accidents = None
    records: list[_MileageRecord] = []
    flags: list[str] = []
    for path, v in _walk(raw):
        key = path.rsplit(".", 1)[-1]
        if "owner" in key:
            if isinstance(v, int) and not isinstance(v, bool) and ("count" in key or "number" in key or key == "owners"):
                owners = max(owners or 0, v)
            elif isinstance(v, list) and owners is None:
                owners = len(v)
        if any(w in key for w in ("accident", "dtp", "crash")):
            if isinstance(v, list):
                accidents = max(accidents or 0, len(v))
            elif isinstance(v, int) and not isinstance(v, bool) and "count" in key:
                accidents = max(accidents or 0, v)
        if isinstance(v, dict):
            km = next((v[k] for k in v if any(w in k.lower() for w in ("mileage", "odometer", "probeg"))
                       and isinstance(v[k], (int, float)) and not isinstance(v[k], bool)), None)
            date = next((v[k] for k in v if "date" in k.lower() and isinstance(v[k], str)), None)
            if km is not None and date:
                records.append(_MileageRecord(date=date[:10], km=int(km)))
        truthy = v is True or (isinstance(v, list) and len(v) > 0) or (
            isinstance(v, str) and v.strip().lower() not in ("", "false", "no", "нет", "0"))
        if truthy and not isinstance(v, dict):
            for fk, name in FLAG_KEYS.items():
                if fk in key and name not in flags:
                    flags.append(name)
    if owners is None and not records:
        return None
    return _HistoryExtract(owners=owners or 0, accidents=accidents or 0, mileage_records=records, flags=flags)


class AutotekaClient:
    def __init__(self, s: Settings, claude=None):
        self.s = s
        self._claude = claude  # нужен только для разбора отчёта Автотеки; создаётся при первом отчёте
        self._token: Optional[str] = None
        self.http = httpx.AsyncClient(base_url=BASE_URL, timeout=60)

    @property
    def enabled(self) -> bool:
        return bool(self.s.autoteka_client_id and self.s.autoteka_client_secret)

    async def _auth(self) -> dict:
        if not self._token:
            r = await self.http.post(TOKEN_PATH, data={
                "grant_type": "client_credentials",
                "client_id": self.s.autoteka_client_id,
                "client_secret": self.s.autoteka_client_secret,
            })
            r.raise_for_status()
            self._token = r.json()["access_token"]
        return {"Authorization": f"Bearer {self._token}"}

    @staticmethod
    def _find(obj, key: str):
        """Ищет ключ на любой глубине: форматы ответов у API бывают вложенными по-разному."""
        if isinstance(obj, dict):
            if key in obj:
                return obj[key]
            for v in obj.values():
                found = AutotekaClient._find(v, key)
                if found is not None:
                    return found
        elif isinstance(obj, list):
            for v in obj:
                found = AutotekaClient._find(v, key)
                if found is not None:
                    return found
        return None

    async def fetch_raw(self, vin: str) -> dict:
        h = await self._auth()
        r = await self.http.post(PREVIEW_PATH, json={"vin": vin}, headers=h)
        r.raise_for_status()
        preview_id = self._find(r.json(), "previewId")
        if not preview_id:
            raise RuntimeError(f"Автотека не вернула previewId: {r.text[:300]}")

        r = await self.http.post(REPORT_PATH, json={"previewId": preview_id}, headers=h)
        r.raise_for_status()
        report_id = self._find(r.json(), "reportId")
        if not report_id:
            raise RuntimeError(f"Автотека не вернула reportId: {r.text[:300]}")

        for _ in range(60):  # до ~5 минут
            r = await self.http.get(f"{REPORT_PATH}/{report_id}", headers=h)
            r.raise_for_status()
            data = r.json()
            status = str(self._find(data, "status") or "").lower()
            if status in ("success", "done", "ready", "completed"):
                return data
            if status in ("error", "failed", "notfound", "not_found"):
                raise RuntimeError(f"Автотека: отчёт не собран ({status})")
            await asyncio.sleep(5)
        raise RuntimeError("Автотека: отчёт не готов за 5 минут")

    @property
    def claude(self):
        if self._claude is None:
            try:
                import anthropic
            except ImportError as exc:
                raise RuntimeError("для разбора Автотеки нужен pip install -r requirements-claude.txt") from exc
            self._claude = anthropic.AsyncAnthropic()
        return self._claude

    async def _extract(self, raw: dict) -> _HistoryExtract:
        resp = await self.claude.messages.parse(
            model=self.s.anthropic_model,
            max_tokens=8000,
            output_config={"effort": "low"},
            system="Это JSON-отчёт Автотеки об истории автомобиля. Извлеки поля по схеме. Ничего не выдумывай.",
            messages=[{"role": "user", "content": json.dumps(raw, ensure_ascii=False)}],
            output_format=_HistoryExtract,
        )
        if resp.parsed_output is None:
            raise RuntimeError("Не удалось разобрать отчёт Автотеки")
        return resp.parsed_output

    async def check(self, vin: str) -> HistoryReport:
        raw = await self.fetch_raw(vin)
        REPORTS_DIR.mkdir(exist_ok=True)
        path = REPORTS_DIR / f"{vin}_{datetime.now():%Y%m%d_%H%M%S}.json"
        path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")

        ex = extract_heuristic(raw)
        if ex is None:  # незнакомый формат — пробуем Claude, если он подключён
            try:
                ex = await self._extract(raw)
            except RuntimeError as exc:
                raise RuntimeError(f"не удалось разобрать отчёт, он сохранён в {path}") from exc
        records = [(m.date, m.km) for m in ex.mileage_records]
        return HistoryReport(
            owners=ex.owners or None,
            accidents=ex.accidents,
            mileage_records=records,
            mileage_rollback=detect_rollback(records, self.s.mileage_tolerance_km),
            last_known_mileage=max((km for _, km in records), default=None),
            other_flags=ex.flags,
            raw_path=str(path),
        )

    async def aclose(self) -> None:
        await self.http.aclose()


def is_valid_vin(vin: str) -> bool:
    return bool(re.fullmatch(r"[A-HJ-NPR-Z0-9]{17}", vin or ""))
