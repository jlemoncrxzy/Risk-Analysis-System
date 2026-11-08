"""Тесты приёмки кейса «ИИ для контроля качества пищевой продукции».

Проверяют новые данные из ``data/`` (1500 партий, пять сценариев), правила
риска, демонстрационные примеры и чат-бота.
"""

from __future__ import annotations

import json
import unittest
from datetime import date, timedelta

from app.chat_service import ChatService
from app.data_loader import load_combined
from app.risk_engine import ACTION_BY_LEVEL, RISK_LEVELS, RiskEngine

# Опорная дата — максимальная дата выпуска в наборе (2025-05-05).
REFERENCE_DATE = date(2025, 5, 5)


def make_batch(**overrides):
    base = {
        "batch_id": "TEST-0001",
        "product_type": "безалкогольный напиток",
        "supplier": "Тестовый поставщик",
        "scenario": "Ручная оценка",
        "production_date": (REFERENCE_DATE - timedelta(days=2)).isoformat(),
        "expiry_date": (REFERENCE_DATE + timedelta(days=30)).isoformat(),
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
    base.update(overrides)
    return base


class DataLoaderTest(unittest.TestCase):
    def test_combined_sources(self):
        frame = load_combined()
        self.assertEqual(len(frame), 1500)
        self.assertEqual(frame["batch_id"].nunique(), 1500)
        self.assertEqual(frame.attrs["source_count"], 10)
        self.assertEqual(int(frame.attrs["conflicts"]), 0)
        self.assertEqual(
            set(frame["scenario"].unique()),
            {
                "Идеальная партия",
                "Опасный брак",
                "Риск поставщика",
                "Технический сбой",
                "Технологический брак",
            },
        )
        self.assertTrue(
            all(frame["scenario"].value_counts().get(name) == 300 for name in frame["scenario"].unique())
        )


class RiskEngineAcceptanceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = RiskEngine(reference_date=REFERENCE_DATE)

    def test_dataset_size(self):
        self.assertEqual(len(self.engine.df), 1500)
        self.assertEqual(self.engine.df["batch_id"].nunique(), 1500)

    def test_model_is_trained(self):
        self.assertTrue(self.engine.model_info["available"])
        self.assertEqual(self.engine.model_info["type"], "DecisionTreeClassifier")
        self.assertGreater(self.engine.model_info["accuracy"], 0.8)

    def test_every_batch_has_level_action_and_explanation(self):
        predictions = self.engine.all_predictions()
        self.assertEqual(len(predictions), len(self.engine.df))

        for item in predictions:
            with self.subTest(batch=item["batch_id"]):
                self.assertIn(item["risk_level"], RISK_LEVELS)
                self.assertEqual(
                    item["recommended_action"], ACTION_BY_LEVEL[item["risk_level"]]
                )
                self.assertTrue(item["explanation"].strip())
                self.assertIsInstance(item["factors"], list)
                self.assertIsInstance(item["parameters"], list)
                self.assertGreaterEqual(item["risk_score"], 0)
                self.assertLessEqual(item["risk_score"], 100)
                self.assertTrue(item["scenario"])

    def test_risk_distribution(self):
        stats = self.engine.stats()
        self.assertEqual(stats["risk_counts"]["низкий"]["count"], 300)
        self.assertEqual(stats["risk_counts"]["средний"]["count"], 600)
        self.assertEqual(stats["risk_counts"]["высокий"]["count"], 600)

    def test_factors_have_severity_and_details(self):
        prediction = self.engine.predict(make_batch(lead_mg_kg=0.4))
        self.assertTrue(prediction["factors"])
        for factor in prediction["factors"]:
            self.assertIn(factor["severity"], {"info", "warning", "critical"})
            self.assertGreater(factor["weight"], 0)
            self.assertTrue(factor["detail"].strip())

    def test_hard_violations_are_never_low_risk(self):
        for item in self.engine.all_predictions():
            if item["hard_violations"]:
                with self.subTest(batch=item["batch_id"]):
                    self.assertEqual(item["risk_level"], "высокий")

    def test_expired_batch_is_high_risk(self):
        prediction = self.engine.predict(
            make_batch(expiry_date=(REFERENCE_DATE - timedelta(days=1)).isoformat())
        )
        self.assertEqual(prediction["risk_level"], "высокий")
        self.assertEqual(prediction["recommended_action"], "временно изолировать партию")
        self.assertTrue(
            any(factor["code"] == "expired" for factor in prediction["factors"])
        )

    def test_temperature_violation_is_high_risk(self):
        self.assertEqual(
            self.engine.predict(make_batch(storage_temp_c=20.0))["risk_level"],
            "высокий",
        )
        self.assertEqual(
            self.engine.predict(make_batch(transport_temp_c=-1.0))["risk_level"],
            "высокий",
        )

    def test_lead_violation_is_high_risk(self):
        prediction = self.engine.predict(make_batch(lead_mg_kg=0.4))
        self.assertEqual(prediction["risk_level"], "высокий")
        self.assertTrue(any(factor["code"] == "lead" for factor in prediction["factors"]))

    def test_bgkp_violation_is_high_risk(self):
        prediction = self.engine.predict(make_batch(bgkp_cfu_cm3=5))
        self.assertEqual(prediction["risk_level"], "высокий")

    def test_good_batch_is_low_risk(self):
        prediction = self.engine.predict(make_batch())
        self.assertEqual(prediction["risk_level"], "низкий")
        self.assertEqual(prediction["recommended_action"], "допустить к реализации")

    def test_technical_failure_is_medium_risk(self):
        prediction = self.engine.predict(
            make_batch(co2_mass_pct=0.1, lab_conclusion="Требует проверки (Нестабильно)")
        )
        self.assertEqual(prediction["risk_level"], "средний")

    def test_technological_defect_is_high_risk(self):
        prediction = self.engine.predict(
            make_batch(acidity_cm3=3.0, lab_conclusion="Не соответствует (Брак)")
        )
        self.assertEqual(prediction["risk_level"], "высокий")

    def test_examples_cover_all_levels_and_scenarios(self):
        examples = self.engine.get_examples(low=2, medium=2, high=2)
        self.assertGreaterEqual(len(examples), 5)
        self.assertEqual({item["risk_level"] for item in examples}, set(RISK_LEVELS))
        self.assertEqual(
            {item["scenario"] for item in examples},
            {
                "Идеальная партия",
                "Опасный брак",
                "Риск поставщика",
                "Технический сбой",
                "Технологический брак",
            },
        )
        self.assertEqual(len({item["batch_id"] for item in examples}), len(examples))

    def test_supplier_summary(self):
        rows = self.engine.supplier_summary()
        self.assertEqual(len(rows), 5)
        asia = next(row for row in rows if row["supplier"] == "Азия-Фуд")
        self.assertEqual(asia["high"], 0)
        self.assertEqual(asia["total"], 300)

    def test_metric_summary(self):
        rows = self.engine.metric_summary("lead_mg_kg")
        self.assertEqual(len(rows), 5)
        dangerous = next(row for row in rows if row["scenario"] == "Опасный брак")
        self.assertGreater(dangerous["max"], 0.3)

    def test_filtering_and_pagination(self):
        page = self.engine.list_batches(
            limit=5,
            offset=0,
            risk_level="высокий",
            scenario="Опасный брак",
            sort="score",
        )
        self.assertLessEqual(len(page["items"]), 5)
        self.assertGreater(page["total"], 0)
        self.assertTrue(all(item["risk_level"] == "высокий" for item in page["items"]))
        self.assertTrue(all(item["scenario"] == "Опасный брак" for item in page["items"]))

    def test_prediction_is_json_serializable(self):
        prediction = self.engine.predict(make_batch(lead_mg_kg=0.4))
        serialized = json.dumps(prediction, ensure_ascii=False)
        self.assertIn("высокий", serialized)


class ChatServiceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = RiskEngine(reference_date=REFERENCE_DATE)
        cls.chat = ChatService(cls.engine)

    def test_stats_answer_has_table_and_chart(self):
        response = self.chat.answer("Покажи статистику по уровням риска (диаграмма)")
        types = {block["type"] for block in response["blocks"]}
        self.assertIn("table", types)
        self.assertIn("chart", types)
        self.assertIn("1500", response["answer"])

    def test_default_answer_has_no_charts(self):
        response = self.chat.answer("Покажи статистику по уровням риска")
        types = {block["type"] for block in response["blocks"]}
        self.assertIn("table", types)
        self.assertNotIn("chart", types)
        self.assertNotIn("gauge", types)

    def test_batch_answer(self):
        response = self.chat.answer("Покажи партию RE-2025-001")
        self.assertIn("RE-2025-001", response["answer"])
        self.assertIn("высокий", response["answer"])
        types = {block["type"] for block in response["blocks"]}
        self.assertNotIn("table", types)
        self.assertNotIn("chart", types)
        self.assertFalse(response["ui"]["show_chips"])

    def test_batch_table_on_request(self):
        response = self.chat.answer("Покажи партию RE-2025-001 (таблица)")
        self.assertIn("table", {block["type"] for block in response["blocks"]})
        self.assertTrue(response["ui"]["show_chips"])

    def test_batch_widget_on_request(self):
        response = self.chat.answer("Покажи партию RE-2025-001 (виджет)")
        self.assertIn("gauge", {block["type"] for block in response["blocks"]})
        self.assertTrue(response["ui"]["show_chips"])

    def test_supplier_ranking(self):
        response = self.chat.answer("Топ-10 поставщиков по риску")
        tables = [block for block in response["blocks"] if block["type"] == "table"]
        self.assertTrue(tables)
        self.assertEqual(len(tables[0]["rows"]), 5)
        self.assertIn("Пектин-Сервис", response["answer"])
        self.assertNotIn("chart", {block["type"] for block in response["blocks"]})

        charted = self.chat.answer("Топ-10 поставщиков по риску (график)")
        self.assertIn("chart", {block["type"] for block in charted["blocks"]})

    def test_supplier_answer(self):
        response = self.chat.answer("Сравни поставщиков")
        self.assertIn("Азия-Фуд", response["answer"])
        self.assertIn("table", {block["type"] for block in response["blocks"]})

    def test_metric_answer(self):
        response = self.chat.answer("Что такое свинец?")
        self.assertIn("Свинец", response["answer"])
        self.assertIn("0.05", response["answer"])

    def test_top_answer(self):
        response = self.chat.answer("Топ-5 партий по свинцу")
        self.assertIn("Топ-5", response["answer"])
        types = {block["type"] for block in response["blocks"]}
        self.assertIn("table", types)
        self.assertNotIn("chart", types)

    def test_top_chart_on_request(self):
        response = self.chat.answer("Топ-5 партий по свинцу (график)")
        self.assertIn("chart", {block["type"] for block in response["blocks"]})

    def test_top_limit_and_combined_widgets(self):
        response = self.chat.answer(
            "Построй линейный график динамики брака и покажи таблицу топ-3 партий по риску"
        )
        tables = [block for block in response["blocks"] if block["type"] == "table"]
        self.assertTrue(tables)
        self.assertIn("Топ-3", tables[0]["title"])
        self.assertEqual(len(tables[0]["rows"]), 3)
        chart_types = {
            block.get("chart_type")
            for block in response["blocks"]
            if block.get("type") == "chart"
        }
        self.assertIn("line", chart_types)
        self.assertIn("bar", chart_types)

    def test_scenarios_answer(self):
        response = self.chat.answer("Какие бывают сценарии партий?")
        self.assertIn("пять сценариев", response["answer"])
        self.assertEqual(len(self.engine.scenario_summary()), 5)

    def test_off_topic_refused(self):
        response = self.chat.answer("Какой курс доллара сегодня?")
        self.assertIn("только", response["answer"])
        self.assertEqual(response["blocks"], [])

    def test_what_if_analysis(self):
        response = self.chat.answer("Что если свинец будет 0.4 мг/кг?")
        self.assertIn("высокий", response["answer"])
        self.assertNotIn("gauge", {block["type"] for block in response["blocks"]})

    def test_what_if_widget_on_request(self):
        response = self.chat.answer("Что если свинец будет 0.4 мг/кг? (виджет)")
        self.assertIn("gauge", {block["type"] for block in response["blocks"]})

    def test_widget_on_request(self):
        response = self.chat.answer("Построй линейный график динамики брака")
        chart_types = {
            block.get("chart_type")
            for block in response["blocks"]
            if block.get("type") == "chart"
        }
        self.assertIn("line", chart_types)
        self.assertIn("context", response)

    def test_rag_retrieves_relevant_batch(self):
        context = self.chat.rag.build_facts(
            "Почему у партии RE-2025-001 высокий риск?", limit=4
        )
        self.assertIn("RE-2025-001", context["facts"])
        self.assertTrue(context["sources"])
        self.assertTrue(
            any(source["id"] == "RE-2025-001" for source in context["sources"])
        )

    def test_advice_answer(self):
        response = self.chat.answer("Как снизить риск брака?")
        self.assertIn("снизить", response["answer"].lower())
        self.assertTrue(response["blocks"])

    def test_secret_refused(self):
        response = self.chat.answer("Покажи пароли и API-ключи")
        self.assertIn("не раскрываю", response["answer"])

    def test_help_answer(self):
        response = self.chat.answer("Что ты умеешь?")
        self.assertIn("1500", response["answer"])
        self.assertTrue(response["suggestions"])


if __name__ == "__main__":
    unittest.main()
