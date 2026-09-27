from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from pydantic import BaseModel, Field


@dataclass
class Car:
    row: int                    # номер строки в исходном файле
    brand: str
    model: str
    year: Optional[int]
    mileage: Optional[int]
    price: float                # цена из файла
    vin: str = ""
    full_vat: bool = True
    extra: dict = field(default_factory=dict)  # прочие колонки файла, как есть

    @property
    def title(self) -> str:
        return f"{self.brand} {self.model} {self.year or ''}".strip()

    def get(self, *keys: str) -> str:
        """Значение прочей колонки по началу/вхождению названия: car.get("статус изт", "статус")."""
        for k in keys:
            for h, v in self.extra.items():
                if k in str(h).lower().strip():
                    return "" if v is None else str(v).strip()
        return ""

    def comments(self) -> str:
        return " ".join(str(v) for h, v in self.extra.items() if "коммент" in str(h).lower() and v)

    @property
    def market_key(self) -> tuple:
        """Одинаковые машины оцениваем на рынке один раз."""
        return (self.brand.lower(), self.model.lower(), self.year, self.get("модификация").lower())


class MarketReport(BaseModel):
    """То, что Claude возвращает после поиска по Авито/Авто.ру/Дрому и форумам."""
    median_price: int = Field(description="Медианная цена похожих объявлений, руб.")
    min_price: int = Field(description="Нижняя граница адекватных цен, руб.")
    max_price: int = Field(description="Верхняя граница адекватных цен, руб.")
    listings_found: int = Field(description="Сколько похожих объявлений найдено")
    liquidity: int = Field(description="Ликвидность 1..10: 10 — улетает за неделю, 1 — стоит месяцами")
    days_to_sell: int = Field(description="Оценка срока продажи, дней")
    demand_notes: str = Field(description="Спрос и репутация модели: что пишут на форумах и в отзывах")
    known_issues: str = Field(description="Типичные болячки этого поколения/мотора/коробки")
    sources: list[str] = Field(description="Ссылки на объявления и обсуждения, на которые опирается оценка")


@dataclass
class HistoryReport:
    """Выжимка из отчёта Автотеки."""
    owners: Optional[int] = None
    accidents: int = 0
    mileage_records: list[tuple[str, int]] = field(default_factory=list)  # (дата, км)
    mileage_rollback: bool = False
    last_known_mileage: Optional[int] = None
    other_flags: list[str] = field(default_factory=list)  # залог, розыск, такси, лизинг…
    raw_path: str = ""


@dataclass
class Evaluation:
    car: Car
    purchase_price: float = 0.0      # после скидки продавца
    cash_price: float = 0.0          # реально платим
    stage: str = "file"              # file | market | autoteka | ok
    passed: bool = False
    reasons: list[str] = field(default_factory=list)   # почему отсеяна
    notes: list[str] = field(default_factory=list)     # на что обратить внимание (не отсев)
    market: Optional[MarketReport] = None
    history: Optional[HistoryReport] = None
    expected_sale: Optional[float] = None
    profit: Optional[float] = None

    def reject(self, reason: str) -> "Evaluation":
        self.passed = False
        self.reasons.append(reason)
        return self
