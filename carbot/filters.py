"""Этап 1: отсев «мусора» по данным из самого файла — бесплатно и мгновенно."""
from __future__ import annotations

from .config import Settings
from .models import Car


def _n(x: float) -> str:
    return f"{x:,.0f}".replace(",", " ")


def file_stage_reasons(car: Car, s: Settings) -> list[str]:
    reasons: list[str] = []

    # --- колонки реестра лизинговой компании (если есть)
    vtype = car.get("тип тс", "тип транспорт").lower()
    if vtype and not any(t in vtype for t in s.vehicle_types):
        return [f"Не легковой: {vtype}"]  # грузовики, прицепы, спецтехнику дальше не разбираем
    status = car.get("статус изт", "статус лота", "статус продажи").lower()
    if status and any(st in status for st in s.skip_statuses):
        reasons.append(f"Уже занята: статус «{status}»")
    cond = car.get("состояние").lower()
    if cond and any(b in cond for b in s.bad_conditions):
        reasons.append(f"Состояние: {car.get('состояние')}")
    comments = car.comments().lower()
    bad = [w for w in s.bad_words if w in comments]
    if bad:
        reasons.append("В комментариях: " + ", ".join(bad))

    # --- общие правила
    name = f"{car.brand} {car.model}".lower()
    if any(name == b or name.startswith(b + " ") or car.brand.lower() == b for b in s.blacklist):
        reasons.append("Марка/модель в чёрном списке")

    if car.year:
        if s.min_year and car.year < s.min_year:
            reasons.append(f"Старая: {car.year} г. (нужно от {s.min_year})")
        elif not s.min_year and s.current_year - car.year > s.max_age_years:
            reasons.append(f"Старая: {car.year} г. (максимум {s.max_age_years} лет)")
    else:
        reasons.append("Не указан год выпуска")

    if car.mileage:  # 0 или пусто — пробег неизвестен, его покажет Автотека
        if car.mileage > s.max_mileage_km:
            reasons.append(f"Большой пробег: {_n(car.mileage)} км (максимум {_n(s.max_mileage_km)})")
        elif car.year:
            years = max(s.current_year - car.year, 1)
            per_year = car.mileage / years
            if per_year > s.max_km_per_year:
                reasons.append(f"Убитая эксплуатация: ~{_n(per_year)} км/год")

    if car.price <= 0:
        reasons.append("Нет цены")
    return reasons
