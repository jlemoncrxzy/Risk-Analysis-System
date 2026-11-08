"""Уведомления и события контроля качества.

Уведомления строятся из текущего набора партий и дополняются сообщениями,
которые пользователь отправляет команде из чата. Состояние прочтения хранится
в памяти процесса — для учебного демо этого достаточно.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from app.risk_engine import RiskEngine


class NotificationService:
    def __init__(self, engine: RiskEngine) -> None:
        self.engine = engine
        self.read_ids: set[str] = set()
        self.extra: list[dict[str, Any]] = []
        self.updated_at = datetime.now()

    # ------------------------------------------------------------------ data
    def _build(self) -> list[dict[str, Any]]:
        predictions = self.engine.all_predictions()
        high = sorted(
            [
                item
                for item in predictions
                if item["risk_level"] == "высокий"
            ],
            key=lambda item: (item.get("checked_at") or "", item["batch_id"]),
            reverse=True,
        )
        medium = sorted(
            [
                item
                for item in predictions
                if item["risk_level"] == "средний"
            ],
            key=lambda item: item["risk_score"],
            reverse=True,
        )
        now = datetime.now()
        items: list[dict[str, Any]] = []

        for index, item in enumerate(high[:3]):
            factors = ", ".join(
                factor["label"]
                for factor in sorted(item["factors"], key=lambda factor: -factor["weight"])[:2]
            )
            items.append(
                {
                    "id": f"high-{item['batch_id']}",
                    "level": "critical" if index == 0 else "warning",
                    "category": "Контроль качества",
                    "title": f"Высокий риск брака в партии {item['batch_id']}",
                    "text": (
                        f"Риск брака — {item['risk_score']}%, сценарий «{item['scenario']}». "
                        f"{factors}. Проверьте партию перед выпуском."
                    ),
                    "batch_id": item["batch_id"],
                    "created_at": (now - timedelta(minutes=5 + index * 7)).isoformat(
                        timespec="minutes"
                    ),
                }
            )

        if medium:
            item = medium[0]
            items.append(
                {
                    "id": f"medium-{item['batch_id']}",
                    "level": "warning",
                    "category": "Контроль качества",
                    "title": f"Партия {item['batch_id']} требует повторного контроля",
                    "text": (
                        f"Риск — {item['risk_score']}%, статус «{item['status_label']}». "
                        "Подтвердите результаты измерений и решение по партии."
                    ),
                    "batch_id": item["batch_id"],
                    "created_at": (now - timedelta(minutes=28)).isoformat(
                        timespec="minutes"
                    ),
                }
            )

        sources = self.engine.data_info
        items.append(
            {
                "id": "system-sync",
                "level": "info",
                "category": "Системное",
                "title": "Данные синхронизированы",
                "text": (
                    f"Загружено {sources.get('source_count', 0)} файлов CSV/XLSX, "
                    f"{len(self.engine.df)} партий. Удалено дубликатов: "
                    f"{sources.get('duplicates_removed', 0)}, конфликтов: "
                    f"{sources.get('conflicts', 0)}."
                ),
                "batch_id": None,
                "created_at": (now - timedelta(minutes=46)).isoformat(
                    timespec="minutes"
                ),
            }
        )

        model = self.engine.model_info
        items.append(
            {
                "id": "model-update",
                "level": "success" if model.get("available") else "info",
                "category": "Модель",
                "title": "Модель риска обновлена",
                "text": (
                    f"Гибридная модель: правила + {model.get('type', 'DecisionTree')}. "
                    f"Точность на отложенной выборке — "
                    f"{round((model.get('accuracy') or 0) * 100, 1)}%."
                ),
                "batch_id": None,
                "created_at": (now - timedelta(minutes=58)).isoformat(
                    timespec="minutes"
                ),
            }
        )

        items.extend(self.extra)
        items.sort(key=lambda item: item.get("created_at") or "", reverse=True)
        return items

    def list(self) -> dict[str, Any]:
        items = []
        for item in self._build():
            copy = dict(item)
            copy["read"] = item["id"] in self.read_ids
            items.append(copy)
        return {
            "items": items,
            "unread": sum(1 for item in items if not item["read"]),
            "updated_at": self.updated_at.isoformat(timespec="minutes"),
        }

    def mark_all_read(self) -> dict[str, Any]:
        self.read_ids.update(item["id"] for item in self._build())
        self.updated_at = datetime.now()
        return self.list()

    def mark_read(self, ids: list[str] | None = None) -> dict[str, Any]:
        if ids:
            self.read_ids.update(ids)
        else:
            self.read_ids.update(item["id"] for item in self._build())
        self.updated_at = datetime.now()
        return self.list()

    def add(self, batch_id: str | None, message: str | None = None) -> dict[str, Any]:
        now = datetime.now()
        item = {
            "id": f"team-{now.timestamp():.0f}",
            "level": "info",
            "category": "Команда",
            "title": (
                f"Команда уведомлена по партии {batch_id}"
                if batch_id
                else "Команда уведомлена"
            ),
            "text": message
            or (
                f"Отправлено уведомление по партии {batch_id}. "
                "Проверьте решение оператора."
                if batch_id
                else "Пользователь отправил уведомление команде."
            ),
            "batch_id": batch_id,
            "created_at": now.isoformat(timespec="minutes"),
        }
        self.extra.append(item)
        self.updated_at = now
        return item
