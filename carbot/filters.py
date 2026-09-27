"""Этап 1: отсев «мусора» по данным из самого файла — бесплатно и мгновенно."""
from __future__ import annotations

from .config import Settings
from .models import Car


def file_stage_reasons(car: Car, s: Settings) -> list[str]:
    reasons: list[str] = []
    name = f"{car.brand} {car.model}".lower()
    if any(name == b or name.startswith(b + " ") or car.brand.lower() == b for b in s.blacklist):
        reasons.append("Марка/модель в чёрном списке")

    if car.year:
        age = s.current_year - car.year
        if age > s.max_age_years:
            reasons.append(f"Старая: {car.year} г. ({age} лет, максимум {s.max_age_years})")
    else:
        reasons.append("Не указан год выпуска")

    if car.mileage is not None:
        if car.mileage > s.max_mileage_km:
            reasons.append(f"Большой пробег: {car.mileage:,} км (максимум {s.max_mileage_km:,})".replace(",", " "))
        elif car.year:
            years = max(s.current_year - car.year, 1)
            per_year = car.mileage / years
            if per_year > s.max_km_per_year:
                reasons.append(f"Убитая эксплуатация: ~{per_year:,.0f} км/год".replace(",", " "))

    if car.price <= 0:
        reasons.append("Нет цены")
    return reasons
