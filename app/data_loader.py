"""Загрузка и нормализация пяти наборов данных из папки ``data/``.

В папке лежат CSV и XLSX-копии пяти сценариев партий безалкогольного напитка.
XLSX-файлы в этом наборе фактически содержат CSV-текст, поэтому загрузчик
определяет формат по сигнатуре файла, а не только по расширению.

Результат — единый DataFrame с нормализованными колонками, уровнем риска,
действием и человекочитаемым описанием сценария.
"""

from __future__ import annotations

import argparse
import logging
import re
from io import StringIO
from pathlib import Path
from typing import Any

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = BASE_DIR / "data"
DEFAULT_OUTPUT = BASE_DIR / "batches.csv"

logger = logging.getLogger(__name__)

REQUIRED_COLUMNS = [
    "batch_id",
    "production_date",
    "expiry_date",
    "storage_temp_c",
    "transport_temp_c",
    "supplier",
    "dry_matter_pct",
    "acidity_cm3",
    "co2_mass_pct",
    "lead_mg_kg",
    "cadmium_mg_kg",
    "arsenic_mg_kg",
    "kmafanm_cfu_cm3",
    "bgkp_cfu_cm3",
    "yeast_cfu_cm3",
    "mold_cfu_cm3",
    "lab_conclusion",
]

NUMERIC_COLUMNS = [
    "dry_matter_pct",
    "acidity_cm3",
    "co2_mass_pct",
    "lead_mg_kg",
    "cadmium_mg_kg",
    "arsenic_mg_kg",
    "kmafanm_cfu_cm3",
    "bgkp_cfu_cm3",
    "yeast_cfu_cm3",
    "mold_cfu_cm3",
]

COMPARE_COLUMNS = ["lab_conclusion", *NUMERIC_COLUMNS]

RISK_ACTION = {
    "низкий": "допустить к реализации",
    "средний": "направить на дополнительную проверку",
    "высокий": "временно изолировать партию",
}

SCENARIO_META: dict[str, dict[str, str]] = {
    "Идеальная партия": {
        "code": "AP",
        "title": "Идеальная партия",
        "risk_level": "низкий",
        "description": "Все показатели в пределах нормы, лаборатория подтвердила соответствие.",
    },
    "Опасный брак": {
        "code": "RE",
        "title": "Опасный брак",
        "risk_level": "высокий",
        "description": "Превышение тяжёлых металлов и микробиологических показателей.",
    },
    "Риск_поставщика": {
        "code": "SR",
        "title": "Риск поставщика",
        "risk_level": "средний",
        "description": "Повышенные дрожжи, плесень и КМАФАнМ у поставщика при формальном соответствии.",
    },
    "Технический сбой": {
        "code": "MF",
        "title": "Технический сбой",
        "risk_level": "средний",
        "description": "Нестабильная карбонизация: CO₂ выходит за рабочий диапазон.",
    },
    "Технологический брак": {
        "code": "DE",
        "title": "Технологический брак",
        "risk_level": "высокий",
        "description": "Повышенная кислотность и отклонение содержания CO₂.",
    },
}

SCENARIO_BY_CODE = {meta["code"]: title for title, meta in SCENARIO_META.items()}


def _normalize_text(value: Any) -> str:
    return str(value or "").strip().replace("ё", "е").replace("Ё", "Е")


def risk_level_from_conclusion(conclusion: Any) -> str:
    """Сопоставляет заключение лаборатории с уровнем риска."""
    text = _normalize_text(conclusion).lower()
    if not text:
        return "средний"
    if "не соответствует" in text:
        return "высокий"
    if "опасно" in text or "брак" in text:
        return "высокий"
    if "риск" in text or "проверк" in text or "нестабиль" in text:
        return "средний"
    if "соответствует" in text:
        return "низкий"
    return "средний"


def parse_temperature_range(value: Any) -> tuple[float, float, float, str]:
    """Разбирает значение вида ``0-18`` или число.

    Возвращает минимум, максимум, середину и подпись диапазона.
    """
    if value is None:
        return 0.0, 18.0, 9.0, "0–18 °C"

    text = str(value).strip().replace("−", "-").replace("–", "-")
    numbers = [
        float(item)
        for item in re.findall(r"(?<!\d)-?\d+(?:[.,]\d+)?", text)
    ]
    if len(numbers) >= 2:
        low, high = sorted(numbers[:2])
        return low, high, round((low + high) / 2, 1), f"{low:g}–{high:g} °C"
    if len(numbers) == 1:
        number = numbers[0]
        return number, number, number, f"{number:g} °C"
    return 0.0, 18.0, 9.0, "0–18 °C"


def _read_table(path: Path) -> pd.DataFrame:
    """Читает CSV или XLSX, включая XLSX с CSV-текстом внутри одной колонки."""
    if path.suffix.lower() in {".xlsx", ".xlsm"}:
        with path.open("rb") as stream:
            signature = stream.read(4)
        if signature == b"PK\x03\x04":
            frame = pd.read_excel(path)
            if frame.shape[1] == 1:
                header = str(frame.columns[0])
                # Некоторые XLSX содержат по одной CSV-строке в каждой ячейке.
                if "," in header and "batch_id" in header:
                    text = header + "\n" + "\n".join(
                        str(item) for item in frame.iloc[:, 0].tolist()
                    )
                    return pd.read_csv(StringIO(text))
            return frame
        # Расширение XLSX, но внутри обычный CSV-текст.
        return pd.read_csv(path)
    return pd.read_csv(path)


