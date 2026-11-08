"""Устаревший генератор молочного набора.

Проект переведён на учебные файлы из папки ``data/`` (безалкогольный напиток,
1500 партий). Модуль не используется приложением; запуск заблокирован.
Актуальная пересборка: ``python -m app.data_loader``.
"""

from __future__ import annotations

import argparse
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = BASE_DIR / "batches.csv"

SUPPLIERS = [
    "ООО Молочный Путь",
    "АгроФуд",
    "Ферма Заря",
    "МилкТрейд",
    "ЭкоПродукт",
]
PRODUCT_TYPES = [
    "молоко пастеризованное",
    "кефир",
    "творог",
    "сметана",
    "йогурт",
]

ACTION_BY_LEVEL = {
    "низкий": "допустить к реализации",
    "средний": "направить на дополнительную проверку",
    "высокий": "временно изолировать партию",
}


def generate_batches(
    n: int = 400,
    output_path: str | Path | None = None,
    anchor_date: date | None = None,
    seed: int = 42,
) -> pd.DataFrame:
    """Создаёт и сохраняет CSV с синтетическими партиями."""
    if n < 1:
        raise ValueError("Количество партий должно быть положительным")

    rng = np.random.default_rng(seed)
    anchor = anchor_date or date.today()
    rows: list[dict] = []

    for index in range(1, n + 1):
        supplier = str(rng.choice(SUPPLIERS))
        product_type = str(rng.choice(PRODUCT_TYPES))

        production_date = anchor - timedelta(days=int(rng.integers(0, 26)))
        shelf_life_days = int(rng.choice([7, 10, 14, 21, 30]))
        expiry_date = production_date + timedelta(days=shelf_life_days)
        days_left = (expiry_date - anchor).days

        # Температура хранения: оптимум 2–6 °C
        storage_temp = round(float(rng.normal(4.0, 2.5)), 1)
        # Температура перевозки: оптимум 2–8 °C
        transport_temp = round(float(rng.normal(5.0, 3.0)), 1)

        # Лабораторные показатели
        ph = round(float(rng.normal(6.6, 0.25)), 2)
        fat = round(float(rng.normal(3.2, 0.4)), 1)
        acidity = round(float(rng.normal(18.0, 4.0)), 1)  # °T
        moisture = round(float(rng.normal(87.5, 1.5)), 1)  # %

        expired = expiry_date < anchor
        hard_temp_violation = storage_temp > 8 or transport_temp > 10

        # Итоговое заключение лаборатории (учебная имитация ground truth)
        if expired:
            lab_result = "не соответствует (просрочено)"
        elif hard_temp_violation:
            lab_result = "не соответствует (нарушение температурного режима)"
        elif ph < 6.2 or ph > 7.0 or acidity > 25:
            lab_result = "не соответствует (лабораторные показатели)"
        else:
            lab_result = "соответствует"

        # Целевая переменная: согласована с правилами risk_engine
        if expired or hard_temp_violation:
            risk_level = "высокий"
        elif (
            storage_temp > 6
            or transport_temp > 8
            or ph < 6.3
            or ph > 6.9
            or acidity > 22
            or days_left <= 2
        ):
            risk_level = "средний"
        else:
            risk_level = "низкий"

        rows.append(
            {
                "batch_id": f"BATCH-{index:04d}",
                "product_type": product_type,
                "supplier": supplier,
                "production_date": production_date.isoformat(),
                "expiry_date": expiry_date.isoformat(),
                "storage_temp_c": storage_temp,
                "transport_temp_c": transport_temp,
                "ph": ph,
                "fat_percent": fat,
                "acidity_tt": acidity,
                "moisture_percent": moisture,
                "lab_result": lab_result,
                "risk_level": risk_level,
                "recommended_action": ACTION_BY_LEVEL[risk_level],
            }
        )

    frame = pd.DataFrame(rows)
    target = Path(output_path).expanduser().resolve() if output_path else DEFAULT_OUTPUT
    target.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(target, index=False)
    return frame


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Генерация синтетического набора партий пищевой продукции"
    )
    parser.add_argument("--count", type=int, default=400, help="Количество партий")
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help="Путь к выходному CSV-файлу",
    )
    parser.add_argument(
        "--anchor-date",
        default=None,
        help="Опорная дата в формате YYYY-MM-DD (по умолчанию — сегодня)",
    )
    parser.add_argument("--seed", type=int, default=42, help="Зерно генератора")
    args = parser.parse_args()

    anchor = date.fromisoformat(args.anchor_date) if args.anchor_date else None
    frame = generate_batches(
        n=args.count,
        output_path=args.output,
        anchor_date=anchor,
        seed=args.seed,
    )
    print(f"Сгенерировано {len(frame)} партий. Файл: {args.output}")
    print(frame["risk_level"].value_counts().to_string())


if __name__ == "__main__":
    raise SystemExit(
        "Генератор устарел: проект использует данные из папки data/. "
        "Для пересборки нормализованного файла выполните: python -m app.data_loader"
    )
