"""Движок оценки риска партий безалкогольного напитка.

Данные — пять учебных сценариев из папки ``data/``: идеальная партия, опасный
брак, риск поставщика, технический сбой и технологический брак.

Подход гибридный:
* система логических правил с жёсткими ограничениями;
* классификатор на дереве решений;
* прозрачное объяснение и перечень факторов для каждой оценки.

Правила приоритетны: партия с просрочкой, нарушением температурного режима,
опасным превышением тяжёлых металлов или несоответствием лаборатории никогда
не может получить низкий уровень риска.
"""

from __future__ import annotations

import copy
import os
import threading
import zlib
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time as dt_time
from pathlib import Path
from typing import Any

import pandas as pd

from app.data_loader import DEFAULT_DATA_DIR, load_combined

RISK_LEVELS: tuple[str, ...] = ("низкий", "средний", "высокий")
LEVEL_ORDER = {level: index for index, level in enumerate(RISK_LEVELS)}

ACTION_BY_LEVEL = {
    "низкий": "допустить к реализации",
    "средний": "направить на дополнительную проверку",
    "высокий": "временно изолировать партию",
}

STATUS_BY_LEVEL = {
    "низкий": "Одобрено",
    "средний": "Имеются дефекты",
    "высокий": "Брак",
}

OPERATORS = [
    {"name": "Анна Смирнова", "initials": "АС"},
    {"name": "Михаил Волков", "initials": "МВ"},
    {"name": "Елена Соколова", "initials": "ЕС"},
    {"name": "Дмитрий Козлов", "initials": "ДК"},
    {"name": "Ольга Морозова", "initials": "ОМ"},
]

SCENARIO_ORDER = [
    "Идеальная партия",
    "Риск поставщика",
    "Технический сбой",
    "Технологический брак",
    "Опасный брак",
]

# Учебные (упрощённые) нормы. Не применять для реальных решений о безопасности.
NORMS: dict[str, dict[str, Any]] = {
    "storage_temp_c": {
        "label": "Температура хранения",
        "min": 0.0,
        "max": 18.0,
        "unit": "°C",
        "norm_text": "0–18 °C",
    },
    "transport_temp_c": {
        "label": "Температура перевозки",
        "min": 0.0,
        "max": 18.0,
        "unit": "°C",
        "norm_text": "0–18 °C",
    },
    "dry_matter_pct": {
        "label": "Сухие вещества",
        "min": 9.5,
        "max": 10.5,
        "unit": "%",
        "norm_text": "9.5–10.5%",
    },
    "acidity_cm3": {
        "label": "Кислотность",
        "max": 2.5,
        "unit": "см³",
        "norm_text": "до 2.5 см³",
    },
    "co2_mass_pct": {
        "label": "CO₂",
        "min": 0.4,
        "max": 0.6,
        "unit": "%",
        "norm_text": "0.4–0.6%",
    },
    "lead_mg_kg": {
        "label": "Свинец",
        "max": 0.05,
        "warn": 0.01,
        "unit": "мг/кг",
        "norm_text": "≤ 0.05 мг/кг",
    },
    "cadmium_mg_kg": {
        "label": "Кадмий",
        "max": 0.01,
        "warn": 0.005,
        "unit": "мг/кг",
        "norm_text": "≤ 0.01 мг/кг",
    },
    "arsenic_mg_kg": {
        "label": "Мышьяк",
        "max": 0.01,
        "warn": 0.005,
        "unit": "мг/кг",
        "norm_text": "≤ 0.01 мг/кг",
    },
    "kmafanm_cfu_cm3": {
        "label": "КМАФАнМ",
        "max": 50.0,
        "critical": 150.0,
        "unit": "КОЕ/см³",
        "norm_text": "≤ 50 КОЕ/см³",
    },
    "bgkp_cfu_cm3": {
        "label": "БГКП",
        "max": 0.0,
        "unit": "КОЕ/см³",
        "norm_text": "не обнаружено",
    },
    "yeast_cfu_cm3": {
        "label": "Дрожжи",
        "max": 10.0,
        "unit": "КОЕ/см³",
        "norm_text": "≤ 10 КОЕ/см³",
    },
    "mold_cfu_cm3": {
        "label": "Плесень",
        "max": 6.0,
        "unit": "КОЕ/см³",
        "norm_text": "≤ 6 КОЕ/см³",
    },
}

MODEL_FEATURE_LABELS = {
    "storage_temp_c": "Температура хранения",
    "transport_temp_c": "Температура перевозки",
    "dry_matter_pct": "Сухие вещества",
    "acidity_cm3": "Кислотность",
    "co2_mass_pct": "CO₂",
    "lead_mg_kg": "Свинец",
    "cadmium_mg_kg": "Кадмий",
    "arsenic_mg_kg": "Мышьяк",
    "kmafanm_cfu_cm3": "КМАФАнМ",
    "bgkp_cfu_cm3": "БГКП",
    "yeast_cfu_cm3": "Дрожжи",
    "mold_cfu_cm3": "Плесень",
}
MODEL_FEATURES: tuple[str, ...] = tuple(MODEL_FEATURE_LABELS)

FEATURE_DEFAULTS = {
    "storage_temp_c": 9.0,
    "transport_temp_c": 9.0,
    "dry_matter_pct": 10.0,
    "acidity_cm3": 2.1,
    "co2_mass_pct": 0.5,
    "lead_mg_kg": 0.0,
    "cadmium_mg_kg": 0.0,
    "arsenic_mg_kg": 0.0,
    "kmafanm_cfu_cm3": 0.0,
    "bgkp_cfu_cm3": 0.0,
    "yeast_cfu_cm3": 0.0,
    "mold_cfu_cm3": 0.0,
}

DISCLAIMER = (
    "Учебный проект. Данные предоставлены для демонстрации и не могут "
    "использоваться для реальных решений о безопасности продукции без экспертизы специалистов."
)


def _as_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if pd.isna(number):
        return default
    return number


