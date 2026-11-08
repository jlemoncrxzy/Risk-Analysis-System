"""Лёгкий RAG-слой над данными о партиях.

Индекс строится локально из тех же данных, которые использует RiskEngine:
партии, сценарии, поставщики, нормы и методика оценки. Поиск выполняется
TF-IDF по символьным n-граммам, поэтому работает без внешних embeddings и
устойчив к русским словоформам. Найденные фрагменты передаются LLM как
единственный источник фактов для ответа.
"""

from __future__ import annotations

import logging
import re
import threading
from typing import Any

from app.risk_engine import ACTION_BY_LEVEL, MODEL_FEATURES, NORMS, RiskEngine

logger = logging.getLogger(__name__)

BATCH_ID_RE = re.compile(r"\b(?:AP|RE|SR|MF|DE)-\d{4}-\d{3}\b", re.IGNORECASE)


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").lower().replace("ё", "е")).strip()


class RagService:
    """Готовит релевантные фрагменты данных для RAG-ответа агента."""

    def __init__(self, engine: RiskEngine) -> None:
        self.engine = engine
        self._documents: list[dict[str, Any]] = []
        self._matrix: Any = None
        self._vectorizer: Any = None
        self._lock = threading.Lock()
        self._ready = False

    # ------------------------------------------------------------------ index
    def _build_documents(self) -> list[dict[str, Any]]:
        docs: list[dict[str, Any]] = []

        for item in self.engine.all_predictions():
            factors = "; ".join(
                f"{factor['label']}: {factor['detail']}"
                for factor in sorted(
                    item.get("factors") or [],
                    key=lambda factor: -int(factor.get("weight") or 0),
                )[:3]
            )
            text = (
                f"Партия {item['batch_id']}. Сценарий: {item['scenario']}. "
                f"Поставщик: {item['supplier']}. Уровень риска: {item['risk_level']}, "
                f"оценка {item['risk_score']}/100. Статус: {item['status_label']}. "
                f"Лаборатория: {item['lab_conclusion']}. Факторы: {factors or 'нет'}. "
                f"Действие: {item['recommended_action']}."
            )
            docs.append(
                {
                    "kind": "batch",
                    "id": item["batch_id"],
                    "text": text,
                    "payload": item,
                }
            )

        for scenario in self.engine.scenario_summary():
            docs.append(
                {
                    "kind": "scenario",
                    "id": scenario["scenario"],
                    "text": (
                        f"Сценарий {scenario['scenario']}. {scenario['description']} "
                        f"Базовый уровень риска: {scenario['risk_level']}. "
                        f"Партий: {scenario['total']}, высокий риск: {scenario['high']}, "
                        f"средний: {scenario['medium']}, низкий: {scenario['low']}. "
                        f"Средняя оценка: {scenario['average_score']}/100. "
                        f"Пример партии: {scenario['example_batch']}."
                    ),
                    "payload": scenario,
                }
            )

        for supplier in self.engine.supplier_summary():
            docs.append(
                {
                    "kind": "supplier",
                    "id": supplier["supplier"],
                    "text": (
                        f"Поставщик {supplier['supplier']}. Партий: {supplier['total']}. "
                        f"Высокий риск: {supplier['high']} "
                        f"({round(supplier['high_share'] * 100)}%), средний: "
                        f"{supplier['medium']}, низкий: {supplier['low']}. "
                        f"Средняя оценка риска: {supplier['average_score']}/100."
                    ),
                    "payload": supplier,
                }
            )

        for day in self.engine.dashboard().get("daily", []):
            docs.append(
                {
                    "kind": "daily",
                    "id": day["date"],
                    "text": (
                        f"Дата выпуска {day['date']}: проверено {day['total']} партий, "
                        f"брак {day['high']} партий, доля брака "
                        f"{round(day['defect_rate'] * 100, 1)}%, средний риск "
                        f"{day['average_score']}/100."
                    ),
                    "payload": day,
                }
            )

        norms_text = "; ".join(
            f"{norm['label']}: {norm['norm_text']}" for norm in NORMS.values()
        )
        docs.append(
            {
                "kind": "norms",
                "id": "norms",
                "text": (
                    "Нормы и критерии качества напитка. " + norms_text + ". "
                    f"Действия по уровням риска: низкий — {ACTION_BY_LEVEL['низкий']}; "
                    f"средний — {ACTION_BY_LEVEL['средний']}; "
                    f"высокий — {ACTION_BY_LEVEL['высокий']}."
                ),
                "payload": {"norms": NORMS, "actions": ACTION_BY_LEVEL},
            }
        )
        docs.append(
            {
                "kind": "method",
                "id": "method",
                "text": (
                    "Методика оценки риска. Сначала применяются жёсткие правила: "
                    "превышение тяжёлых металлов, бактерии БГКП, истёкший срок, "
                    "температурный режим и опасное заключение лаборатории. Затем "
                    "гибридная модель на основе DecisionTreeClassifier учитывает "
                    f"показатели: {', '.join(MODEL_FEATURES)}. Итог — оценка 0–100, "
                    "уровень низкий/средний/высокий, факторы и рекомендация."
                ),
                "payload": {"features": list(MODEL_FEATURES)},
            }
        )
        return docs

    def ensure_index(self) -> bool:
        if self._ready:
            return True
        with self._lock:
            if self._ready:
                return True
            try:
                from sklearn.feature_extraction.text import TfidfVectorizer

                documents = self._build_documents()
                vectorizer = TfidfVectorizer(
                    analyzer="char_wb",
                    ngram_range=(3, 5),
                    min_df=2,
                    max_features=120_000,
                    sublinear_tf=True,
                )
                matrix = vectorizer.fit_transform([doc["text"] for doc in documents])
                self._documents = documents
                self._vectorizer = vectorizer
                self._matrix = matrix
                self._ready = True
                logger.info("RAG-индекс готов: %s фрагментов", len(documents))
                return True
            except Exception as exc:  # noqa: BLE001 - поиск не должен ломать чат
                logger.error("Не удалось построить RAG-индекс: %s", exc)
                self._ready = False
                return False

    # ----------------------------------------------------------------- search
    @staticmethod
    def _preferred_kind(question: str) -> str | None:
        """Направляет RAG-поиск к нужному типу источника."""
        if any(
            term in question
            for term in ["динамик", "тренд", "по дням", "по датам", "за период"]
        ):
            return "daily"
        if any(term in question for term in ["поставщик", "поставк"]):
            return "supplier"
        if any(term in question for term in ["сценари", "техническ", "технологическ", "опасн"]):
            return "scenario"
        if any(
            term in question
            for term in ["норм", "предел", "допустим", "критери", "порог"]
        ):
            return "norms"
        if any(
            term in question
            for term in ["методик", "дерев", "модель", "алгоритм", "как счита"]
        ):
            return "method"
        return None

    def search(self, question: str, limit: int = 6) -> list[dict[str, Any]]:
        if not self.ensure_index():
            return []
        query = _normalize(question)
        if not query:
            return []

        direct_id = BATCH_ID_RE.search(question)
        direct_doc: dict[str, Any] | None = None
        if direct_id:
            batch_id = direct_id.group(0).upper()
            direct_doc = next(
                (
                    doc
                    for doc in self._documents
                    if doc["kind"] == "batch" and doc["id"] == batch_id
                ),
                None,
            )

        try:
            from sklearn.metrics.pairwise import cosine_similarity

            vector = self._vectorizer.transform([query])
            scores = cosine_similarity(vector, self._matrix)[0]
            ranked = scores.argsort()[::-1]
        except Exception as exc:  # noqa: BLE001
            logger.warning("Ошибка RAG-поиска: %s", exc)
            return [direct_doc] if direct_doc else []

        results: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        if direct_doc:
            # Для вопроса о конкретной партии не подмешиваем другие сценарии:
            # маленькая локальная модель иначе путает примеры партий.
            return [{**direct_doc, "score": 1.0}]

        preferred = self._preferred_kind(query)
        for index in ranked:
            doc = self._documents[int(index)]
            if preferred and doc["kind"] != preferred:
                continue
            score = float(scores[int(index)])
            if score < 0.02 and not preferred:
                break
            key = (doc["kind"], doc["id"])
            if key in seen:
                continue
            seen.add(key)
            results.append({**doc, "score": round(score, 4)})
            if len(results) >= limit:
                break
        return results

    # ------------------------------------------------------------------ facts
    def build_facts(
        self, question: str, analytical_answer: str = "", limit: int = 3
    ) -> dict[str, Any]:
        stats = self.engine.stats()
        dashboard = self.engine.dashboard()
        matches = self.search(question, limit=limit)

        reasons = ", ".join(
            f"{item['label']} — {item['count']}"
            for item in dashboard.get("top_reasons", [])[:4]
        )
        summary = (
            f"Всего {stats['total']} партий: низкий {stats['risk_counts']['низкий']['count']}, "
            f"средний {stats['risk_counts']['средний']['count']}, высокий "
            f"{stats['risk_counts']['высокий']['count']}; доля брака "
            f"{round(dashboard['defect_rate'] * 100)}%; средний риск "
            f"{dashboard['average_score']}/100; период {dashboard['period']['from']} — "
            f"{dashboard['period']['to']}. Частые причины: {reasons or 'нет'}."
        )

        fragments = []
        for index, doc in enumerate(matches, start=1):
            fragments.append(
                f"[{index}] ({doc['kind']}) {doc['text'][:320]}"
            )
        if not fragments:
            fragments.append("Релевантные фрагменты не найдены; используй общую сводку.")

        facts = (
            (f"КАРКАС ОТВЕТА:\n{analytical_answer[:260]}\n\n" if analytical_answer else "") +
            f"ФРАГМЕНТЫ RAG:\n" + "\n".join(fragments) +
            f"\n\nСВОДКА:\n{summary}"
        )

        sources = [
            {
                "kind": doc["kind"],
                "id": doc["id"],
                "score": doc["score"],
            }
            for doc in matches
        ]
        return {
            "facts": facts[:2200],
            "sources": sources,
            "matches": matches,
            "summary": summary,
        }
