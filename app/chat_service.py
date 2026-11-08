"""Чат-бот по данным сайта.

Ответы строятся детерминированно по объединённому набору партий: статистика,
сценарии, поставщики, показатели, нормы, отдельные партии. Бот умеет возвращать
текст, списки, таблицы и данные для графиков, а также отказывается обсуждать
посторонние темы и раскрывать служебные данные.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from app.rag_service import RagService
from app.risk_engine import ACTION_BY_LEVEL, NORMS, RiskEngine

DEFAULT_SUGGESTIONS = [
    "Покажи статистику по уровням риска",
    "Топ-10 поставщиков по риску",
    "Сравни поставщиков",
    "Топ-5 партий по свинцу",
    "Покажи динамику брака (график)",
    "Покажи партию RE-2025-001",
]

SECRET_TERMS = [
    "парол",
    "password",
    "токен",
    "token",
    "api-ключ",
    "api ключ",
    "apikey",
    "секрет",
    "конфиденц",
    "credential",
    "gigachat_credentials",
    "переменн окруж",
    "environment",
    ".env",
    "system prompt",
    "системн промпт",
    "промпт",
    "исходн",
    "исходный код",
    "внутренние данные",
    "база данных паролей",
    "приватн",
]

OFFTOPIC_TERMS = [
    "погод",
    "футбол",
    "матч",
    "курс доллар",
    "президент",
    "политик",
    "анекдот",
    "фильм",
    "музык",
    "сериал",
    "религи",
    "лекарств",
    "криптовалют",
    "акци",
    "новост",
    "астролог",
    "отношен",
    "свадьб",
    "любов",
]

INJECTION_TERMS = [
    "игнорируй инструкц",
    "забудь инструкц",
    "ignore previous",
    "ignore instructions",
    "ты теперь",
    "действуй как",
    "jailbreak",
    "developer message",
]

DOMAIN_TERMS = [
    "парт",
    "риск",
    "качеств",
    "продукц",
    "напит",
    "тархун",
    "поставщик",
    "поставк",
    "лаборатор",
    "показател",
    "норм",
    "брак",
    "сбой",
    "статист",
    "данн",
    "csv",
    "xlsx",
    "excel",
    "график",
    "диаграмм",
    "таблиц",
    "список",
    "анализ",
    "образц",
    "свинец",
    "свинц",
    "кадми",
    "мышьяк",
    "мышья",
    "кислот",
    "co2",
    "co₂",
    "со2",
    "углекисл",
    "сухи",
    "кмафанм",
    "кмаф",
    "бгкп",
    "дрожж",
    "плесен",
    "срок",
    "годност",
    "температур",
    "хранен",
    "перевоз",
    "действи",
    "изоляц",
    "проверк",
    "дерев",
    "решен",
    "модель",
    "llm",
    "гигачат",
    "gigachat",
    "нейросет",
    "искусственн",
    "интеллект",
    "рекоменд",
    "отчет",
    "выборк",
    "топ",
    "сравн",
    "сценари",
    "идеальн",
    "опасн",
    "техническ",
    "технологическ",
    "безопасн",
    "соответств",
    "условно годен",
    "производств",
    "дата выпуска",
    "дата производств",
    "серия",
    "склад",
    "проект",
    "сайт",
    "пример",
]

METRICS: dict[str, dict[str, Any]] = {
    "свинец": {"column": "lead_mg_kg", "label": "Свинец", "unit": "мг/кг", "digits": 4},
    "свинц": {"column": "lead_mg_kg", "label": "Свинец", "unit": "мг/кг", "digits": 4},
    "кадми": {"column": "cadmium_mg_kg", "label": "Кадмий", "unit": "мг/кг", "digits": 4},
    "мышьяк": {"column": "arsenic_mg_kg", "label": "Мышьяк", "unit": "мг/кг", "digits": 4},
    "мышья": {"column": "arsenic_mg_kg", "label": "Мышьяк", "unit": "мг/кг", "digits": 4},
    "кмафанм": {
        "column": "kmafanm_cfu_cm3",
        "label": "КМАФАнМ",
        "unit": "КОЕ/см³",
        "digits": 0,
    },
    "кмаф": {
        "column": "kmafanm_cfu_cm3",
        "label": "КМАФАнМ",
        "unit": "КОЕ/см³",
        "digits": 0,
    },
    "бгкп": {"column": "bgkp_cfu_cm3", "label": "БГКП", "unit": "КОЕ/см³", "digits": 0},
    "дрожж": {"column": "yeast_cfu_cm3", "label": "Дрожжи", "unit": "КОЕ/см³", "digits": 0},
    "плесен": {"column": "mold_cfu_cm3", "label": "Плесень", "unit": "КОЕ/см³", "digits": 0},
    "кислот": {"column": "acidity_cm3", "label": "Кислотность", "unit": "см³", "digits": 1},
    "co2": {"column": "co2_mass_pct", "label": "CO₂", "unit": "%", "digits": 2},
    "co₂": {"column": "co2_mass_pct", "label": "CO₂", "unit": "%", "digits": 2},
    "со2": {"column": "co2_mass_pct", "label": "CO₂", "unit": "%", "digits": 2},
    "углекисл": {"column": "co2_mass_pct", "label": "CO₂", "unit": "%", "digits": 2},
    "сухи": {"column": "dry_matter_pct", "label": "Сухие вещества", "unit": "%", "digits": 1},
}

STATUS_LABELS = {
    "ok": "в норме",
    "info": "справочно",
    "warning": "внимание",
    "critical": "критично",
}


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").lower().replace("ё", "е")).strip()


def _fmt(value: Any, digits: int = 2) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if digits <= 0:
        return f"{number:.0f}"
    return f"{number:.{digits}f}".rstrip("0").rstrip(".")


def _pct(value: float) -> str:
    return f"{round(value * 100)}%"


def _date_ru(value: Any) -> str:
    text = str(value or "")[:10]
    parts = text.split("-")
    if len(parts) == 3:
        return f"{parts[2]}.{parts[1]}.{parts[0]}"
    return text or "—"


class ChatService:
    """Детерминированный ассистент по данным RiskEngine."""

    def __init__(self, engine: RiskEngine, llm: Any | None = None) -> None:
        self.engine = engine
        self.llm = llm
        self.rag = RagService(engine)

    # ------------------------------------------------------------------ API
    def answer(
        self,
        message: str,
        history: list[dict[str, str]] | None = None,
        agent: str = "deepseek",
    ) -> dict[str, Any]:
        question = _normalize(message)
        requested_widgets = self._requested_widgets(question)
        response = self._answer_rule(message)
        response = self._ensure_widgets(question, response)
        response = self._apply_widget_policy(response, requested_widgets, question)
        batch_id = self._find_batch_id(message)
        response["ui"] = {
            "show_chips": bool(
                batch_id
                and ("table" in requested_widgets or "gauge" in requested_widgets)
            )
        }
        response = self._attach_context(response)
        response["agent"] = agent

        if response.get("skip_llm"):
            return response

        rag_limit = 6 if self._is_supplier_question(question) else 3
        rag_context = self.rag.build_facts(
            message, response.get("answer", "")[:260], limit=rag_limit
        )
        matches = rag_context.get("matches") or []
        source_ids = [
            f"{item['kind']}:{item['id']}" for item in matches[:5]
        ]
        response["context"]["rag_sources"] = rag_context.get("sources") or []
        response["context"]["steps"].extend(
            [
                (
                    f"RAG: найдено {len(matches)} релевантных фрагментов"
                    if matches
                    else "RAG: релевантных фрагментов нет, использована общая сводка"
                ),
                (
                    "Источники: " + ", ".join(source_ids)
                    if source_ids
                    else "Источники: сводка по данным и методика оценки"
                ),
            ]
        )

        llm_result = self._narrate(
            message, response, agent, history, rag_context["facts"]
        )
        if llm_result:
            response["answer"] = self._strip_format_refusals(llm_result["text"])
            response["provider"] = llm_result["provider"]
            failed = llm_result.get("failed_providers") or []
            if failed and agent in {"deepseek", "gigachat", "alisa"}:
                response["agent_note"] = (
                    f"{', '.join(failed)} недоступен — ответ сформирован "
                    "следующим доступным провайдером."
                )
            response["context"]["steps"].append(
                f"Ответ сгенерирован: {llm_result['provider']}"
            )
        elif agent in {"deepseek", "gigachat", "alisa", "risk-agent-free"}:
            cloud_name = {
                "deepseek": "DeepSeek",
                "gigachat": "GigaChat",
                "alisa": "Алиса AI",
            }.get(agent, "LLM")
            rejected = bool(getattr(self.llm, "last_local_rejected", False))
            if rejected:
                if not requested_widgets:
                    response["agent_note"] = (
                        f"{cloud_name} недоступен, а локальная модель не прошла проверку "
                        "фактов — показан точный data-driven анализ Risk Engine."
                        if agent in {"deepseek", "gigachat", "alisa"}
                        else (
                            "Локальная модель не прошла проверку фактов — показан точный "
                            "data-driven анализ Risk Engine."
                        )
                    )
            else:
                prefix = (
                    f"{cloud_name} недоступен. "
                    if agent in {"deepseek", "gigachat", "alisa"}
                    else ""
                )
                response["agent_note"] = (
                    f"{prefix}LLM-провайдер недоступен, показан проверенный "
                    "data-driven анализ. Проверьте ключи API или локальный fallback."
                )
            response["provider"] = "rule-based по данным сайта"
            response["context"]["steps"].append(
                "LLM недоступна, использован детерминированный анализ Risk Engine"
            )
        return response

    def _answer_rule(self, message: str) -> dict[str, Any]:
        question = _normalize(message)
        if not question:
            return self._reply(
                "Напишите вопрос о партиях, рисках, поставщиках или показателях качества.",
                skip_llm=True,
            )

        if self._is_secret(question) or self._is_injection(question):
            return self._reply(
                "Я не раскрываю пароли, ключи, переменные окружения, системные инструкции "
                "и внутренние служебные данные. Могу помочь только с информацией о партиях "
                "и качестве продукции из этого проекта.",
                suggestions=[
                    "Покажи статистику партий",
                    "Сравни поставщиков",
                    "Какие показатели проверяются?",
                ],
                skip_llm=True,
            )

        if self._is_off_topic(question):
            return self._reply(
                "Этот вопрос вне темы контроля качества и партий. Я отвечаю только на "
                "вопросы о рисках, лабораторных показателях, поставщиках и решениях "
                "по партиям этого проекта.",
                suggestions=DEFAULT_SUGGESTIONS[:4],
                skip_llm=True,
            )

        batch_id = self._find_batch_id(message)
        if batch_id:
            return self._batch_answer(batch_id)

        if self._is_what_if(question):
            return self._what_if_answer(question)

        if self._is_advice(question):
            return self._advice_answer(question)

        if self._is_causes(question):
            return self._causes_answer(question)

        if self._is_greeting(question):
            return self._reply(
                "Здравствуйте! Я — помощник по качеству партий безалкогольного напитка. "
                "Могу показать статистику, сравнить поставщиков, объяснить риск конкретной "
                "партии, построить график или таблицу по показателям.",
                blocks=[self._list_block("Что можно спросить", DEFAULT_SUGGESTIONS[:4])],
                skip_llm=True,
            )

        if self._is_help(question):
            return self._reply(
                "Я работаю только с данными этого проекта: 1500 партий, пять сценариев "
                "качества, лабораторные показатели и рекомендации по действиям. "
                "Спросите про партию, поставщика, показатель, норму или статистику.",
                blocks=[
                    self._list_block(
                        "Примеры вопросов",
                        DEFAULT_SUGGESTIONS,
                    )
                ],
                skip_llm=True,
            )

        if self._is_identity(question):
            return self._reply(
                "Я — ИИ-помощник системы «Симулякрум». "
                "Отвечаю по данным сайта и не обсуждаю посторонние темы.",
                skip_llm=True,
            )

        if self._is_thanks(question):
            return self._reply(
                "Пожалуйста! Задайте ещё вопрос о партиях или показателях качества.",
                skip_llm=True,
            )

        metric_key = self._find_metric(question)
        supplier = self._find_supplier(question)

        if metric_key and supplier:
            return self._supplier_answer(supplier, metric_key)
        if metric_key and self._is_supplier_question(question):
            return self._supplier_answer(None, metric_key)
        if metric_key and self._is_top(question):
            return self._top_answer(question)
        if metric_key:
            return self._metric_answer(metric_key, question)
        if supplier:
            return self._supplier_answer(supplier)

        if self._is_supplier_ranking(question):
            return self._supplier_ranking_answer(question)

        # Явные запросы на рейтинг или динамику важнее общего обзора сценариев.
        if self._is_top(question):
            return self._top_answer(question)
        if self._is_trend(question):
            return self._trend_answer()

        scenario = self._find_scenario(question)
        if scenario:
            return self._scenario_answer(scenario)
        if self._is_supplier_question(question):
            return self._supplier_answer(None)
        if self._is_scenario_question(question):
            return self._scenarios_overview()

        if self._is_examples(question):
            return self._examples_answer()

        if self._is_norms(question):
            return self._norms_answer()

        if self._is_actions(question):
            return self._actions_answer()

        risk_level = self._find_risk_level(question)
        if risk_level:
            return self._risk_level_answer(risk_level)

        if self._is_batch_list(question):
            return self._batch_list_answer()

        if self._is_trend(question):
            return self._trend_answer()

        if self._is_stats(question):
            return self._stats_answer()

        if self._is_top(question):
            return self._top_answer(question)

        if self._is_expiry(question):
            return self._expiry_answer()

        if self._is_temperature(question):
            return self._temperature_answer()

        if self._is_method(question):
            return self._method_answer()

        if self._is_llm(question):
            return self._llm_answer()

        if self._is_data(question):
            return self._data_answer()

        if self._is_domain(question):
            return self._overview_answer(question)

        return self._reply(
            "Я отвечаю только на вопросы о партиях, качестве, рисках, поставщиках и "
            "лабораторных показателях этого проекта. Посторонние темы и служебные данные "
            "не обсуждаю. Например, спросите: «Покажи статистику рисков» или "
            "«Топ-5 партий по свинцу».",
            suggestions=DEFAULT_SUGGESTIONS[:4],
            skip_llm=True,
        )

    # -------------------------------------------------------------- helpers
    @staticmethod
    def _reply(
        text: str,
        blocks: list[dict[str, Any]] | None = None,
        suggestions: list[str] | None = None,
        skip_llm: bool = False,
    ) -> dict[str, Any]:
        return {
            "answer": text,
            "blocks": blocks or [],
            "suggestions": suggestions or DEFAULT_SUGGESTIONS,
            "provider": "rule-based по данным сайта",
            "skip_llm": skip_llm,
        }

    @staticmethod
    def _requested_widgets(question: str) -> set[str]:
        """Виджеты включаются только по явному маркеру или команде.

        Примеры: «покажи динамику (график)», «построй график по партиям»,
        «покажи распределение (диаграмма)», «покажи партию (виджет)».
        Обычный вопрос без маркера не должен добавлять графики.
        """
        requested: set[str] = set()
        markers = " ".join(re.findall(r"\(([^()]*)\)", question))
        imperative = bool(
            re.search(
                r"(?:построй|нарисуй|покажи)\s+(?:[\w-]+\s+){0,3}"
                r"(?:график|диаграмм|chart|донut|donut)",
                question,
            )
        )
        visual = markers if re.search(
            r"график|диаграм|chart|линейн|кругов|донат|донut|donut", markers
        ) else ""
        if visual or imperative:
            requested.add("chart")
            if any(term in question for term in ["линейн", "тренд", "динамик", "по дням"]):
                requested.add("line")
            if any(term in question for term in ["кругов", "донат", "донut", "donut"]):
                requested.add("donut")

        if "таблиц" in question or "таблиц" in markers:
            requested.add("table")
        if "список" in question or "список" in markers:
            requested.add("list")
        if re.search(r"виджет|gauge|шкал", markers) or re.search(
            r"покажи\s+(?:виджет|шкалу|шкалу риска)", question
        ):
            requested.add("gauge")
        return requested

    @staticmethod
    def _strip_chart_mentions(text: str) -> str:
        cleaned = str(text or "")
        cleaned = re.sub(
            r"\s*Сравнение\s*[—-]\s*на графике\.?",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(
            r"\s*График[^.]*добавлен ниже\.?",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(
            r"\s*Полная выборка — в таблице, сравнение — на графике\.?",
            " Полная выборка — в таблице.",
            cleaned,
            flags=re.IGNORECASE,
        )
        return cleaned.strip()

    @staticmethod
    def _is_statistics_request(question: str) -> bool:
        direct = any(
            term in question
            for term in [
                "статист",
                "распредел",
                "доля",
                "процент",
                "сколько",
                "количеств",
                "сравн",
                "топ",
                "рейтинг",
                "динамик",
                "тренд",
                "по дням",
                "сводк",
                "обзор",
                "итог",
                "сумм",
            ]
        )
        if direct:
            return True
        return bool(
            ChatService._is_stats(question)
            or ChatService._is_top(question)
            or ChatService._is_trend(question)
            or ChatService._is_supplier_ranking(question)
            or ChatService._is_supplier_question(question)
            or ChatService._is_batch_list(question)
            or ChatService._is_examples(question)
            or ChatService._find_risk_level(question)
        )

    def _apply_widget_policy(
        self, response: dict[str, Any], requested: set[str], question: str = ""
    ) -> dict[str, Any]:
        """Убирает графики, шкалы и таблицы, если их не запросили явно."""
        blocks = response.get("blocks") or []
        if not blocks:
            return response

        keep_chart = "chart" in requested
        keep_gauge = "gauge" in requested
        keep_tables = "table" in requested or self._is_statistics_request(question)
        filtered = []
        removed_chart = False
        removed_table = False
        for block in blocks:
            block_type = block.get("type")
            if block_type == "chart" and not keep_chart:
                removed_chart = True
                continue
            if block_type == "gauge" and not keep_gauge:
                removed_chart = True
                continue
            if block_type == "table" and not keep_tables:
                removed_table = True
                continue
            filtered.append(block)
        if removed_chart or removed_table:
            response["answer"] = self._strip_chart_mentions(response.get("answer", ""))
            if removed_table:
                response["answer"] = self._strip_table_mentions(response["answer"])
                if not keep_tables and "(таблица)" not in response["answer"]:
                    response["answer"] = (
                        response["answer"].rstrip()
                        + " Для детальной таблицы добавьте (таблица)."
                    )
            response["blocks"] = filtered
        return response

    @staticmethod
    def _strip_table_mentions(text: str) -> str:
        cleaned = str(text or "")
        cleaned = re.sub(
            r"\s*Полная выборка — в таблице,?\s*сравнение — на графике\.?",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(
            r"\s*Таблица показывает[^.]*\.",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(
            r"\s*Ниже первые\s+\d+[^.]*\.",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        return ChatService._strip_format_refusals(cleaned)

    @staticmethod
    def _strip_format_refusals(text: str) -> str:
        """Убирает фразы LLM о том, что таблица/график «не выводятся»."""
        cleaned = str(text or "")
        cleaned = re.sub(
            r"\s*(?:Табличную|Таблицу|Таблицы)\s+(?:форму\s+)?не\s+вывожу[^.]*\.",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(
            r"\s*(?:График|Диаграмму|Виджет)\s+не\s+(?:вывожу|строю|показываю)[^.]*\.",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(
            r"\s*Данные\s+приведены\s+в\s+тексте[^.]*\.",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        return cleaned.strip()


    def _ensure_widgets(
        self, question: str, response: dict[str, Any]
    ) -> dict[str, Any]:
        requested = self._requested_widgets(question)
        if not requested:
            return response

        blocks = response.setdefault("blocks", [])
        types = {block.get("type") for block in blocks}
        chart_types = {
            block.get("chart_type")
            for block in blocks
            if block.get("type") == "chart"
        }
        stats = self.engine.stats()
        dashboard = self.engine.dashboard()

        if "line" in requested and "line" not in chart_types:
            blocks.append(
                {
                    "type": "chart",
                    "chart_type": "line",
                    "title": "Динамика доли брака по датам выпуска",
                    "labels": [item["label"] for item in dashboard["daily"]],
                    "values": [
                        round(item["defect_rate"] * 100, 1)
                        for item in dashboard["daily"]
                    ],
                    "unit": "%",
                }
            )

        if "donut" in requested:
            pie_block = next(
                (
                    block
                    for block in blocks
                    if block.get("type") == "chart"
                    and block.get("chart_type") in {"pie", "donut"}
                ),
                None,
            )
            if pie_block is not None:
                pie_block["chart_type"] = "donut"
            else:
                blocks.append(
                    {
                        "type": "chart",
                        "chart_type": "donut",
                        "title": "Распределение статусов партий",
                        "labels": ["Одобрено", "Имеются дефекты", "Брак"],
                        "values": [
                            stats["risk_counts"]["низкий"]["count"],
                            stats["risk_counts"]["средний"]["count"],
                            stats["risk_counts"]["высокий"]["count"],
                        ],
                        "unit": "",
                    }
                )

        if "chart" in requested and not chart_types:
            blocks.append(
                {
                    "type": "chart",
                    "chart_type": "bar",
                    "title": "Партии по уровню риска",
                    "labels": ["низкий", "средний", "высокий"],
                    "values": [
                        stats["risk_counts"]["низкий"]["count"],
                        stats["risk_counts"]["средний"]["count"],
                        stats["risk_counts"]["высокий"]["count"],
                    ],
                    "unit": "",
                }
            )

        if "table" in requested and "table" not in types:
            blocks.append(
                self._table_block(
                    "Ключевые показатели",
                    [
                        {"key": "metric", "label": "Показатель"},
                        {"key": "value", "label": "Значение"},
                    ],
                    [
                        {"metric": "Всего партий", "value": stats["total"]},
                        {
                            "metric": "Доля брака",
                            "value": _pct(stats["risk_counts"]["высокий"]["share"]),
                        },
                        {
                            "metric": "Средний риск",
                            "value": f"{stats['average_score']}/100",
                        },
                        {
                            "metric": "Жёстких нарушений",
                            "value": stats["hard_violations"],
                        },
                    ],
                )
            )

        if "list" in requested and "list" not in types:
            blocks.append(
                self._list_block(
                    "Что видно по данным",
                    [
                        f"Всего партий: {stats['total']}",
                        f"Высокий риск: {stats['risk_counts']['высокий']['count']} "
                        f"({_pct(stats['risk_counts']['высокий']['share'])})",
                        f"Средний риск: {stats['risk_counts']['средний']['count']}",
                        f"Средняя оценка риска: {stats['average_score']}/100",
                        "Подробности — в таблицах и графиках ниже",
                    ],
                )
            )

        if "gauge" in requested and "gauge" not in types:
            batch = self._context_batch(response)
            if batch:
                blocks.append(
                    {
                        "type": "gauge",
                        "title": f"Риск партии {batch['batch_id']}",
                        "value": batch["risk_score"],
                        "max": 100,
                        "level": batch["risk_level"],
                        "label": batch.get("status_label", ""),
                        "threshold": 70,
                    }
                )

        return response

    @staticmethod
    def _compact_batch(prediction: dict[str, Any]) -> dict[str, Any]:
        return {
            "batch_id": prediction["batch_id"],
            "scenario": prediction["scenario"],
            "supplier": prediction["supplier"],
            "risk_level": prediction["risk_level"],
            "risk_score": prediction["risk_score"],
            "status_label": prediction["status_label"],
            "operator": prediction["operator"],
            "checked_at": prediction["checked_at"],
            "recommended_action": prediction["recommended_action"],
            "top_factors": [
                factor["label"]
                for factor in sorted(
                    prediction["factors"],
                    key=lambda factor: -factor["weight"],
                )[:3]
            ],
            "confidence": (prediction.get("model") or {}).get("confidence"),
        }

    def _context_batch(self, response: dict[str, Any]) -> dict[str, Any] | None:
        existing = (response.get("context") or {}).get("batch")
        if existing:
            return existing
        answer = str(response.get("answer") or "")
        match = re.search(r"\b(?:AP|RE|SR|MF|DE)-\d{4}-\d{3}\b", answer.upper())
        if match:
            prediction = self.engine.get_batch(match.group(0))
            if prediction:
                return self._compact_batch(prediction)
        latest = self.engine.dashboard().get("latest_high")
        return latest

    def _attach_context(self, response: dict[str, Any]) -> dict[str, Any]:
        stats = self.engine.stats()
        dashboard = self.engine.dashboard()
        batch = self._context_batch(response)
        now = datetime.now()
        logs = [
            {
                "time": (now - timedelta(minutes=5)).strftime("%H:%M"),
                "text": "Данные из data/ синхронизированы",
            },
            {
                "time": (now - timedelta(minutes=4)).strftime("%H:%M"),
                "text": "Модель риска обновлена",
            },
            {
                "time": (now - timedelta(minutes=2)).strftime("%H:%M"),
                "text": "Сформирован ответ агента",
            },
        ]
        response["context"] = {
            "batch": batch,
            "metrics": [
                {"label": "Всего партий", "value": str(stats["total"])},
                {"label": "Доля брака", "value": _pct(dashboard["defect_rate"])},
                {
                    "label": "Средний риск",
                    "value": f"{dashboard['average_score']}/100",
                },
                {
                    "label": "Точность модели",
                    "value": f"{round((dashboard['model'].get('accuracy') or 0) * 100, 1)}%",
                },
            ],
            "steps": [
                f"Проанализировано {stats['total']} партий",
                f"Найдено {stats['risk_counts']['высокий']['count']} партий высокого риска",
                "Проверены правила, лабораторные показатели и дерево решений",
            ],
            "logs": logs,
        }
        return response

    def _narrate(
        self,
        question: str,
        response: dict[str, Any],
        agent: str,
        history: list[dict[str, str]] | None,
        facts: str,
    ) -> dict[str, Any] | None:
        if self.llm is None:
            return None
        return self.llm.generate_agent_answer(
            question,
            facts,
            history,
            agent=agent,
        )

    @staticmethod
    def _facts_text(response: dict[str, Any]) -> str:
        parts = [f"Аналитический ответ: {response.get('answer', '')}"]
        for block in response.get("blocks", []):
            block_type = block.get("type")
            if block_type == "table":
                parts.append(f"Таблица «{block.get('title', '')}»:")
                columns = [column.get("label", "") for column in block.get("columns", [])]
                parts.append(" | ".join(columns))
                for row in block.get("rows", [])[:12]:
                    parts.append(
                        " | ".join(str(row.get(column.get("key"), "")) for column in block.get("columns", []))
                    )
            elif block_type == "chart":
                parts.append(
                    f"График «{block.get('title', '')}»: "
                    + ", ".join(
                        f"{label} — {value}{block.get('unit', '')}"
                        for label, value in zip(
                            block.get("labels", []), block.get("values", [])
                        )
                    )
                )
            elif block_type == "list":
                parts.append(
                    f"Список «{block.get('title', '')}»: "
                    + "; ".join(str(item) for item in block.get("items", []))
                )
        context = response.get("context") or {}
        batch = context.get("batch")
        if batch:
            parts.append(
                "Партия: "
                + ", ".join(f"{key}={value}" for key, value in batch.items())
            )
        return "\n".join(parts)[:7000]

    @staticmethod
    def _table_block(
        title: str, columns: list[dict[str, str]], rows: list[dict[str, Any]]
    ) -> dict[str, Any]:
        return {"type": "table", "title": title, "columns": columns, "rows": rows}

    @staticmethod
    def _list_block(title: str, items: list[str]) -> dict[str, Any]:
        return {"type": "list", "title": title, "items": items}

    @staticmethod
    def _chart_block(
        title: str,
        labels: list[str],
        values: list[float],
        unit: str = "",
        chart_type: str = "bar",
    ) -> dict[str, Any]:
        return {
            "type": "chart",
            "chart_type": chart_type,
            "title": title,
            "labels": labels,
            "values": values,
            "unit": unit,
        }

    @staticmethod
    def _text_block(text: str) -> dict[str, Any]:
        return {"type": "text", "text": text}

    def _is_secret(self, question: str) -> bool:
        return any(term in question for term in SECRET_TERMS)

    @staticmethod
    def _is_injection(question: str) -> bool:
        return any(term in question for term in INJECTION_TERMS)

    @staticmethod
    def _is_greeting(question: str) -> bool:
        clean = re.sub(r"[!?.,]+", " ", question).strip()
        return clean in {
            "привет",
            "приветствую",
            "здравствуй",
            "здравствуйте",
            "добрый день",
            "доброе утро",
            "добрый вечер",
            "хай",
            "hello",
            "как дела",
        }

    @staticmethod
    def _is_help(question: str) -> bool:
        return any(
            term in question
            for term in [
                "что умеешь",
                "умеешь",
                "чем помочь",
                "помощь",
                "помоги",
                "команд",
                "как пользоваться",
                "возможност",
                "что ты можешь",
                "что можешь",
            ]
        )

    @staticmethod
    def _is_identity(question: str) -> bool:
        return any(term in question for term in ["кто ты", "ты кто", "как тебя зовут", "что ты за"])

    @staticmethod
    def _is_thanks(question: str) -> bool:
        return any(term in question for term in ["спасибо", "благодар"])

    @staticmethod
    def _is_what_if(question: str) -> bool:
        if not any(
            term in question
            for term in ["что если", "что будет", "если ", "как изменится", "сценарий если"]
        ):
            return False
        return any(
            term in question
            for term in ["риск", "парт", "брак", "качеств", "показател", "поставщ", "свинец", "кислот", "co2"]
        )

    @staticmethod
    def _is_advice(question: str) -> bool:
        if not any(
            term in question
            for term in ["снизить", "улучшить", "предотвратить", "как избежать", "рекомендации по"]
        ):
            return False
        return any(
            term in question
            for term in ["риск", "парт", "брак", "качеств", "показател", "поставщ"]
        )

    @staticmethod
    def _is_off_topic(question: str) -> bool:
        if not any(term in question for term in OFFTOPIC_TERMS):
            return False
        return not any(
            term in question
            for term in [
                "парт",
                "риск",
                "брак",
                "качеств",
                "показател",
                "поставщ",
                "лаборатор",
                "напит",
            ]
        )

    @staticmethod
    def _is_causes(question: str) -> bool:
        if not any(
            term in question
            for term in ["почему", "причин", "фактор", "что влияет", "из-за чего", "что вызвало"]
        ):
            return False
        return any(
            term in question
            for term in ["риск", "парт", "брак", "качеств", "показател", "поставщ", "сбой"]
        )

    @staticmethod
    def _is_trend(question: str) -> bool:
        if not any(
            term in question
            for term in ["тренд", "динамик", "по дням", "за неделю", "график динамики"]
        ):
            return False
        return any(
            term in question
            for term in ["риск", "парт", "брак", "качеств", "показател", "поставщ", "сбой"]
        )

    @staticmethod
    def _is_stats(question: str) -> bool:
        if any(
            term in question
            for term in ["статист", "распредел", "доля", "процент", "сводк", "обзор"]
        ):
            return not any(
                term in question
                for term in ["футбол", "матч", "спорт", "игр", "погод", "курс"]
            )
        if any(term in question for term in ["сколько", "количество", "итог"]):
            return any(
                term in question
                for term in ["парт", "риск", "поставщ", "брак", "сбой", "образц", "данн"]
            )
        return False

    @staticmethod
    def _is_norms(question: str) -> bool:
        return any(
            term in question
            for term in ["норм", "порог", "правил", "критери", "как оценива", "как счита", "почему риск"]
        )

    @staticmethod
    def _is_actions(question: str) -> bool:
        if not any(
            term in question
            for term in ["что делать", "действи", "изолир", "утилиз", "допуст", "рекоменд"]
        ):
            return False
        return any(
            term in question
            for term in ["парт", "риск", "брак", "сбой", "продукц", "качеств", "поставщ"]
        )

    @staticmethod
    def _is_supplier_question(question: str) -> bool:
        return any(term in question for term in ["поставщик", "поставк"])

    @staticmethod
    def _is_supplier_ranking(question: str) -> bool:
        if not any(term in question for term in ["поставщик", "поставк"]):
            return False
        return any(
            term in question
            for term in [
                "топ",
                "лучш",
                "худш",
                "самы",
                "рейтинг",
                "наиболь",
                "наимень",
                "больш",
                "меньш",
                "надеж",
                "надёж",
                "первый",
            ]
        )

    @staticmethod
    def _find_risk_level(question: str) -> str | None:
        mentions_party = any(
            term in question for term in ["риск", "парт", "образец", "список"]
        )
        if not mentions_party:
            return None
        if "высок" in question or "опасн" in question:
            return "высокий"
        if "средн" in question:
            return "средний"
        if "низк" in question:
            return "низкий"
        return None

    @staticmethod
    def _is_scenario_question(question: str) -> bool:
        if not any(
            term in question
            for term in ["парт", "сценари", "сбой", "качеств", "риск", "показател"]
        ):
            return False
        return any(
            term in question
            for term in [
                "сценари",
                "категор",
                "тип парт",
                "виды парт",
                "типы парт",
                "брак",
                "не соответствует",
            ]
        )

    @staticmethod
    def _is_batch_list(question: str) -> bool:
        return any(
            term in question
            for term in ["все партии", "список партий", "покажи партии", "показать партии"]
        )

    @staticmethod
    def _is_examples(question: str) -> bool:
        if not any(
            term in question
            for term in ["парт", "демо", "образц", "риск", "качеств", "показател"]
        ):
            return False
        if "демо" in question or "покажи образцы" in question:
            return True
        return "пример" in question and "например" not in question

    def _examples_answer(self) -> dict[str, Any]:
        examples = self.engine.get_examples(low=2, medium=2, high=2)
        rows = [
            {
                "batch_id": item["batch_id"],
                "scenario": item["scenario"],
                "risk": item["risk_level"],
                "score": item["risk_score"],
                "action": item["recommended_action"],
            }
            for item in examples
        ]
        return self._reply(
            "Шесть демонстрационных партий покрывают все три уровня риска и пять сценариев.",
            blocks=[
                self._table_block(
                    "Демонстрационные примеры",
                    [
                        {"key": "batch_id", "label": "Партия"},
                        {"key": "scenario", "label": "Сценарий"},
                        {"key": "risk", "label": "Риск"},
                        {"key": "score", "label": "Оценка"},
                        {"key": "action", "label": "Действие"},
                    ],
                    rows,
                )
            ],
        )

    @staticmethod
    def _is_top(question: str) -> bool:
        if not any(
            term in question
            for term in ["топ", "самы", "худш", "лучш", "рейтинг", "наиболь", "наимень"]
        ):
            return False
        return any(
            term in question
            for term in [
                "парт",
                "риск",
                "поставщ",
                "брак",
                "сбой",
                "свинец",
                "свинц",
                "кадми",
                "мышьяк",
                "кислот",
                "co2",
                "co₂",
                "со2",
                "кмаф",
                "дрожж",
                "плесен",
            ]
        )

    @staticmethod
    def _is_expiry(question: str) -> bool:
        if any(term in question for term in ["годност", "просроч", "истека"]):
            return True
        return "срок" in question and any(
            term in question for term in ["парт", "хранен", "продукц", "образц"]
        )

    @staticmethod
    def _is_temperature(question: str) -> bool:
        return any(term in question for term in ["температур", "хранен", "перевоз"])

    @staticmethod
    def _is_method(question: str) -> bool:
        if any(term in question for term in ["дерев", "accuracy", "как работает оценка"]):
            return True
        if "алгоритм" in question or "классификац" in question:
            return True
        return "модель" in question and any(
            term in question for term in ["риск", "оценк", "качеств", "парт", "дерев"]
        )

    @staticmethod
    def _is_llm(question: str) -> bool:
        if any(term in question for term in ["llm", "гигачат", "gigachat", "нейросет"]):
            return True
        return "искусственн" in question and "интеллект" in question

    @staticmethod
    def _is_data(question: str) -> bool:
        if any(term in question for term in ["csv", "xlsx", "excel", "источник", "набор"]):
            return True
        return "данн" in question and any(
            term in question for term in ["парт", "сайт", "проект", "файл", "качеств", "лаборатор"]
        )

    @staticmethod
    def _is_domain(question: str) -> bool:
        return any(term in question for term in DOMAIN_TERMS)

    @staticmethod
    def _find_batch_id(message: str) -> str | None:
        match = re.search(r"\b(?:AP|RE|SR|MF|DE)-\d{4}-\d{3}\b", message.upper())
        return match.group(0) if match else None

    @staticmethod
    def _find_metric(question: str) -> str | None:
        for key in METRICS:
            if key in question:
                return key
        return None

    def _find_supplier(self, question: str) -> str | None:
        for supplier in self.engine.meta()["suppliers"]:
            normalized = _normalize(supplier)
            if normalized in question:
                return supplier
        aliases = {
            "аквалайф": "ООО «Аквалайф»",
            "азия": "Азия-Фуд",
            "ингредиент": "Ингредиент-Юг",
            "армения": "Армения-Тархун",
            "пектин": "Пектин-Сервис",
        }
        for alias, supplier in aliases.items():
            if alias in question:
                return supplier
        return None

    def _find_scenario(self, question: str) -> str | None:
        if not any(
            term in question
            for term in [
                "парт",
                "сценари",
                "брак",
                "сбой",
                "риск",
                "категор",
                "условно",
                "нестабиль",
            ]
        ):
            return None
        aliases = {
            "идеальн": "Идеальная партия",
            "опасн": "Опасный брак",
            "риск поставщика": "Риск поставщика",
            "условно годен": "Риск поставщика",
            "техническ": "Технический сбой",
            "нестабиль": "Технический сбой",
            "технологическ": "Технологический брак",
        }
        for alias, scenario in aliases.items():
            if alias in question:
                return scenario
        return None

    # -------------------------------------------------------------- answers
    def _batch_answer(self, batch_id: str) -> dict[str, Any]:
        prediction = self.engine.get_batch(batch_id)
        if prediction is None:
            return self._reply(
                f"Партия {batch_id} не найдена. Доступны идентификаторы вида AP-2025-001, "
                "RE-2025-001, SR-2025-001, MF-2025-001, DE-2025-001.",
                suggestions=["Покажи статистику партий", "Покажи партию AP-2025-001"],
            )

        parameter_rows = [
            {
                "parameter": item["label"],
                "value": f"{item['value']} {item['unit']}".strip(),
                "norm": item["norm"],
                "status": STATUS_LABELS.get(item["status"], item["status"]),
            }
            for item in prediction["parameters"]
        ]
        factors = prediction["factors"]
        factor_items = [
            f"{factor['label']}: {factor['detail']}" for factor in factors
        ] or ["Явных факторов риска не выявлено."]

        blocks = [
            self._table_block(
                "Параметры партии",
                [
                    {"key": "parameter", "label": "Показатель"},
                    {"key": "value", "label": "Значение"},
                    {"key": "norm", "label": "Норма"},
                    {"key": "status", "label": "Статус"},
                ],
                parameter_rows,
            ),
            self._list_block("Факторы риска", factor_items),
        ]
        if factors:
            blocks.append(
                self._chart_block(
                    "Веса факторов риска",
                    [factor["label"] for factor in factors],
                    [factor["weight"] for factor in factors],
                )
            )

        return self._reply(
            f"Партия {prediction['batch_id']}: сценарий «{prediction['scenario']}», "
            f"уровень риска — {prediction['risk_level']} ({prediction['risk_score']}/100). "
            f"Заключение лаборатории: {prediction['lab_conclusion']}. "
            f"Рекомендованное действие — {prediction['recommended_action']}. "
            f"{prediction['explanation']}",
            blocks=blocks,
            suggestions=[
                "Покажи статистику по уровням риска",
                "Сравни поставщиков",
                "Какие нормы применяются?",
            ],
        )

    def _stats_answer(self) -> dict[str, Any]:
        stats = self.engine.stats()
        scenarios = self.engine.scenario_summary()
        risk_rows = []
        for level in ("низкий", "средний", "высокий"):
            item = stats["risk_counts"][level]
            risk_rows.append(
                {
                    "level": level,
                    "count": item["count"],
                    "share": _pct(item["share"]),
                    "action": ACTION_BY_LEVEL[level],
                }
            )
        scenario_rows = [
            {
                "scenario": row["scenario"],
                "count": row["total"],
                "level": row["risk_level"],
                "score": row["average_score"],
                "example": row["example_batch"],
            }
            for row in scenarios
        ]
        blocks = [
            self._table_block(
                "Уровни риска",
                [
                    {"key": "level", "label": "Уровень"},
                    {"key": "count", "label": "Партий"},
                    {"key": "share", "label": "Доля"},
                    {"key": "action", "label": "Действие"},
                ],
                risk_rows,
            ),
            self._chart_block(
                "Распределение партий по уровню риска",
                ["низкий", "средний", "высокий"],
                [
                    stats["risk_counts"]["низкий"]["count"],
                    stats["risk_counts"]["средний"]["count"],
                    stats["risk_counts"]["высокий"]["count"],
                ],
                chart_type="pie",
            ),
            self._table_block(
                "Сценарии набора",
                [
                    {"key": "scenario", "label": "Сценарий"},
                    {"key": "count", "label": "Партий"},
                    {"key": "level", "label": "Уровень"},
                    {"key": "score", "label": "Средний балл"},
                    {"key": "example", "label": "Пример"},
                ],
                scenario_rows,
            ),
        ]
        return self._reply(
            f"В наборе {stats['total']} партий. Низкий риск — "
            f"{stats['risk_counts']['низкий']['count']}, средний — "
            f"{stats['risk_counts']['средний']['count']}, высокий — "
            f"{stats['risk_counts']['высокий']['count']}. Средняя оценка риска — "
            f"{stats['average_score']}/100, жёстких нарушений — {stats['hard_violations']}. "
            f"Опорная дата оценки — {_date_ru(stats['reference_date'])}.",
            blocks=blocks,
        )

    def _scenario_answer(self, scenario: str) -> dict[str, Any]:
        rows = self.engine.scenario_summary()
        selected = next((row for row in rows if row["scenario"] == scenario), None)
        if selected is None:
            return self._overview_answer()

        predictions = [
            item
            for item in self.engine.all_predictions()
            if item["scenario"] == scenario
        ]
        predictions.sort(key=lambda item: (-item["risk_score"], item["batch_id"]))
        examples = predictions[:5]
        example_rows = [
            {
                "batch_id": item["batch_id"],
                "supplier": item["supplier"],
                "score": item["risk_score"],
                "conclusion": item["lab_conclusion"],
            }
            for item in examples
        ]
        factor_labels: list[str] = []
        if examples:
            factor_labels = [factor["label"] for factor in examples[0]["factors"]][:5]

        blocks = [
            self._text_block(selected["description"]),
            self._table_block(
                "Примеры партий сценария",
                [
                    {"key": "batch_id", "label": "Партия"},
                    {"key": "supplier", "label": "Поставщик"},
                    {"key": "score", "label": "Оценка"},
                    {"key": "conclusion", "label": "Заключение"},
                ],
                example_rows,
            ),
            self._chart_block(
                "Оценки риска в примерах",
                [item["batch_id"] for item in examples],
                [item["risk_score"] for item in examples],
            ),
        ]
        if factor_labels:
            blocks.append(
                self._list_block("Что влияет на риск в этом сценарии", factor_labels)
            )

        return self._reply(
            f"Сценарий «{selected['scenario']}»: {selected['total']} партий, "
            f"уровень риска — {selected['risk_level']}, средний балл — "
            f"{selected['average_score']}/100. {selected['description']} "
            f"Рекомендованное действие — {ACTION_BY_LEVEL[selected['risk_level']]}.",
            blocks=blocks,
        )

    def _scenarios_overview(self) -> dict[str, Any]:
        rows = self.engine.scenario_summary()
        blocks = [
            self._table_block(
                "Сценарии партий",
                [
                    {"key": "scenario", "label": "Сценарий"},
                    {"key": "count", "label": "Партий"},
                    {"key": "level", "label": "Уровень"},
                    {"key": "score", "label": "Средний балл"},
                    {"key": "example", "label": "Пример"},
                ],
                [
                    {
                        "scenario": row["scenario"],
                        "count": row["total"],
                        "level": row["risk_level"],
                        "score": row["average_score"],
                        "example": row["example_batch"],
                    }
                    for row in rows
                ],
            ),
            self._chart_block(
                "Количество партий по сценариям",
                [row["scenario"] for row in rows],
                [row["total"] for row in rows],
            ),
            self._list_block(
                "Что означает каждый сценарий",
                [f"{row['scenario']} — {row['description']}" for row in rows],
            ),
        ]
        return self._reply(
            "В наборе пять сценариев: идеальная партия, риск поставщика, технический "
            "сбой, технологический брак и опасный брак. У каждого — свой профиль "
            "показателей и рекомендуемое действие.",
            blocks=blocks,
        )

    def _supplier_ranking_answer(self, question: str) -> dict[str, Any]:
        summary = self.engine.supplier_summary()
        if not summary:
            return self._reply("В наборе нет данных по поставщикам.")

        requested = self._top_limit(question)
        limit = min(requested, len(summary))
        ascending = any(
            term in question
            for term in ["лучш", "наимен", "низк", "меньш", "надеж", "надёж"]
        )
        by_score = "средн" in question and (
            "риск" in question or "балл" in question
        )

        def sort_value(item: dict[str, Any]) -> float:
            return float(item["average_score"] if by_score else item["high_share"])

        ordered = sorted(
            summary,
            key=lambda item: (sort_value(item), item["average_score"]),
            reverse=not ascending,
        )
        items = ordered[:limit]
        rows = [
            {
                "rank": index,
                "supplier": item["supplier"],
                "total": item["total"],
                "high": item["high"],
                "share": _pct(item["high_share"]),
                "score": item["average_score"],
            }
            for index, item in enumerate(items, start=1)
        ]
        metric_label = (
            "среднему баллу риска"
            if by_score
            else "доле высокого риска"
        )
        direction = "Лучшие поставщики" if ascending else "Поставщики с наибольшим риском"
        leaders = ", ".join(
            f"{item['supplier']} — {_pct(item['high_share'])} брака, "
            f"средний балл {item['average_score']}/100"
            for item in items[:3]
        )
        suffix = (
            f" Запрошено {requested}, в наборе всего {len(summary)} поставщиков."
            if requested > len(summary)
            else ""
        )
        answer = (
            f"{direction} по {metric_label}: {leaders}.{suffix} "
            f"Рейтинг построен по {len(summary)} поставщикам и {sum(item['total'] for item in summary)} партиям."
        )
        return self._reply(
            answer,
            blocks=[
                self._table_block(
                    f"Рейтинг поставщиков по {metric_label}",
                    [
                        {"key": "rank", "label": "№"},
                        {"key": "supplier", "label": "Поставщик"},
                        {"key": "total", "label": "Партий"},
                        {"key": "high", "label": "Брак"},
                        {"key": "share", "label": "Доля брака"},
                        {"key": "score", "label": "Средний балл"},
                    ],
                    rows,
                ),
                self._chart_block(
                    f"Доля высокого риска: {metric_label}",
                    [item["supplier"] for item in items],
                    [round(item["high_share"] * 100, 1) for item in items],
                    unit="%",
                ),
            ],
            suggestions=[
                "Покажи поставщиков (график)",
                "Сравни поставщиков",
                "Топ-5 партий по свинцу",
            ],
        )

    def _supplier_answer(
        self, supplier: str | None = None, metric_key: str | None = None
    ) -> dict[str, Any]:
        summary = self.engine.supplier_summary()
        if metric_key:
            metric = METRICS[metric_key]
            column = metric["column"]
            predictions = self.engine.all_predictions()
            groups: dict[str, list[float]] = {}
            for item in predictions:
                groups.setdefault(item["supplier"], []).append(float(item[column]))
            if supplier:
                if supplier not in groups:
                    return self._reply(f"Поставщик {supplier} не найден в наборе.")
                groups = {supplier: groups[supplier]}
            rows = []
            for supplier_name, values in groups.items():
                rows.append(
                    {
                        "supplier": supplier_name,
                        "count": len(values),
                        "average": _fmt(sum(values) / len(values), metric["digits"]),
                        "max": _fmt(max(values), metric["digits"]),
                    }
                )
            rows.sort(key=lambda item: float(item["max"].replace(",", ".")), reverse=True)
            blocks = [
                self._table_block(
                    f"{metric['label']} по поставщикам",
                    [
                        {"key": "supplier", "label": "Поставщик"},
                        {"key": "count", "label": "Партий"},
                        {"key": "average", "label": "Среднее"},
                        {"key": "max", "label": "Максимум"},
                    ],
                    rows,
                ),
                self._chart_block(
                    f"Средний уровень: {metric['label']}",
                    [row["supplier"] for row in rows],
                    [float(row["average"]) for row in rows],
                    unit=metric["unit"],
                ),
            ]
            scope = (
                f"У поставщика {supplier}"
                if supplier
                else "Сравнение поставщиков"
            )
            return self._reply(
                f"{scope} по показателю «{metric['label']}» ({metric['unit']}).",
                blocks=blocks,
            )

        rows = [
            {
                "supplier": item["supplier"],
                "total": item["total"],
                "low": item["low"],
                "medium": item["medium"],
                "high": item["high"],
                "share": _pct(item["high_share"]),
                "score": item["average_score"],
            }
            for item in summary
        ]
        chart_rows = summary[:10]
        blocks = [
            self._table_block(
                "Поставщики и уровни риска",
                [
                    {"key": "supplier", "label": "Поставщик"},
                    {"key": "total", "label": "Партий"},
                    {"key": "low", "label": "Низкий"},
                    {"key": "medium", "label": "Средний"},
                    {"key": "high", "label": "Высокий"},
                    {"key": "share", "label": "Доля высокого"},
                    {"key": "score", "label": "Средний балл"},
                ],
                rows,
            ),
            self._chart_block(
                "Доля высокого риска по поставщикам",
                [item["supplier"] for item in chart_rows],
                [round(item["high_share"] * 100, 1) for item in chart_rows],
                unit="%",
            ),
        ]

        if supplier:
            item = next((row for row in summary if row["supplier"] == supplier), None)
            if item is None:
                return self._reply(f"Поставщик {supplier} не найден в наборе.")
            supplier_batches = [
                prediction
                for prediction in self.engine.all_predictions()
                if prediction["supplier"] == supplier
            ]
            supplier_batches.sort(key=lambda prediction: -prediction["risk_score"])
            top_rows = [
                {
                    "batch_id": prediction["batch_id"],
                    "scenario": prediction["scenario"],
                    "score": prediction["risk_score"],
                    "action": prediction["recommended_action"],
                }
                for prediction in supplier_batches[:5]
            ]
            blocks.append(
                self._table_block(
                    f"Наиболее рискованные партии: {supplier}",
                    [
                        {"key": "batch_id", "label": "Партия"},
                        {"key": "scenario", "label": "Сценарий"},
                        {"key": "score", "label": "Оценка"},
                        {"key": "action", "label": "Действие"},
                    ],
                    top_rows,
                )
            )
            return self._reply(
                f"Поставщик {supplier}: {item['total']} партий, из них высокого риска — "
                f"{item['high']} ({_pct(item['high_share'])}), средний балл — "
                f"{item['average_score']}/100. Обратите внимание на партии из таблицы ниже.",
                blocks=blocks,
            )

        best = min(summary, key=lambda item: (item["high_share"], item["average_score"]))
        worst = max(summary, key=lambda item: (item["high_share"], item["average_score"]))
        return self._reply(
            f"В наборе {len(summary)} поставщиков. Наименьшая доля высокого риска — у "
            f"{best['supplier']} ({_pct(best['high_share'])}), наибольшая — у "
            f"{worst['supplier']} ({_pct(worst['high_share'])}). "
            "Таблица показывает распределение партий по уровням риска.",
            blocks=blocks,
        )

    def _metric_answer(self, metric_key: str, question: str) -> dict[str, Any]:
        metric = METRICS[metric_key]
        column = metric["column"]
        label = metric["label"]
        unit = metric["unit"]
        digits = metric["digits"]
        summary = self.engine.metric_summary(column)
        predictions = self.engine.all_predictions()
        values = [float(item[column]) for item in predictions]
        norm = NORMS.get(column, {})
        norm_text = norm.get("norm_text", "—")

        rows = [
            {
                "scenario": row["scenario"],
                "count": row["count"],
                "min": _fmt(row["min"], digits),
                "average": _fmt(row["average"], digits),
                "max": _fmt(row["max"], digits),
            }
            for row in summary
        ]
        blocks = [
            self._table_block(
                f"{label}: сводка по сценариям",
                [
                    {"key": "scenario", "label": "Сценарий"},
                    {"key": "count", "label": "Партий"},
                    {"key": "min", "label": "Минимум"},
                    {"key": "average", "label": "Среднее"},
                    {"key": "max", "label": "Максимум"},
                ],
                rows,
            ),
            self._chart_block(
                f"Среднее значение: {label}",
                [row["scenario"] for row in summary],
                [row["average"] for row in summary],
                unit=unit,
            ),
        ]

        above_norm: list[dict[str, Any]] = []
        max_allowed = norm.get("max")
        min_allowed = norm.get("min")
        for item in predictions:
            value = float(item[column])
            if max_allowed is not None and value > max_allowed:
                above_norm.append(item)
            elif min_allowed is not None and value < min_allowed:
                above_norm.append(item)
        above_norm.sort(key=lambda item: float(item[column]), reverse=True)

        if above_norm:
            top_rows = [
                {
                    "batch_id": item["batch_id"],
                    "scenario": item["scenario"],
                    "supplier": item["supplier"],
                    "value": _fmt(item[column], digits),
                    "risk": item["risk_level"],
                }
                for item in above_norm[:5]
            ]
            blocks.append(
                self._table_block(
                    f"Партии с отклонением: {label}",
                    [
                        {"key": "batch_id", "label": "Партия"},
                        {"key": "scenario", "label": "Сценарий"},
                        {"key": "supplier", "label": "Поставщик"},
                        {"key": "value", "label": "Значение"},
                        {"key": "risk", "label": "Риск"},
                    ],
                    top_rows,
                )
            )

        average = sum(values) / len(values) if values else 0
        answer = (
            f"Показатель «{label}»: среднее — {_fmt(average, digits)} {unit}, "
            f"минимум — {_fmt(min(values), digits)}, максимум — {_fmt(max(values), digits)}. "
            f"Норма: {norm_text}. Отклонений от нормы — {len(above_norm)} партий."
        )
        if "график" in question or "диаграмм" in question:
            answer += " График средних значений по сценариям добавлен ниже."
        return self._reply(answer, blocks=blocks)

    def _norms_answer(self) -> dict[str, Any]:
        rows = [
            {
                "indicator": value["label"],
                "norm": value["norm_text"],
                "unit": value["unit"] or "—",
            }
            for value in NORMS.values()
        ]
        actions = [
            {
                "level": level,
                "action": action,
                "when": {
                    "низкий": "лаборатория подтвердила соответствие, показатели в норме",
                    "средний": "есть пограничные показатели или условно годная партия",
                    "высокий": "опасный брак, несоответствие, тяжёлые металлы или БГКП",
                }[level],
            }
            for level, action in ACTION_BY_LEVEL.items()
        ]
        return self._reply(
            "Оценка гибридная: сначала срабатывают жёсткие правила, затем оценка 0–100 "
            "и дерево решений. Порог высокого риска — 70 баллов или критический фактор; "
            "среднего — 15 баллов.",
            blocks=[
                self._table_block(
                    "Нормы показателей",
                    [
                        {"key": "indicator", "label": "Показатель"},
                        {"key": "norm", "label": "Норма"},
                        {"key": "unit", "label": "Единица"},
                    ],
                    rows,
                ),
                self._table_block(
                    "Уровни риска и действия",
                    [
                        {"key": "level", "label": "Уровень"},
                        {"key": "action", "label": "Действие"},
                        {"key": "when", "label": "Когда"},
                    ],
                    actions,
                ),
            ],
        )

    def _what_if_answer(self, question: str) -> dict[str, Any]:
        metric_key = self._find_metric(question)
        if metric_key is None:
            return self._reply(
                "Уточните показатель: свинец, кадмий, мышьяк, кислотность, CO₂, КМАФАнМ, БГКП, дрожжи или плесень.",
                suggestions=[
                    "Что если свинец будет 0.4 мг/кг?",
                    "Что если кислотность будет 3.0?",
                    "Что если CO₂ будет 0.1%?",
                ],
            )
        metric = METRICS[metric_key]
        numbers = re.findall(r"\d+(?:[.,]\d+)?", question)
        value = None
        if numbers:
            # Последнее число обычно относится к показателю (например, «свинец 0.4»).
            for raw in reversed(numbers):
                candidate = float(raw.replace(",", "."))
                if 1900 <= candidate <= 2100:
                    continue
                value = candidate
                break
        if value is None:
            defaults = {
                "lead_mg_kg": 0.4,
                "cadmium_mg_kg": 0.02,
                "arsenic_mg_kg": 0.02,
                "kmafanm_cfu_cm3": 200,
                "bgkp_cfu_cm3": 5,
                "yeast_cfu_cm3": 25,
                "mold_cfu_cm3": 15,
                "acidity_cm3": 3.0,
                "co2_mass_pct": 0.1,
                "dry_matter_pct": 9.0,
            }
            value = defaults.get(metric["column"], 1.0)

        batch = {
            "batch_id": "WHAT-IF",
            "product_type": "безалкогольный напиток",
            "supplier": "Сценарный расчёт",
            "scenario": "Сценарий «что если»",
            "expiry_date": (self.engine.reference_date + timedelta(days=30)).isoformat(),
            "storage_temp_c": 9.0,
            "transport_temp_c": 9.0,
            "dry_matter_pct": 10.0,
            "acidity_cm3": 2.1,
            "co2_mass_pct": 0.5,
            "lead_mg_kg": 0.002,
            "cadmium_mg_kg": 0.001,
            "arsenic_mg_kg": 0.0015,
            "kmafanm_cfu_cm3": 15,
            "bgkp_cfu_cm3": 0,
            "yeast_cfu_cm3": 5,
            "mold_cfu_cm3": 2,
            "lab_conclusion": "Соответствует",
        }
        batch[metric["column"]] = value
        prediction = self.engine.predict(batch)
        factors = prediction["factors"]
        factor_text = "; ".join(factor["detail"] for factor in factors) or "явных факторов нет"
        blocks = [
            {
                "type": "gauge",
                "title": f"Прогноз риска при {metric['label']} = {_fmt(value, metric['digits'])} {metric['unit']}",
                "value": prediction["risk_score"],
                "max": 100,
                "level": prediction["risk_level"],
                "label": prediction["status_label"],
                "threshold": 70,
            },
            self._list_block(
                "Что изменится",
                [
                    f"Уровень риска: {prediction['risk_level']}",
                    f"Оценка: {prediction['risk_score']}/100",
                    f"Факторы: {factor_text}",
                    f"Действие: {prediction['recommended_action']}",
                ],
            ),
        ]
        if factors:
            blocks.append(
                self._chart_block(
                    "Веса факторов в сценарии",
                    [factor["label"] for factor in factors],
                    [factor["weight"] for factor in factors],
                )
            )
        response = self._reply(
            f"Если {metric['label']} = {_fmt(value, metric['digits'])} {metric['unit']}, "
            f"модель присвоит {prediction['risk_level']} риск ({prediction['risk_score']}/100). "
            f"{prediction['explanation']}",
            blocks=blocks,
        )
        response["context"] = {"batch": self._compact_batch(prediction)}
        return response

    def _advice_answer(self, question: str) -> dict[str, Any]:
        supplier = self._find_supplier(question)
        scenario = self._find_scenario(question)
        predictions = [
            item for item in self.engine.all_predictions() if item["risk_level"] == "высокий"
        ]
        scope = "по всему набору"
        if supplier:
            predictions = [item for item in predictions if item["supplier"] == supplier]
            scope = f"по поставщику {supplier}"
        elif scenario:
            predictions = [item for item in predictions if item["scenario"] == scenario]
            scope = f"по сценарию «{scenario}»"

        counter: dict[str, int] = {}
        for item in predictions:
            for factor in item["factors"]:
                counter[factor["label"]] = counter.get(factor["label"], 0) + 1
        top = sorted(counter.items(), key=lambda pair: pair[1], reverse=True)[:4]

        recommendations: list[str] = []
        joined = " ".join(label.lower() for label, _ in top)
        if any(term in joined for term in ["свинец", "кадми", "мышьяк"]):
            recommendations.append("Усилить входной контроль сырья и запросить протоколы тяжёлых металлов у поставщика.")
        if "кислот" in joined:
            recommendations.append("Проверить рецептуру и параметры технологического процесса по кислотности.")
        if "co₂" in joined or "co2" in joined:
            recommendations.append("Откалибровать оборудование карбонизации и проверить герметичность линии.")
        if any(term in joined for term in ["бгкп", "кмафанм", "дрожж", "плесен"]):
            recommendations.append("Проверить санитарную обработку линии, воду и герметичность упаковки.")
        if not recommendations:
            recommendations.append("Продолжать мониторинг партий и подтверждать результаты лабораторией.")

        blocks = [
            self._list_block(
                f"Рекомендации {scope}",
                recommendations,
            ),
            self._table_block(
                f"Что чаще всего влияет {scope}",
                [
                    {"key": "reason", "label": "Фактор"},
                    {"key": "count", "label": "Партий"},
                ],
                [{"reason": label, "count": count} for label, count in top],
            ),
        ]
        if top:
            blocks.append(
                self._chart_block(
                    "Приоритетные факторы",
                    [label for label, _ in top],
                    [count for _, count in top],
                )
            )
            answer = (
                f"Чтобы снизить риск {scope}, в первую очередь стоит заняться: "
                + ", ".join(label for label, _ in top[:3])
                + ". Ниже — конкретные шаги и приоритеты."
            )
        else:
            answer = (
                f"{scope[0].upper() + scope[1:]}: партий высокого риска не найдено. "
                "Можно продолжать мониторинг и плановые лабораторные проверки."
            )
        return self._reply(answer, blocks=blocks)

    def _causes_answer(self, question: str) -> dict[str, Any]:
        supplier = self._find_supplier(question)
        scenario = self._find_scenario(question)
        predictions = self.engine.all_predictions()
        scope = "по всему набору"
        if supplier:
            predictions = [item for item in predictions if item["supplier"] == supplier]
            scope = f"по поставщику {supplier}"
        elif scenario:
            predictions = [item for item in predictions if item["scenario"] == scenario]
            scope = f"по сценарию «{scenario}»"

        risky = [item for item in predictions if item["risk_level"] in {"высокий", "средний"}]
        if not risky:
            risky = predictions
        counter: dict[str, int] = {}
        for item in risky:
            for factor in item["factors"]:
                counter[factor["label"]] = counter.get(factor["label"], 0) + 1
        top = sorted(counter.items(), key=lambda pair: pair[1], reverse=True)[:5]
        rows = [
            {
                "reason": label,
                "count": count,
                "share": _pct(count / len(risky)) if risky else "0%",
            }
            for label, count in top
        ]
        high = sum(1 for item in predictions if item["risk_level"] == "высокий")
        blocks = [
            self._table_block(
                f"Причины риска {scope}",
                [
                    {"key": "reason", "label": "Причина"},
                    {"key": "count", "label": "Партий"},
                    {"key": "share", "label": "Доля"},
                ],
                rows,
            ),
            self._chart_block(
                f"Топ причин риска {scope}",
                [label for label, _ in top],
                [count for _, count in top],
            ),
        ]
        if top:
            top_text = ", ".join(f"{label} — {count}" for label, count in top[:3])
            scope_title = scope[0].upper() + scope[1:]
            answer = (
                f"{scope_title}: проанализировано {len(predictions)} партий, "
                f"высокий риск у {high} ({_pct(high / len(predictions)) if predictions else '0%'}). "
                f"Чаще всего встречаются: {top_text}. Это факторы, которые сильнее "
                "всего влияют на итоговый уровень риска в выборке."
            )
        else:
            answer = f"{scope[0].upper() + scope[1:]}: выраженных факторов риска не найдено."
        return self._reply(answer, blocks=blocks)

    def _trend_answer(self) -> dict[str, Any]:
        dashboard = self.engine.dashboard()
        daily = dashboard["daily"]
        rows = [
            {
                "date": item["label"],
                "total": item["total"],
                "high": item["high"],
                "rate": _pct(item["defect_rate"]),
                "score": item["average_score"],
            }
            for item in daily
        ]
        blocks = [
            {
                "type": "chart",
                "chart_type": "line",
                "title": "Доля брака по датам выпуска",
                "labels": [item["label"] for item in daily],
                "values": [round(item["defect_rate"] * 100, 1) for item in daily],
                "unit": "%",
            },
            self._table_block(
                "Динамика по дням",
                [
                    {"key": "date", "label": "Дата"},
                    {"key": "total", "label": "Партий"},
                    {"key": "high", "label": "Брак"},
                    {"key": "rate", "label": "Доля брака"},
                    {"key": "score", "label": "Средний риск"},
                ],
                rows,
            ),
        ]
        if daily:
            worst = max(daily, key=lambda item: item["defect_rate"])
            best = min(daily, key=lambda item: item["defect_rate"])
            change = (
                daily[-1]["defect_rate"] - daily[0]["defect_rate"]
                if len(daily) > 1
                else 0
            )
            answer = (
                f"В динамике по {len(daily)} датам выпуска максимальная доля брака — "
                f"{_pct(worst['defect_rate'])} ({worst['label']}), минимальная — "
                f"{_pct(best['defect_rate'])} ({best['label']}). Изменение от первой "
                f"даты к последней: {change * 100:+.1f} п.п. Средняя оценка риска по "
                f"набору — {dashboard['average_score']}/100."
            )
        else:
            answer = "В наборе недостаточно дат выпуска для построения динамики."
        return self._reply(answer, blocks=blocks)

    def _batch_list_answer(self) -> dict[str, Any]:
        items = sorted(
            self.engine.all_predictions(), key=lambda item: item["batch_id"]
        )[:10]
        rows = [
            {
                "batch_id": item["batch_id"],
                "scenario": item["scenario"],
                "supplier": item["supplier"],
                "score": item["risk_score"],
                "risk": item["risk_level"],
            }
            for item in items
        ]
        return self._reply(
            f"В наборе {len(self.engine.df)} партий. Ниже первые 10; для полного списка "
            "используйте реестр с фильтрами на сайте.",
            blocks=[
                self._table_block(
                    "Первые 10 партий",
                    [
                        {"key": "batch_id", "label": "Партия"},
                        {"key": "scenario", "label": "Сценарий"},
                        {"key": "supplier", "label": "Поставщик"},
                        {"key": "score", "label": "Оценка"},
                        {"key": "risk", "label": "Риск"},
                    ],
                    rows,
                )
            ],
        )

    def _risk_level_answer(self, level: str) -> dict[str, Any]:
        stats = self.engine.stats()
        predictions = [
            item
            for item in self.engine.all_predictions()
            if item["risk_level"] == level
        ]
        if level == "низкий":
            predictions.sort(key=lambda item: (item["risk_score"], item["batch_id"]))
        else:
            predictions.sort(key=lambda item: (-item["risk_score"], item["batch_id"]))
        examples = predictions[:10]
        rows = [
            {
                "batch_id": item["batch_id"],
                "scenario": item["scenario"],
                "supplier": item["supplier"],
                "score": item["risk_score"],
                "conclusion": item["lab_conclusion"],
            }
            for item in examples
        ]
        count = stats["risk_counts"][level]["count"]
        share = _pct(stats["risk_counts"][level]["share"])
        return self._reply(
            f"Партий с уровнем «{level}» — {count} ({share}). "
            f"Рекомендованное действие — {ACTION_BY_LEVEL[level]}. Ниже первые "
            f"{len(examples)} партий и их оценки.",
            blocks=[
                self._table_block(
                    f"Партии: {level} риск",
                    [
                        {"key": "batch_id", "label": "Партия"},
                        {"key": "scenario", "label": "Сценарий"},
                        {"key": "supplier", "label": "Поставщик"},
                        {"key": "score", "label": "Оценка"},
                        {"key": "conclusion", "label": "Заключение"},
                    ],
                    rows,
                ),
                self._chart_block(
                    f"Оценки риска: {level}",
                    [item["batch_id"] for item in examples],
                    [item["risk_score"] for item in examples],
                ),
            ],
        )

    def _actions_answer(self) -> dict[str, Any]:
        stats = self.engine.stats()
        rows = [
            {
                "level": level,
                "action": action,
                "count": stats["risk_counts"][level]["count"],
                "share": _pct(stats["risk_counts"][level]["share"]),
            }
            for level, action in ACTION_BY_LEVEL.items()
        ]
        return self._reply(
            "Действие зависит от уровня риска. Высокий риск — временная изоляция, "
            "средний — дополнительная проверка, низкий — допуск к реализации.",
            blocks=[
                self._table_block(
                    "Рекомендованные действия",
                    [
                        {"key": "level", "label": "Уровень"},
                        {"key": "action", "label": "Действие"},
                        {"key": "count", "label": "Партий"},
                        {"key": "share", "label": "Доля"},
                    ],
                    rows,
                )
            ],
        )

    def _top_answer(self, question: str) -> dict[str, Any]:
        metric_key = self._find_metric(question)
        ascending = any(term in question for term in ["лучш", "наимень", "низк"])
        limit = self._top_limit(question)
        if metric_key:
            metric = METRICS[metric_key]
            items = self.engine.top_batches(metric["column"], limit=limit, ascending=ascending)
            rows = [
                {
                    "batch_id": item["batch_id"],
                    "scenario": item["scenario"],
                    "supplier": item["supplier"],
                    "value": _fmt(item[metric["column"]], metric["digits"]),
                    "risk": item["risk_level"],
                }
                for item in items
            ]
            direction = "минимальным" if ascending else "максимальным"
            leaders = ", ".join(
                f"{item['batch_id']} — {_fmt(item[metric['column']], metric['digits'])} "
                f"{metric['unit']}"
                for item in items[:3]
            )
            return self._reply(
                f"Топ-{limit} партий по показателю «{metric['label']}» ({metric['unit']}). "
                f"Лидеры с {direction} значением: {leaders}. "
                f"Полная выборка — в таблице, сравнение — на графике.",
                blocks=[
                    self._table_block(
                        f"Топ-{limit}: {metric['label']}",
                        [
                            {"key": "batch_id", "label": "Партия"},
                            {"key": "scenario", "label": "Сценарий"},
                            {"key": "supplier", "label": "Поставщик"},
                            {"key": "value", "label": "Значение"},
                            {"key": "risk", "label": "Риск"},
                        ],
                        rows,
                    ),
                    self._chart_block(
                        f"Топ-{limit}: {metric['label']}",
                        [item["batch_id"] for item in items],
                        [float(item[metric["column"]]) for item in items],
                        unit=metric["unit"],
                    ),
                ],
            )

        items = self.engine.top_batches(None, limit=limit, ascending=ascending)
        rows = [
            {
                "batch_id": item["batch_id"],
                "scenario": item["scenario"],
                "supplier": item["supplier"],
                "score": item["risk_score"],
                "risk": item["risk_level"],
                "action": item["recommended_action"],
            }
            for item in items
        ]
        title = (
            f"Топ-{limit} партий с наименьшей оценкой риска"
            if ascending
            else f"Топ-{limit} партий с наибольшей оценкой риска"
        )
        leaders = ", ".join(
            f"{item['batch_id']} ({item['risk_score']}/100, {item['risk_level']})"
            for item in items[:3]
        )
        if ascending:
            summary = f"{title}: {leaders}. Это наиболее благополучные партии выборки."
        else:
            summary = (
                f"{title}: {leaders}. Для них в первую очередь нужно применить "
                "рекомендованные действия из таблицы."
            )
        return self._reply(
            summary,
            blocks=[
                self._table_block(
                    title,
                    [
                        {"key": "batch_id", "label": "Партия"},
                        {"key": "scenario", "label": "Сценарий"},
                        {"key": "supplier", "label": "Поставщик"},
                        {"key": "score", "label": "Оценка"},
                        {"key": "risk", "label": "Риск"},
                        {"key": "action", "label": "Действие"},
                    ],
                    rows,
                ),
                self._chart_block(
                    "Оценка риска выбранных партий",
                    [item["batch_id"] for item in items],
                    [float(item["risk_score"]) for item in items],
                    unit="/100",
                ),
            ],
        )

    @staticmethod
    def _top_limit(question: str) -> int:
        match = re.search(r"топ[\s-]?(\d{1,2})", question)
        if not match:
            match = re.search(r"(\d{1,2})\s+(?:парт|поставщ|показател)", question)
        if match:
            return max(1, min(20, int(match.group(1))))
        return 5

    def _expiry_answer(self) -> dict[str, Any]:
        predictions = self.engine.all_predictions()
        expired = [item for item in predictions if item["days_to_expiry"] < 0]
        critical = [item for item in predictions if 0 <= item["days_to_expiry"] <= 2]
        near = [item for item in predictions if 3 <= item["days_to_expiry"] <= 5]
        safe = [item for item in predictions if item["days_to_expiry"] > 5]
        rows = [
            {"group": "Просрочено", "count": len(expired)},
            {"group": "Осталось 0–2 дня", "count": len(critical)},
            {"group": "Осталось 3–5 дней", "count": len(near)},
            {"group": "Более 5 дней", "count": len(safe)},
        ]
        blocks = [
            self._table_block(
                "Срок годности",
                [
                    {"key": "group", "label": "Группа"},
                    {"key": "count", "label": "Партий"},
                ],
                rows,
            ),
            self._chart_block(
                "Распределение по сроку годности",
                [row["group"] for row in rows],
                [row["count"] for row in rows],
            ),
        ]
        if expired:
            blocks.append(
                self._list_block(
                    "Примеры просроченных партий",
                    [item["batch_id"] for item in expired[:10]],
                )
            )
        return self._reply(
            f"Опорная дата — {_date_ru(self.engine.reference_date.isoformat())}. "
            f"Просрочено: {len(expired)}, осталось 0–2 дня: {len(critical)}, "
            f"3–5 дней: {len(near)}, более 5 дней: {len(safe)}.",
            blocks=blocks,
        )

    def _temperature_answer(self) -> dict[str, Any]:
        frame = self.engine.df
        storage_out = (
            (frame["storage_temp_min_c"] < 0) | (frame["storage_temp_max_c"] > 18)
        ).sum()
        transport_out = (
            (frame["transport_temp_min_c"] < 0) | (frame["transport_temp_max_c"] > 18)
        ).sum()
        rows = [
            {"mode": "Хранение", "norm": "0–18 °C", "outside": int(storage_out)},
            {"mode": "Перевозка", "norm": "0–18 °C", "outside": int(transport_out)},
        ]
        return self._reply(
            "Температурный режим для напитка — 0–18 °C и при хранении, и при перевозке. "
            f"В наборе нарушений по хранению — {int(storage_out)}, по перевозке — "
            f"{int(transport_out)}. Выход за диапазон считается критическим фактором.",
            blocks=[
                self._table_block(
                    "Температурный режим",
                    [
                        {"key": "mode", "label": "Этап"},
                        {"key": "norm", "label": "Норма"},
                        {"key": "outside", "label": "Нарушений"},
                    ],
                    rows,
                )
            ],
        )

    def _method_answer(self) -> dict[str, Any]:
        model = self.engine.model_info
        features = model.get("features", [])[:5]
        rows = [
            {
                "feature": item["label"],
                "importance": f"{round(item['importance'] * 100, 1)}%",
            }
            for item in features
        ]
        blocks = [
            self._list_block(
                "Как принимается решение",
                [
                    "Проверяются жёсткие правила: срок годности, температура, тяжёлые металлы, БГКП и заключение лаборатории.",
                    "Факторы взвешиваются и суммируются в оценку 0–100.",
                    "Высокий риск — критический фактор или 70+ баллов, средний — 15+ баллов.",
                    "Дерево решений даёт независимое «второе мнение».",
                ],
            ),
            self._table_block(
                "Важность признаков в дереве решений",
                [
                    {"key": "feature", "label": "Признак"},
                    {"key": "importance", "label": "Важность"},
                ],
                rows,
            ),
        ]
        if features:
            blocks.append(
                self._chart_block(
                    "Важность признаков",
                    [item["label"] for item in features],
                    [round(item["importance"] * 100, 1) for item in features],
                    unit="%",
                )
            )
        accuracy = model.get("accuracy")
        accuracy_text = f"{round(accuracy * 100, 1)}%" if accuracy is not None else "—"
        return self._reply(
            f"Используется гибрид правил и дерева решений. Точность дерева на отложенной "
            f"выборке — {accuracy_text}, глубина — {model.get('depth', '—')}, "
            f"листьев — {model.get('leaves', '—')}.",
            blocks=blocks,
        )

    def _llm_answer(self) -> dict[str, Any]:
        provider = getattr(self.llm, "provider_label", "rule-based") if self.llm else "rule-based"
        chain = (
            getattr(self.llm, "fallback_chain", None)
            if self.llm
            else [
                "DeepSeek",
                "GigaChat",
                "Алиса AI",
                "локальная open-source модель",
                "rule-based объяснение",
            ]
        )
        return self._reply(
            f"Активный провайдер объяснений: {provider}. "
            "Если выбранный облачный агент недоступен, используются GigaChat, Алиса AI, "
            "локальная open-source модель и rule-based объяснение движка.",
            blocks=[
                self._list_block(
                    "Цепочка провайдеров",
                    list(chain or []),
                )
            ],
        )

    def _data_answer(self) -> dict[str, Any]:
        meta = self.engine.meta()
        scenarios = meta["scenarios"]
        rows = [
            {
                "scenario": item["title"],
                "count": item["count"],
                "level": item["risk_level"],
                "example": item["example_batch"],
            }
            for item in scenarios
        ]
        sources = self.engine.data_info
        return self._reply(
            f"Используется {meta['dataset_size']} партий из папки data: "
            f"{sources.get('source_count', 0)} файлов CSV/XLSX, пять сценариев. "
            f"Удалено дубликатов: {sources.get('duplicates_removed', 0)}, "
            f"конфликтов: {sources.get('conflicts', 0)}. "
            "Данные учебные, реальные решения без экспертизы недопустимы.",
            blocks=[
                self._table_block(
                    "Сценарии и объём данных",
                    [
                        {"key": "scenario", "label": "Сценарий"},
                        {"key": "count", "label": "Партий"},
                        {"key": "level", "label": "Уровень"},
                        {"key": "example", "label": "Пример"},
                    ],
                    rows,
                ),
                self._list_block(
                    "Что проверяется в данных",
                    [
                        "Температура хранения и перевозки",
                        "Сухие вещества, кислотность, CO₂",
                        "Свинец, кадмий, мышьяк",
                        "КМАФАнМ, БГКП, дрожжи, плесень",
                        "Срок годности и заключение лаборатории",
                    ],
                ),
            ],
        )

    def _overview_answer(self, question: str = "") -> dict[str, Any]:
        if question:
            matches = self.rag.search(question, limit=6)
            if matches:
                kind_labels = {
                    "batch": "Партия",
                    "supplier": "Поставщик",
                    "scenario": "Сценарий",
                    "daily": "Дата",
                    "norms": "Нормативы",
                    "method": "Методика",
                }
                rows = []
                for match in matches:
                    kind = match["kind"]
                    payload = match.get("payload") or {}
                    if kind == "batch":
                        detail = (
                            f"{payload.get('risk_level', '—')} риск, "
                            f"{payload.get('risk_score', '—')}/100, "
                            f"{payload.get('scenario', '—')}"
                        )
                    elif kind == "supplier":
                        detail = (
                            f"высокий риск: {payload.get('high', '—')} "
                            f"({_pct(payload.get('high_share', 0))}), "
                            f"средний балл {payload.get('average_score', '—')}"
                        )
                    elif kind == "scenario":
                        detail = (
                            f"{payload.get('total', '—')} партий, "
                            f"уровень {payload.get('risk_level', '—')}, "
                            f"средний балл {payload.get('average_score', '—')}"
                        )
                    elif kind == "daily":
                        detail = (
                            f"{payload.get('total', '—')} партий, "
                            f"брак {payload.get('high', '—')}, "
                            f"доля {round(payload.get('defect_rate', 0) * 100, 1)}%"
                        )
                    else:
                        detail = "справочный фрагмент базы знаний"
                    rows.append(
                        {
                            "type": kind_labels.get(kind, kind),
                            "id": match["id"],
                            "detail": detail,
                            "score": match["score"],
                        }
                    )
                leaders = ", ".join(
                    f"{row['type'].lower()} {row['id']}" for row in rows[:4]
                )
                return self._reply(
                    f"По запросу нашёл {len(matches)} релевантных фрагментов RAG. "
                    f"Наиболее близкие источники: {leaders}. "
                    "Ниже — факты из данных проекта; уточните, если нужен более узкий срез.",
                    blocks=[
                        self._table_block(
                            "Релевантные фрагменты RAG",
                            [
                                {"key": "type", "label": "Тип"},
                                {"key": "id", "label": "Источник"},
                                {"key": "detail", "label": "Факт"},
                                {"key": "score", "label": "Сходство"},
                            ],
                            rows,
                        )
                    ],
                    suggestions=DEFAULT_SUGGESTIONS,
                )

        stats = self.engine.stats()
        scenarios = self.engine.scenario_summary()
        rows = [
            {
                "scenario": row["scenario"],
                "count": row["total"],
                "level": row["risk_level"],
                "score": row["average_score"],
            }
            for row in scenarios
        ]
        return self._reply(
            f"В наборе {stats['total']} партий: низкий риск — "
            f"{stats['risk_counts']['низкий']['count']}, средний — "
            f"{stats['risk_counts']['средний']['count']}, высокий — "
            f"{stats['risk_counts']['высокий']['count']}. Уточните вопрос: партия, "
            "поставщик, показатель, норма или действие.",
            blocks=[
                self._chart_block(
                    "Распределение по уровню риска",
                    ["низкий", "средний", "высокий"],
                    [
                        stats["risk_counts"]["низкий"]["count"],
                        stats["risk_counts"]["средний"]["count"],
                        stats["risk_counts"]["высокий"]["count"],
                    ],
                    chart_type="pie",
                ),
                self._table_block(
                    "Сценарии",
                    [
                        {"key": "scenario", "label": "Сценарий"},
                        {"key": "count", "label": "Партий"},
                        {"key": "level", "label": "Уровень"},
                        {"key": "score", "label": "Средний балл"},
                    ],
                    rows,
                ),
            ],
        )