def _as_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        parsed = pd.to_datetime(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(parsed):
        return None
    return parsed.date()


def _iso_date(value: Any) -> str | None:
    parsed = _as_date(value)
    return parsed.isoformat() if parsed else None


def _operator_for(batch_id: str) -> dict[str, str]:
    if not batch_id:
        return {"name": "Система", "initials": "СИ"}
    index = zlib.crc32(batch_id.encode("utf-8")) % len(OPERATORS)
    return dict(OPERATORS[index])


def _checked_at_for(batch_id: str, production_date: Any, reference: date) -> str:
    batch_date = _as_date(production_date) or reference
    seed = zlib.crc32((batch_id or "manual").encode("utf-8"))
    hour = 8 + seed % 10
    minute = seed % 60
    return datetime.combine(batch_date, dt_time(hour, minute)).isoformat(timespec="minutes")


@dataclass(frozen=True)
class Factor:
    """Один фактор риска с весом и человекочитаемым пояснением."""

    code: str
    label: str
    severity: str  # info | warning | critical
    weight: int
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "label": self.label,
            "severity": self.severity,
            "weight": self.weight,
            "detail": self.detail,
        }


class RiskEngine:
    """Гибридный движок: логические правила + дерево решений."""

    def __init__(
        self,
        data_dir: str | Path | None = None,
        reference_date: date | None = None,
        data: pd.DataFrame | None = None,
    ) -> None:
        if data is not None:
            self.df = data.copy()
            self.data_dir = None
            self.data_info = {
                "source_files": list(data.attrs.get("source_files", [])),
                "source_count": int(data.attrs.get("source_count", 0)),
                "duplicates_removed": int(data.attrs.get("duplicates_removed", 0)),
                "conflicts": int(data.attrs.get("conflicts", 0)),
            }
        else:
            self.data_dir = Path(
                data_dir or os.getenv("DATA_DIR") or DEFAULT_DATA_DIR
            ).expanduser().resolve()
            self.df = load_combined(self.data_dir)
            self.data_info = {
                "source_files": list(self.df.attrs.get("source_files", [])),
                "source_count": int(self.df.attrs.get("source_count", 0)),
                "duplicates_removed": int(self.df.attrs.get("duplicates_removed", 0)),
                "conflicts": int(self.df.attrs.get("conflicts", 0)),
            }

        required = {
            "batch_id",
            "product_type",
            "supplier",
            "scenario",
            "production_date",
            "expiry_date",
            "storage_temp_c",
            "transport_temp_c",
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
            "risk_level",
            "recommended_action",
        }
        missing = required - set(self.df.columns)
        if missing:
            raise ValueError(f"В наборе данных отсутствуют колонки: {', '.join(sorted(missing))}")

        for column in (
            "storage_temp_c",
            "transport_temp_c",
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
        ):
            self.df[column] = pd.to_numeric(self.df[column], errors="coerce")

        self.df["production_date"] = pd.to_datetime(
            self.df["production_date"], errors="coerce"
        ).dt.date
        self.df["expiry_date"] = pd.to_datetime(
            self.df["expiry_date"], errors="coerce"
        ).dt.date
        self.df = self.df.dropna(subset=["expiry_date"]).reset_index(drop=True)
        self.df["risk_level"] = self.df["risk_level"].astype(str)
        self.df["recommended_action"] = self.df["recommended_action"].astype(str)
        self.df["scenario"] = self.df["scenario"].astype(str)

        self.reference_date = reference_date or self._resolve_reference_date()
        self.supplier_stats = self._compute_supplier_stats()
        self._model: Any = None
        self.model_info: dict[str, Any] = {"available": False, "type": "rules-only"}
        self._cache: list[dict[str, Any]] | None = None
        self._dashboard_cache: dict[str, Any] | None = None
        self._cache_lock = threading.Lock()
        self._train_model()

    # ------------------------------------------------------------------ data
    def _resolve_reference_date(self) -> date:
        """Опорная дата оценки.

        По умолчанию берётся максимальная дата выпуска из набора: синтетический
        датасет не «стареет», и демонстрационные примеры остаются стабильными.
        Для оценки на текущую дату задайте REFERENCE_DATE.
        """
        raw = os.getenv("REFERENCE_DATE")
        if raw:
            parsed = _as_date(raw)
            if parsed:
                return parsed

        production_dates = self.df["production_date"].dropna()
        if not production_dates.empty:
            return max(production_dates)
        return date.today()

    def _compute_supplier_stats(self) -> dict[str, dict[str, Any]]:
        """Сводка по поставщикам: количество партий и доля каждого уровня риска."""
        stats: dict[str, dict[str, Any]] = {}
        for supplier, group in self.df.groupby("supplier"):
            total = int(len(group))
            counts = group["risk_level"].value_counts()
            stats[str(supplier)] = {
                "total": total,
                "low": int(counts.get("низкий", 0)),
                "medium": int(counts.get("средний", 0)),
                "high": int(counts.get("высокий", 0)),
                "high_share": round(int(counts.get("высокий", 0)) / total, 4)
                if total
                else 0.0,
            }
        return stats

    def _features(self, row: dict[str, Any]) -> dict[str, float]:
        expiry = _as_date(row.get("expiry_date"))
        days = (expiry - self.reference_date).days if expiry else -999
        features = {
            name: _as_float(row.get(name), FEATURE_DEFAULTS.get(name, 0.0))
            for name in MODEL_FEATURES
            if name != "days_to_expiry"
        }
        features["days_to_expiry"] = float(days)
        return features

    # ----------------------------------------------------------------- model
    def _train_model(self) -> None:
        try:
            from sklearn.model_selection import train_test_split
            from sklearn.tree import DecisionTreeClassifier
        except Exception as exc:  # pragma: no cover - зависит от окружения
            self.model_info = {
                "available": False,
                "type": "rules-only",
                "reason": f"scikit-learn недоступен: {exc}",
            }
            return

        records = self.df.to_dict(orient="records")
        features = pd.DataFrame(
            [self._features(row) for row in records], columns=list(MODEL_FEATURES)
        )
        labels = self.df["risk_level"].astype(str)

        if labels.nunique() < 2:
            self.model_info = {
                "available": False,
                "type": "rules-only",
                "reason": "В наборе данных менее двух классов риска",
            }
            return

        try:
            x_train, x_test, y_train, y_test = train_test_split(
                features, labels, test_size=0.25, random_state=42, stratify=labels
            )
            probe = DecisionTreeClassifier(
                max_depth=6,
                min_samples_leaf=8,
                random_state=42,
                class_weight="balanced",
            )
            probe.fit(x_train, y_train)
            accuracy = float(probe.score(x_test, y_test))

            model = DecisionTreeClassifier(
                max_depth=6,
                min_samples_leaf=8,
                random_state=42,
                class_weight="balanced",
            )
            model.fit(features, labels)
        except Exception as exc:  # pragma: no cover - защитный сценарий
            self.model_info = {
                "available": False,
                "type": "rules-only",
                "reason": f"Не удалось обучить модель: {exc}",
            }
            return

        self._model = model
        importances = sorted(
            (
                {
                    "name": name,
                    "label": MODEL_FEATURE_LABELS.get(name, name),
                    "importance": round(float(value), 4),
                }
                for name, value in zip(MODEL_FEATURES, model.feature_importances_)
            ),
            key=lambda item: item["importance"],
            reverse=True,
        )
        self.model_info = {
            "available": True,
            "type": "DecisionTreeClassifier",
            "accuracy": round(accuracy, 3),
            "depth": int(model.get_depth()),
            "leaves": int(model.get_n_leaves()),
            "classes": [str(item) for item in model.classes_],
            "features": importances,
        }

    def _model_prediction(self, features: dict[str, float]) -> dict[str, Any] | None:
        if self._model is None:
            return None
        frame = pd.DataFrame([features], columns=list(MODEL_FEATURES))
        prediction = str(self._model.predict(frame)[0])
        classes = [str(item) for item in self._model.classes_]
        probabilities = self._model.predict_proba(frame)[0]
        confidence = float(probabilities[classes.index(prediction)])
        return {
            "available": True,
            "risk_level": prediction,
            "confidence": round(confidence, 3),
        }

    # ----------------------------------------------------------------- rules
    def _evaluate(
        self, row: dict[str, Any], features: dict[str, float]
    ) -> tuple[list[Factor], int, str]:
        factors: list[Factor] = []
        days = int(features["days_to_expiry"])
        storage = features["storage_temp_c"]
        transport = features["transport_temp_c"]
        dry_matter = features["dry_matter_pct"]
        acidity = features["acidity_cm3"]
        co2 = features["co2_mass_pct"]
        lead = features["lead_mg_kg"]
        cadmium = features["cadmium_mg_kg"]
        arsenic = features["arsenic_mg_kg"]
        kmafanm = features["kmafanm_cfu_cm3"]
        bgkp = features["bgkp_cfu_cm3"]
        yeast = features["yeast_cfu_cm3"]
        mold = features["mold_cfu_cm3"]

        # 1. Заключение лаборатории
        lab_conclusion = str(row.get("lab_conclusion") or "").strip()
        lab_text = lab_conclusion.lower().replace("ё", "е")
        if "опасно" in lab_text:
            factors.append(
                Factor(
                    "lab_danger",
                    "Опасная партия по заключению лаборатории",
                    "critical",
                    100,
                    f"лаборатория: {lab_conclusion}",
                )
            )
        elif "не соответствует" in lab_text or "брак" in lab_text:
            factors.append(
                Factor(
                    "lab_reject",
                    "Партия не соответствует требованиям",
                    "critical",
                    70,
                    f"лаборатория: {lab_conclusion}",
                )
            )
        elif "риск" in lab_text or "условно" in lab_text:
            factors.append(
                Factor(
                    "lab_supplier_risk",
                    "Условно годная партия: риск поставщика",
                    "warning",
                    15,
                    f"лаборатория: {lab_conclusion}",
                )
            )
        elif "проверк" in lab_text or "нестабиль" in lab_text:
            factors.append(
                Factor(
                    "lab_unstable",
                    "Нестабильная партия: требуется проверка",
                    "warning",
                    15,
                    f"лаборатория: {lab_conclusion}",
                )
            )

        # 2. Срок годности
        if days < 0:
            factors.append(
                Factor(
                    "expired",
                    "Истёкший срок годности",
                    "critical",
                    100,
                    f"срок годности истёк {abs(days)} дн. назад",
                )
            )
        elif days <= 2:
            factors.append(
                Factor(
                    "expiry_critical",
                    "Критически близкий срок годности",
                    "warning",
                    30,
                    f"до истечения срока годности осталось {days} дн.",
                )
            )
        elif days <= 5:
            factors.append(
                Factor(
                    "expiry_near",
                    "Близкий срок годности",
                    "info",
                    10,
                    f"до истечения срока годности осталось {days} дн.",
                )
            )

        # 3. Температурный режим (в данных диапазон 0–18 °C)
        storage_min = _as_float(row.get("storage_temp_min_c"), storage)
        storage_max = _as_float(row.get("storage_temp_max_c"), storage)
        if storage_max > 18 or storage_min < 0:
            range_text = str(row.get("storage_temp_range") or f"{storage:g} °C")
            factors.append(
                Factor(
                    "storage_temp",
                    "Нарушение температурного режима при хранении",
                    "critical",
                    80,
                    f"температура хранения {range_text} вне диапазона 0–18 °C",
                )
            )

        transport_min = _as_float(row.get("transport_temp_min_c"), transport)
        transport_max = _as_float(row.get("transport_temp_max_c"), transport)
        if transport_max > 18 or transport_min < 0:
            range_text = str(row.get("transport_temp_range") or f"{transport:g} °C")
            factors.append(
                Factor(
                    "transport_temp",
                    "Нарушение температурного режима при перевозке",
                    "critical",
                    80,
                    f"температура перевозки {range_text} вне диапазона 0–18 °C",
                )
            )

        # 4. Физико-химические показатели
        if dry_matter < 9.5 or dry_matter > 10.5:
            factors.append(
                Factor(
                    "dry_matter",
                    "Отклонение сухих веществ",
                    "warning",
                    8,
                    f"сухие вещества {dry_matter:g}% вне диапазона 9.5–10.5%",
                )
            )

        if acidity > 2.5:
            factors.append(
                Factor(
                    "acidity",
                    "Повышенная кислотность",
                    "warning",
                    30,
                    f"кислотность {acidity:g} см³ превышает норму 2.5 см³",
                )
            )

        if co2 < 0.4 or co2 > 0.6:
            weight = 25 if co2 < 0.2 or co2 > 0.8 else 20
            factors.append(
                Factor(
                    "co2",
                    "CO₂ вне рабочего диапазона",
                    "warning",
                    weight,
                    f"массовая доля CO₂ {co2:g}% вне диапазона 0.4–0.6%",
                )
            )

        # 5. Тяжёлые металлы
        if lead > 0.05:
            factors.append(
                Factor(
                    "lead",
                    "Превышение свинца",
                    "critical",
                    100,
                    f"свинец {lead:g} мг/кг превышает предел 0.05 мг/кг",
                )
            )
        elif lead > 0.01:
            factors.append(
                Factor(
                    "lead_warn",
                    "Свинец выше безопасного уровня",
                    "warning",
                    25,
                    f"свинец {lead:g} мг/кг выше 0.01 мг/кг",
                )
            )

        if cadmium > 0.01:
            factors.append(
                Factor(
                    "cadmium",
                    "Превышение кадмия",
                    "critical",
                    60,
                    f"кадмий {cadmium:g} мг/кг превышает предел 0.01 мг/кг",
                )
            )
        elif cadmium > 0.005:
            factors.append(
                Factor(
                    "cadmium_warn",
                    "Кадмий выше безопасного уровня",
                    "warning",
                    20,
                    f"кадмий {cadmium:g} мг/кг выше 0.005 мг/кг",
                )
            )

        if arsenic > 0.01:
            factors.append(
                Factor(
                    "arsenic",
                    "Превышение мышьяка",
                    "critical",
                    60,
                    f"мышьяк {arsenic:g} мг/кг превышает предел 0.01 мг/кг",
                )
            )
        elif arsenic > 0.005:
            factors.append(
                Factor(
                    "arsenic_warn",
                    "Мышьяк выше безопасного уровня",
                    "warning",
                    20,
                    f"мышьяк {arsenic:g} мг/кг выше 0.005 мг/кг",
                )
            )

        # 6. Микробиологические показатели
        if kmafanm > 150:
            factors.append(
                Factor(
                    "kmafanm_high",
                    "Критическое превышение КМАФАнМ",
                    "critical",
                    70,
                    f"КМАФАнМ {kmafanm:g} КОЕ/см³ превышает 150 КОЕ/см³",
                )
            )
        elif kmafanm > 50:
            factors.append(
                Factor(
                    "kmafanm_warn",
                    "Повышенное КМАФАнМ",
                    "warning",
                    10,
                    f"КМАФАнМ {kmafanm:g} КОЕ/см³ превышает 50 КОЕ/см³",
                )
            )

        if bgkp > 0:
            factors.append(
                Factor(
                    "bgkp",
                    "Обнаружены БГКП",
                    "critical",
                    70,
                    f"БГКП {bgkp:g} КОЕ/см³ — не допускается",
                )
            )

        if yeast > 10:
            weight = 20 if yeast > 20 else 15
            factors.append(
                Factor(
                    "yeast",
                    "Повышенное содержание дрожжей",
                    "warning",
                    weight,
                    f"дрожжи {yeast:g} КОЕ/см³ превышают 10 КОЕ/см³",
                )
            )

        if mold > 6:
            weight = 20 if mold > 10 else 15
            factors.append(
                Factor(
                    "mold",
                    "Повышенное содержание плесени",
                    "warning",
                    weight,
                    f"плесень {mold:g} КОЕ/см³ превышает 6 КОЕ/см³",
                )
            )

        score = min(100, sum(factor.weight for factor in factors))
        has_critical = any(factor.severity == "critical" for factor in factors)
        if has_critical or score >= 70:
            risk_level = "высокий"
        elif score >= 15:
            risk_level = "средний"
        else:
            risk_level = "низкий"

        return factors, score, risk_level

    # ----------------------------------------------------------- explanations
    @staticmethod
    def _build_explanation(risk_level: str, factors: list[Factor]) -> str:
        if not factors:
            return (
                "Уровень риска «низкий»: лаборатория подтвердила соответствие, "
                "физико-химические и микробиологические показатели в пределах нормы, "
                "температурный режим и срок годности не нарушены."
            )

        ordered = sorted(factors, key=lambda item: (-item.weight, item.label))
        details = "; ".join(factor.detail for factor in ordered[:4])
        if risk_level == "низкий":
            return (
                "Уровень риска «низкий». Замечания не препятствуют допуску: " + details + "."
            )
        return (
            f"Уровень риска «{risk_level}» из-за следующих факторов: {details}. "
            f"Рекомендованное действие — {ACTION_BY_LEVEL[risk_level]}."
        )

    def _parameter_rows(
        self, row: dict[str, Any], features: dict[str, float]
    ) -> list[dict[str, Any]]:
        days = int(features["days_to_expiry"])
        storage = features["storage_temp_c"]
        transport = features["transport_temp_c"]
        dry_matter = features["dry_matter_pct"]
        acidity = features["acidity_cm3"]
        co2 = features["co2_mass_pct"]
        lead = features["lead_mg_kg"]
        cadmium = features["cadmium_mg_kg"]
        arsenic = features["arsenic_mg_kg"]
        kmafanm = features["kmafanm_cfu_cm3"]
        bgkp = features["bgkp_cfu_cm3"]
        yeast = features["yeast_cfu_cm3"]
        mold = features["mold_cfu_cm3"]

        storage_min = _as_float(row.get("storage_temp_min_c"), storage)
        storage_max = _as_float(row.get("storage_temp_max_c"), storage)
        transport_min = _as_float(row.get("transport_temp_min_c"), transport)
        transport_max = _as_float(row.get("transport_temp_max_c"), transport)

        storage_status = (
            "critical" if storage_min < 0 or storage_max > 18 else "ok"
        )
        transport_status = (
            "critical" if transport_min < 0 or transport_max > 18 else "ok"
        )
        storage_value = str(
            row.get("storage_temp_range") or f"{storage:g} °C"
        )
        transport_value = str(
            row.get("transport_temp_range") or f"{transport:g} °C"
        )

        if days < 0:
            days_status = "critical"
        elif days <= 2:
            days_status = "warning"
        elif days <= 5:
            days_status = "info"
        else:
            days_status = "ok"

        dry_status = "warning" if dry_matter < 9.5 or dry_matter > 10.5 else "ok"
        acidity_status = "warning" if acidity > 2.5 else "ok"
        co2_status = "warning" if co2 < 0.4 or co2 > 0.6 else "ok"

        if lead > 0.05:
            lead_status = "critical"
        elif lead > 0.01:
            lead_status = "warning"
        else:
            lead_status = "ok"

        if cadmium > 0.01:
            cadmium_status = "critical"
        elif cadmium > 0.005:
            cadmium_status = "warning"
        else:
            cadmium_status = "ok"

        if arsenic > 0.01:
            arsenic_status = "critical"
        elif arsenic > 0.005:
            arsenic_status = "warning"
        else:
            arsenic_status = "ok"

        if kmafanm > 150:
            kmafanm_status = "critical"
        elif kmafanm > 50:
            kmafanm_status = "warning"
        else:
            kmafanm_status = "ok"

        bgkp_status = "critical" if bgkp > 0 else "ok"
        yeast_status = "warning" if yeast > 10 else "ok"
        mold_status = "warning" if mold > 6 else "ok"

        return [
            {
                "key": "storage_temp_c",
                "label": "Температура хранения",
                "value": storage_value,
                "unit": "",
                "norm": NORMS["storage_temp_c"]["norm_text"],
                "status": storage_status,
            },
            {
                "key": "transport_temp_c",
                "label": "Температура перевозки",
                "value": transport_value,
                "unit": "",
                "norm": NORMS["transport_temp_c"]["norm_text"],
                "status": transport_status,
            },
            {
                "key": "days_to_expiry",
                "label": "Дней до истечения срока",
                "value": days,
                "unit": "дн.",
                "norm": "не менее 3",
                "status": days_status,
            },
            {
                "key": "dry_matter_pct",
                "label": "Сухие вещества",
                "value": round(dry_matter, 1),
                "unit": "%",
                "norm": NORMS["dry_matter_pct"]["norm_text"],
                "status": dry_status,
            },
            {
                "key": "acidity_cm3",
                "label": "Кислотность",
                "value": round(acidity, 1),
                "unit": "см³",
                "norm": NORMS["acidity_cm3"]["norm_text"],
                "status": acidity_status,
            },
            {
                "key": "co2_mass_pct",
                "label": "CO₂",
                "value": round(co2, 2),
                "unit": "%",
                "norm": NORMS["co2_mass_pct"]["norm_text"],
                "status": co2_status,
            },
            {
                "key": "lead_mg_kg",
                "label": "Свинец",
                "value": round(lead, 4),
                "unit": "мг/кг",
                "norm": NORMS["lead_mg_kg"]["norm_text"],
                "status": lead_status,
            },
            {
                "key": "cadmium_mg_kg",
                "label": "Кадмий",
                "value": round(cadmium, 4),
                "unit": "мг/кг",
                "norm": NORMS["cadmium_mg_kg"]["norm_text"],
                "status": cadmium_status,
            },
            {
                "key": "arsenic_mg_kg",
                "label": "Мышьяк",
                "value": round(arsenic, 4),
                "unit": "мг/кг",
                "norm": NORMS["arsenic_mg_kg"]["norm_text"],
                "status": arsenic_status,
            },
            {
                "key": "kmafanm_cfu_cm3",
                "label": "КМАФАнМ",
                "value": round(kmafanm, 0),
                "unit": "КОЕ/см³",
                "norm": NORMS["kmafanm_cfu_cm3"]["norm_text"],
                "status": kmafanm_status,
            },
            {
                "key": "bgkp_cfu_cm3",
                "label": "БГКП",
                "value": round(bgkp, 0),
                "unit": "КОЕ/см³",
                "norm": NORMS["bgkp_cfu_cm3"]["norm_text"],
                "status": bgkp_status,
            },
            {
                "key": "yeast_cfu_cm3",
                "label": "Дрожжи",
                "value": round(yeast, 0),
                "unit": "КОЕ/см³",
                "norm": NORMS["yeast_cfu_cm3"]["norm_text"],
                "status": yeast_status,
            },
            {
                "key": "mold_cfu_cm3",
                "label": "Плесень",
                "value": round(mold, 0),
                "unit": "КОЕ/см³",
                "norm": NORMS["mold_cfu_cm3"]["norm_text"],
                "status": mold_status,
            },
        ]

    # --------------------------------------------------------------- public
    def predict(self, batch: dict[str, Any]) -> dict[str, Any]:
        """Оценка риска одной партии (dict). Возвращает полную карточку оценки."""
        features = self._features(batch)
        factors, score, risk_level = self._evaluate(batch, features)

        model = self._model_prediction(features)
        if model is not None:
            model["agrees"] = model["risk_level"] == risk_level

        hard_violations = [factor.label for factor in factors if factor.severity == "critical"]
        scenario = str(batch.get("scenario") or "Ручной ввод")
        batch_id = str(batch.get("batch_id") or "")
        if batch_id.startswith("WHAT-IF"):
            operator = {"name": "Сценарный расчёт", "initials": "СЦ"}
        elif batch_id.startswith("MANUAL"):
            operator = {"name": "Ручная проверка", "initials": "РП"}
        else:
            operator = _operator_for(batch_id)
        checked_at = _checked_at_for(
            batch_id, batch.get("production_date"), self.reference_date
        )
        if not batch.get("production_date") and (
            batch_id.startswith("MANUAL") or batch_id.startswith("WHAT-IF")
        ):
            checked_at = datetime.now().isoformat(timespec="minutes")

        return {
            "batch_id": batch_id,
            "operator": operator["name"],
            "operator_initials": operator["initials"],
            "status_label": STATUS_BY_LEVEL[risk_level],
            "checked_at": checked_at,
            "product_type": str(batch.get("product_type") or "безалкогольный напиток"),
            "supplier": str(batch.get("supplier") or "не указан"),
            "scenario": scenario,
            "scenario_code": str(batch.get("scenario_code") or ""),
            "scenario_description": str(
                batch.get("scenario_description") or batch.get("scenario") or "Ручная оценка"
            ),
            "production_date": _iso_date(batch.get("production_date")),
            "expiry_date": _iso_date(batch.get("expiry_date")),
            "days_to_expiry": int(features["days_to_expiry"]),
            "storage_temp_c": features["storage_temp_c"],
            "storage_temp_range": str(
                batch.get("storage_temp_range") or f"{features['storage_temp_c']:g} °C"
            ),
            "transport_temp_c": features["transport_temp_c"],
            "transport_temp_range": str(
                batch.get("transport_temp_range") or f"{features['transport_temp_c']:g} °C"
            ),
            "dry_matter_pct": features["dry_matter_pct"],
            "acidity_cm3": features["acidity_cm3"],
            "co2_mass_pct": features["co2_mass_pct"],
            "lead_mg_kg": features["lead_mg_kg"],
            "cadmium_mg_kg": features["cadmium_mg_kg"],
            "arsenic_mg_kg": features["arsenic_mg_kg"],
            "kmafanm_cfu_cm3": features["kmafanm_cfu_cm3"],
            "bgkp_cfu_cm3": features["bgkp_cfu_cm3"],
            "yeast_cfu_cm3": features["yeast_cfu_cm3"],
            "mold_cfu_cm3": features["mold_cfu_cm3"],
            "lab_conclusion": str(batch.get("lab_conclusion") or "не указано"),
            "risk_reason": str(
                batch.get("risk_reason") or batch.get("scenario_description") or ""
            ),
            "risk_score": score,
            "risk_level": risk_level,
            "recommended_action": ACTION_BY_LEVEL[risk_level],
            "factors": [factor.as_dict() for factor in factors],
            "parameters": self._parameter_rows(batch, features),
            "hard_violations": hard_violations,
            "explanation": self._build_explanation(risk_level, factors),
            "model": model,
            "reference_date": self.reference_date.isoformat(),
        }

    def all_predictions(self) -> list[dict[str, Any]]:
        """Оценки для всего набора; кэшируются после первого вызова."""
        if self._cache is None:
            with self._cache_lock:
                if self._cache is None:
                    self._cache = [
                        self.predict(row) for row in self.df.to_dict(orient="records")
                    ]
        return self._cache

    def get_batch(self, batch_id: str) -> dict[str, Any] | None:
        for prediction in self.all_predictions():
            if prediction["batch_id"] == batch_id:
                return copy.deepcopy(prediction)
        return None

    def list_batches(
        self,
        limit: int = 10,
        offset: int = 0,
        risk_level: str | None = None,
        search: str | None = None,
        supplier: str | None = None,
        scenario: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        sort: str = "batch",
    ) -> dict[str, Any]:
        items = list(self.all_predictions())

        if risk_level:
            items = [item for item in items if item["risk_level"] == risk_level]
        if supplier:
            items = [item for item in items if item["supplier"] == supplier]
        if scenario:
            items = [item for item in items if item["scenario"] == scenario]
        if date_from:
            items = [
                item
                for item in items
                if item["production_date"] and item["production_date"] >= date_from
            ]
        if date_to:
            items = [
                item
                for item in items
                if item["production_date"] and item["production_date"] <= date_to
            ]
        if search:
            query = search.strip().lower()
            if query:
                items = [
                    item
                    for item in items
                    if query in item["batch_id"].lower()
                    or query in item["product_type"].lower()
                    or query in item["supplier"].lower()
                    or query in item["scenario"].lower()
                    or query in item["lab_conclusion"].lower()
                    or query in item["operator"].lower()
                ]

        if sort == "risk":
            items.sort(key=lambda item: (-LEVEL_ORDER[item["risk_level"]], -item["risk_score"]))
        elif sort == "score":
            items.sort(key=lambda item: (-item["risk_score"], item["batch_id"]))
        elif sort == "expiry":
            items.sort(key=lambda item: (item["days_to_expiry"], item["batch_id"]))
        elif sort == "date":
            items.sort(
                key=lambda item: (item.get("checked_at") or "", item["batch_id"]),
                reverse=True,
            )
        else:
            items.sort(key=lambda item: item["batch_id"])

        total = len(items)
        page = [copy.deepcopy(item) for item in items[offset : offset + limit]]
        return {"items": page, "total": total, "limit": limit, "offset": offset}

    def get_examples(self, low: int = 2, medium: int = 2, high: int = 2) -> list[dict[str, Any]]:
        """Не менее 5 примеров, включая каждую категорию риска и разные сценарии."""
        predictions = self.all_predictions()
        targets = {
            "высокий": max(1, high),
            "средний": max(1, medium),
            "низкий": max(1, low),
        }
        priority = {
            "высокий": ["Опасный брак", "Технологический брак"],
            "средний": ["Риск поставщика", "Технический сбой"],
            "низкий": ["Идеальная партия"],
        }
        picked: list[dict[str, Any]] = []
        seen: set[str] = set()

        for level, needed in targets.items():
            candidates = [item for item in predictions if item["risk_level"] == level]
            ordered: list[dict[str, Any]] = []
            added: set[str] = set()

            scenario_order = priority.get(level, [])
            groups = [
                sorted(
                    [item for item in candidates if item["scenario"] == scenario],
                    key=lambda item: item["batch_id"],
                )
                for scenario in scenario_order
            ]
            if groups:
                # Чередуем сценарии, чтобы среди примеров были разные причины риска.
                for index in range(max(len(group) for group in groups)):
                    for group in groups:
                        if index >= len(group):
                            continue
                        item = group[index]
                        if item["batch_id"] in added:
                            continue
                        ordered.append(item)
                        added.add(item["batch_id"])
            for item in sorted(candidates, key=lambda item: item["batch_id"]):
                if item["batch_id"] not in added:
                    ordered.append(item)
                    added.add(item["batch_id"])

            chosen: list[dict[str, Any]] = []
            chosen_ids: set[str] = set()
            used_suppliers: set[str] = set()

            # Для низкого риска стараемся показать разных поставщиков.
            for candidate in ordered:
                if len(chosen) >= needed:
                    break
                if (
                    candidate["batch_id"] in seen
                    or candidate["batch_id"] in chosen_ids
                ):
                    continue
                if level == "низкий" and candidate["supplier"] in used_suppliers:
                    continue
                chosen.append(candidate)
                chosen_ids.add(candidate["batch_id"])
                used_suppliers.add(candidate["supplier"])

            for candidate in ordered:
                if len(chosen) >= needed:
                    break
                if candidate["batch_id"] in seen or candidate["batch_id"] in chosen_ids:
                    continue
                chosen.append(candidate)
                chosen_ids.add(candidate["batch_id"])

            for candidate in chosen:
                picked.append(copy.deepcopy(candidate))
                seen.add(candidate["batch_id"])

        # Гарантируем минимум пять демонстрационных примеров.
        if len(picked) < 5:
            for candidate in predictions:
                if len(picked) >= 5:
                    break
                if candidate["batch_id"] in seen:
                    continue
                picked.append(copy.deepcopy(candidate))
                seen.add(candidate["batch_id"])

        return picked

    def supplier_summary(self) -> list[dict[str, Any]]:
        """Сводка по поставщикам для таблиц и графиков чат-бота."""
        predictions = self.all_predictions()
        groups: dict[str, dict[str, Any]] = {}
        for item in predictions:
            group = groups.setdefault(
                item["supplier"],
                {
                    "supplier": item["supplier"],
                    "total": 0,
                    "low": 0,
                    "medium": 0,
                    "high": 0,
                    "score_sum": 0,
                },
            )
            group["total"] += 1
            group["score_sum"] += item["risk_score"]
            if item["risk_level"] == "низкий":
                group["low"] += 1
            elif item["risk_level"] == "средний":
                group["medium"] += 1
            else:
                group["high"] += 1

        rows = []
        for group in groups.values():
            total = group["total"]
            rows.append(
                {
                    "supplier": group["supplier"],
                    "total": total,
                    "low": group["low"],
                    "medium": group["medium"],
                    "high": group["high"],
                    "high_share": round(group["high"] / total, 4) if total else 0.0,
                    "average_score": round(group["score_sum"] / total, 1)
                    if total
                    else 0.0,
                }
            )
        rows.sort(key=lambda item: (-item["high_share"], -item["total"]))
        return rows

    def scenario_summary(self) -> list[dict[str, Any]]:
        """Сводка по пяти сценариям партий."""
        predictions = self.all_predictions()
        groups: dict[str, dict[str, Any]] = {}
        for item in predictions:
            group = groups.setdefault(
                item["scenario"],
                {
                    "scenario": item["scenario"],
                    "risk_level": item["risk_level"],
                    "description": item["scenario_description"],
                    "total": 0,
                    "score_sum": 0,
                    "example_batch": item["batch_id"],
                    "low": 0,
                    "medium": 0,
                    "high": 0,
                },
            )
            group["total"] += 1
            group["score_sum"] += item["risk_score"]
            if item["risk_level"] == "низкий":
                group["low"] += 1
            elif item["risk_level"] == "средний":
                group["medium"] += 1
            else:
                group["high"] += 1

        rows = []
        for group in groups.values():
            total = group["total"]
            rows.append(
                {
                    "scenario": group["scenario"],
                    "risk_level": group["risk_level"],
                    "description": group["description"],
                    "total": total,
                    "low": group["low"],
                    "medium": group["medium"],
                    "high": group["high"],
                    "average_score": round(group["score_sum"] / total, 1)
                    if total
                    else 0.0,
                    "example_batch": group["example_batch"],
                }
            )
        order = {name: index for index, name in enumerate(SCENARIO_ORDER)}
        rows.sort(key=lambda item: (order.get(item["scenario"], 99), item["scenario"]))
        return rows

    def metric_summary(self, column: str) -> list[dict[str, Any]]:
        """Минимум, среднее и максимум показателя в разрезе сценариев."""
        if column not in MODEL_FEATURES:
            raise ValueError(f"Неизвестный показатель: {column}")
        predictions = self.all_predictions()
        groups: dict[str, list[float]] = {}
        for item in predictions:
            value = item.get(column)
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            if pd.isna(number):
                continue
            groups.setdefault(item["scenario"], []).append(number)

        order = {name: index for index, name in enumerate(SCENARIO_ORDER)}
        rows = []
        for scenario, values in groups.items():
            rows.append(
                {
                    "scenario": scenario,
                    "count": len(values),
                    "min": min(values),
                    "average": round(sum(values) / len(values), 4),
                    "max": max(values),
                }
            )
        rows.sort(key=lambda item: (order.get(item["scenario"], 99), item["scenario"]))
        return rows

    def top_batches(
        self,
        column: str | None = None,
        limit: int = 5,
        ascending: bool = False,
        risk_level: str | None = None,
    ) -> list[dict[str, Any]]:
        """Топ партий по показателю или оценке риска."""
        items = list(self.all_predictions())
        if risk_level:
            items = [item for item in items if item["risk_level"] == risk_level]
        key_name = column or "risk_score"
        if column and column not in MODEL_FEATURES:
            raise ValueError(f"Неизвестный показатель: {column}")
        items.sort(key=lambda item: float(item.get(key_name) or 0), reverse=not ascending)
        return [copy.deepcopy(item) for item in items[: max(1, limit)]]

    def stats(self) -> dict[str, Any]:
        predictions = self.all_predictions()
        counts = Counter(item["risk_level"] for item in predictions)
        actions = Counter(item["recommended_action"] for item in predictions)
        scenarios = {row["scenario"]: row for row in self.scenario_summary()}
        total = len(predictions)
        return {
            "total": total,
            "reference_date": self.reference_date.isoformat(),
            "risk_counts": {
                level: {
                    "count": counts.get(level, 0),
                    "share": round(counts.get(level, 0) / total, 4) if total else 0.0,
                }
                for level in RISK_LEVELS
            },
            "average_score": round(
                sum(item["risk_score"] for item in predictions) / total, 1
            )
            if total
            else 0.0,
            "hard_violations": sum(1 for item in predictions if item["hard_violations"]),
            "actions": dict(actions),
            "scenario_counts": {
                name: {
                    "count": row["total"],
                    "share": round(row["total"] / total, 4) if total else 0.0,
                    "risk_level": row["risk_level"],
                    "average_score": row["average_score"],
                }
                for name, row in scenarios.items()
            },
            "suppliers_count": int(self.df["supplier"].nunique()),
            "model": self.model_info,
        }

    def dashboard(self) -> dict[str, Any]:
        """Сводка для панели администратора."""
        if self._dashboard_cache is not None:
            return self._dashboard_cache
        predictions = self.all_predictions()
        total = len(predictions)
        counts = Counter(item["risk_level"] for item in predictions)
        high_predictions = [
            item for item in predictions if item["risk_level"] == "высокий"
        ]
        average_score = (
            round(sum(item["risk_score"] for item in predictions) / total, 1)
            if total
            else 0.0
        )
        defect_rate = round(len(high_predictions) / total, 4) if total else 0.0

        statuses = [
            {
                "key": "low",
                "label": "Одобрено",
                "count": counts.get("низкий", 0),
                "share": round(counts.get("низкий", 0) / total, 4) if total else 0.0,
                "action": ACTION_BY_LEVEL["низкий"],
            },
            {
                "key": "medium",
                "label": "Имеются дефекты",
                "count": counts.get("средний", 0),
                "share": round(counts.get("средний", 0) / total, 4) if total else 0.0,
                "action": ACTION_BY_LEVEL["средний"],
            },
            {
                "key": "high",
                "label": "Брак",
                "count": counts.get("высокий", 0),
                "share": round(counts.get("высокий", 0) / total, 4) if total else 0.0,
                "action": ACTION_BY_LEVEL["высокий"],
            },
        ]

        daily_groups: dict[str, dict[str, Any]] = {}
        for item in predictions:
            day = item.get("production_date")
            if not day:
                continue
            group = daily_groups.setdefault(
                day, {"date": day, "total": 0, "high": 0, "score_sum": 0}
            )
            group["total"] += 1
            group["score_sum"] += item["risk_score"]
            if item["risk_level"] == "высокий":
                group["high"] += 1
        daily = []
        for day in sorted(daily_groups):
            group = daily_groups[day]
            day_total = group["total"]
            daily.append(
                {
                    "date": day,
                    "label": f"{day[8:10]}.{day[5:7]}",
                    "total": day_total,
                    "high": group["high"],
                    "defect_rate": round(group["high"] / day_total, 4)
                    if day_total
                    else 0.0,
                    "average_score": round(group["score_sum"] / day_total, 1)
                    if day_total
                    else 0.0,
                }
            )

        factor_counter: Counter[str] = Counter()
        for item in high_predictions:
            for factor in item["factors"]:
                factor_counter[factor["label"]] += 1
        top_reasons = [
            {
                "label": label,
                "count": count,
                "share": round(count / len(high_predictions), 4)
                if high_predictions
                else 0.0,
            }
            for label, count in factor_counter.most_common(4)
        ]

        latest_high = None
        if high_predictions:
            ordered = sorted(
                high_predictions,
                key=lambda item: (item.get("checked_at") or "", item["risk_score"]),
                reverse=True,
            )
            item = ordered[0]
            latest_high = {
                "batch_id": item["batch_id"],
                "scenario": item["scenario"],
                "supplier": item["supplier"],
                "operator": item["operator"],
                "checked_at": item["checked_at"],
                "risk_level": item["risk_level"],
                "risk_score": item["risk_score"],
                "status_label": item["status_label"],
                "top_factors": [
                    factor["label"]
                    for factor in sorted(
                        item["factors"], key=lambda factor: -factor["weight"]
                    )[:3]
                ],
                "confidence": (item.get("model") or {}).get("confidence"),
            }

        dates = [
            item["production_date"]
            for item in predictions
            if item.get("production_date")
        ]
        result = {
            "total": total,
            "defect_rate": defect_rate,
            "average_score": average_score,
            "statuses": statuses,
            "daily": daily,
            "top_reasons": top_reasons,
            "latest_high": latest_high,
            "period": {
                "from": min(dates) if dates else None,
                "to": max(dates) if dates else None,
            },
            "model": self.model_info,
            "reference_date": self.reference_date.isoformat(),
        }
        self._dashboard_cache = result
        return result

    def meta(self) -> dict[str, Any]:
        scenarios = self.scenario_summary()
        return {
            "risk_levels": list(RISK_LEVELS),
            "actions": ACTION_BY_LEVEL,
            "suppliers": sorted(self.df["supplier"].dropna().astype(str).unique().tolist()),
            "product_types": sorted(
                self.df["product_type"].dropna().astype(str).unique().tolist()
            ),
            "scenarios": [
                {
                    "title": row["scenario"],
                    "risk_level": row["risk_level"],
                    "description": row["description"],
                    "count": row["total"],
                    "example_batch": row["example_batch"],
                }
                for row in scenarios
            ],
            "lab_conclusions": sorted(
                self.df["lab_conclusion"].dropna().astype(str).unique().tolist()
            ),
            "norms": {
                key: {
                    "label": value["label"],
                    "norm_text": value["norm_text"],
                    "unit": value["unit"],
                    "min": value.get("min"),
                    "max": value.get("max"),
                    "warn": value.get("warn"),
                    "critical": value.get("critical"),
                }
                for key, value in NORMS.items()
            },
            "model": self.model_info,
            "reference_date": self.reference_date.isoformat(),
            "dataset_size": int(len(self.df)),
            "sources": self.data_info,
            "disclaimer": DISCLAIMER,
        }