def _clean_frame(frame: pd.DataFrame, path: Path) -> pd.DataFrame:
    frame = frame.copy()
    frame.columns = [str(column).strip() for column in frame.columns]
    missing = [column for column in REQUIRED_COLUMNS if column not in frame.columns]
    if missing:
        raise ValueError(f"{path.name}: отсутствуют колонки {', '.join(missing)}")

    # Удаляем повторяющиеся строки-заголовки и пустые идентификаторы.
    batch_ids = frame["batch_id"].astype(str).str.strip()
    frame = frame[batch_ids.ne("batch_id") & batch_ids.ne("nan") & batch_ids.ne("")]
    frame["batch_id"] = frame["batch_id"].astype(str).str.strip()
    frame = frame[frame["batch_id"].str.match(r"^[A-Z]{2}-\d{4}-\d{3}$", na=False)]

    scenario = path.stem
    meta = SCENARIO_META.get(scenario)
    if meta is None:
        prefix = frame["batch_id"].str.slice(0, 2).mode()
        code = str(prefix.iloc[0]) if len(prefix) else "XX"
        title = SCENARIO_BY_CODE.get(code, scenario)
        meta = SCENARIO_META.get(
            title,
            {
                "code": code,
                "title": title,
                "risk_level": "средний",
                "description": f"Набор {scenario}",
            },
        )

    frame["scenario"] = meta["title"]
    frame["scenario_code"] = meta["code"]
    frame["scenario_description"] = meta["description"]
    frame["product_type"] = "безалкогольный напиток"
    frame["lab_conclusion"] = frame["lab_conclusion"].map(_normalize_text)

    for column in NUMERIC_COLUMNS:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")

    parsed_storage = frame["storage_temp_c"].map(parse_temperature_range)
    parsed_transport = frame["transport_temp_c"].map(parse_temperature_range)
    frame["storage_temp_min_c"] = [item[0] for item in parsed_storage]
    frame["storage_temp_max_c"] = [item[1] for item in parsed_storage]
    frame["storage_temp_c"] = [item[2] for item in parsed_storage]
    frame["storage_temp_range"] = [item[3] for item in parsed_storage]
    frame["transport_temp_min_c"] = [item[0] for item in parsed_transport]
    frame["transport_temp_max_c"] = [item[1] for item in parsed_transport]
    frame["transport_temp_c"] = [item[2] for item in parsed_transport]
    frame["transport_temp_range"] = [item[3] for item in parsed_transport]

    frame["production_date"] = pd.to_datetime(
        frame["production_date"], errors="coerce"
    ).dt.date
    frame["expiry_date"] = pd.to_datetime(frame["expiry_date"], errors="coerce").dt.date

    derived_level = frame["lab_conclusion"].map(risk_level_from_conclusion)
    frame["risk_level"] = derived_level
    frame["recommended_action"] = frame["risk_level"].map(RISK_ACTION)
    frame["risk_reason"] = frame["scenario_description"]
    frame["source_file"] = path.name
    return frame


def load_combined(data_dir: str | Path | None = None) -> pd.DataFrame:
    """Читает все CSV/XLSX из папки, объединяет и удаляет дубликаты."""
    directory = Path(data_dir or DEFAULT_DATA_DIR).expanduser().resolve()
    if not directory.exists():
        raise FileNotFoundError(f"Папка с данными не найдена: {directory}")

    files = sorted(
        path
        for path in directory.iterdir()
        if path.is_file() and path.suffix.lower() in {".csv", ".xlsx", ".xlsm", ".xls"}
    )
    if not files:
        raise FileNotFoundError(f"В папке {directory} нет CSV/XLSX файлов")

    frames = [_clean_frame(_read_table(path), path) for path in files]
    combined = pd.concat(frames, ignore_index=True)
    combined = combined.dropna(subset=["expiry_date"]).reset_index(drop=True)

    duplicates = int(combined.duplicated("batch_id").sum())
    conflicts = 0
    duplicated_rows = combined[combined.duplicated("batch_id", keep=False)]
    if not duplicated_rows.empty:
        for _, group in duplicated_rows.groupby("batch_id"):
            if group[COMPARE_COLUMNS].astype(str).drop_duplicates().shape[0] > 1:
                conflicts += 1
    combined = combined.drop_duplicates("batch_id", keep="first").reset_index(drop=True)
    combined = combined.sort_values("batch_id", kind="stable").reset_index(drop=True)

    combined.attrs["source_files"] = [path.name for path in files]
    combined.attrs["source_count"] = len(files)
    combined.attrs["duplicates_removed"] = duplicates
    combined.attrs["conflicts"] = conflicts
    combined.attrs["data_dir"] = str(directory)

    logger.info(
        "Загружено %s партий из %s файлов, удалено дубликатов: %s, конфликтов: %s",
        len(combined),
        len(files),
        duplicates,
        conflicts,
    )
    return combined


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Объединить CSV/XLSX из data/ в нормализованный batches.csv"
    )
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()

    frame = load_combined(args.data_dir)
    output = Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(output, index=False)
    print(f"Объединено {len(frame)} партий из {frame.attrs.get('source_count')} файлов")
    print(f"Файл: {output}")
    print(frame.groupby("scenario")["batch_id"].count().to_string())
    print(frame["risk_level"].value_counts().to_string())


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
