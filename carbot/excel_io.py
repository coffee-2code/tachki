"""Чтение присланного Excel и сборка итогового файла."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from openpyxl import load_workbook

from .config import Settings
from .models import Car

# Возможные названия колонок (в нижнем регистре, ищем вхождение)
ALIASES = {
    "brand": ["марка", "бренд", "производитель", "make", "brand"],
    "model": ["модель", "model"],
    "title": ["наименование", "автомобиль", "название", "транспортное средство", "car", "name"],
    "year": ["год выпуска", "год", "year"],
    "mileage": ["пробег", "mileage", "км"],
    "price": ["цена", "стоимость", "срс", "price", "сумма"],
    "vin": ["vin", "вин", "идентификационный"],
    "vat": ["ндс", "vat", "налог"],
}

MULTIWORD_BRANDS = [
    "land rover", "li auto", "great wall", "alfa romeo", "aston martin", "lynk & co", "rolls royce",
    "mercedes benz", "range rover", "gac motor", "jac motors",
]

VIN_RE = re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b")


def parse_number(v) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace("\xa0", " ").replace(" ", "").replace(",", ".")
    m = re.search(r"-?\d+(\.\d+)?", s)
    return float(m.group()) if m else None


def parse_vat(v, default: bool) -> bool:
    if v is None or str(v).strip() == "":
        return default
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if any(w in s for w in ("без", "нет", "не обл", "no", "false")) or s in ("0", "-"):
        return False
    if any(w in s for w in ("полн", "да", "с ндс", "вкл", "yes", "true", "20", "+")) or s == "1":
        return True
    return default


def _match(header: str, key: str) -> bool:
    h = header.lower().strip()
    return any(a == h or a in h for a in ALIASES[key])


def _detect_header(ws) -> tuple[int, dict[str, int]]:
    """Ищем в первых 20 строках строку-заголовок, где есть хотя бы цена и марка/наименование."""
    for r in range(1, min(ws.max_row, 20) + 1):
        cols: dict[str, int] = {}
        for c in range(1, ws.max_column + 1):
            val = ws.cell(r, c).value
            if val is None:
                continue
            h = str(val)
            low = h.lower()
            if _match(h, "brand") and _match(h, "model") and "title" not in cols:
                cols["title"] = c  # «Марка/модель» в одной колонке
                continue
            if "сумма ндс" in low or "в т.ч" in low or low.startswith("ндс,"):
                continue  # сумма налога — не цена и не признак
            if _match(h, "price") and "без ндс" in low and "price" in cols:
                continue  # есть цена с НДС — цену без НДС не берём
            if _match(h, "price") and "с ндс" in low:
                cols["price"] = c  # цена с НДС приоритетнее
                continue
            # порядок важен: «год выпуска» не должен стать моделью, «цена с НДС» — признаком НДС и т. п.
            for key in ("vin", "year", "mileage", "price", "vat", "brand", "model", "title"):
                if key not in cols and _match(h, key):
                    cols[key] = c
                    break
        if "price" in cols and ("brand" in cols or "title" in cols or "model" in cols):
            return r, cols
    raise ValueError(
        "Не нашёл строку с заголовками. Нужны хотя бы колонки «Цена» и «Марка»/«Модель»/«Наименование»."
    )


def split_title(title: str) -> tuple[str, str]:
    t = re.sub(r"\s+", " ", title.replace("-", " ")).strip()
    low = t.lower()
    for b in MULTIWORD_BRANDS:
        if low.startswith(b + " ") or low == b:
            n = len(b.split())
            parts = title.split()
            return " ".join(parts[:n]), " ".join(parts[n:])
    parts = title.split(maxsplit=1)
    return (parts[0], parts[1] if len(parts) > 1 else "")


def read_cars(path: str | Path, s: Settings) -> list[Car]:
    wb = load_workbook(path, data_only=True, read_only=False)
    ws = wb.active
    header_row, cols = _detect_header(ws)
    headers = {c: str(ws.cell(header_row, c).value or f"col{c}") for c in range(1, ws.max_column + 1)}

    cars: list[Car] = []
    for r in range(header_row + 1, ws.max_row + 1):
        get = lambda key: ws.cell(r, cols[key]).value if key in cols else None  # noqa: E731
        price = parse_number(get("price"))
        if not price:
            continue  # пустая/итоговая строка

        brand, model = str(get("brand") or "").strip(), str(get("model") or "").strip()
        title = str(get("title") or "").strip()
        if title and not brand:
            brand, rest = split_title(title)
            model = model or rest
        elif title and not model:
            model = title

        # год: число или дата; иногда год зашит в наименование
        year_raw = get("year")
        year = getattr(year_raw, "year", None) or parse_number(year_raw)
        if not year and title:
            m = re.search(r"\b(19[89]\d|20[0-4]\d)\b", title)
            year = float(m.group()) if m else None

        mileage = parse_number(get("mileage"))
        vin = str(get("vin") or "").strip().upper()
        if not vin:  # VIN иногда лежит в соседних колонках или в примечании
            for c in range(1, ws.max_column + 1):
                m = VIN_RE.search(str(ws.cell(r, c).value or "").upper())
                if m:
                    vin = m.group()
                    break

        extra = {headers[c]: ws.cell(r, c).value for c in headers if c not in cols.values()}
        cars.append(Car(
            row=r, brand=brand, model=model,
            year=int(year) if year else None,
            mileage=int(mileage) if mileage is not None else None,
            price=price, vin=vin,
            full_vat=parse_vat(get("vat"), s.default_full_vat) if "vat" in cols else s.default_full_vat,
            extra=extra,
        ))
    return cars


# Итоговый отчёт живёт в report.py; реэкспорт для старых импортов
from .report import is_not_passenger, write_result  # noqa: E402,F401

__all__ = ["read_cars", "write_result", "is_not_passenger", "parse_vat", "split_title"]
