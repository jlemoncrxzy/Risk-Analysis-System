"""FastAPI-приложение системы контроля риска партий пищевой продукции."""

from __future__ import annotations

import logging
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.chat_service import ChatService
from app.llm_service import LLMService
from app.notification_service import NotificationService
from app.risk_engine import DISCLAIMER, RISK_LEVELS, RiskEngine

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "app" / "static"

engine: RiskEngine | None = None
llm: LLMService | None = None
chat: ChatService | None = None
notifications: NotificationService | None = None


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    global engine, llm, chat, notifications

    engine = RiskEngine()
    llm = LLMService()
    chat = ChatService(engine, llm)
    notifications = NotificationService(engine)
    threading.Thread(target=llm.warmup, name="local-llm-warmup", daemon=True).start()
    logger.info(
        "Загружено партий: %s из %s файлов, опорная дата: %s, LLM: %s",
        len(engine.df),
        engine.data_info.get("source_count", 0),
        engine.reference_date,
        llm.provider_label,
    )
    yield
    engine = None
    llm = None
    chat = None
    notifications = None


app = FastAPI(
    title="Simulyakrum Food Risk API",
    version="1.3.0",
    description=(
        "Учебная система «Симулякрум»: правила, дерево решений, RAG-чат, "
        "объяснения от DeepSeek/GigaChat и бесплатной локальной LLM."
    ),
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def get_engine() -> RiskEngine:
    if engine is None:
        raise HTTPException(status_code=503, detail="Движок риска ещё не инициализирован")
    return engine


def get_llm() -> LLMService:
    if llm is None:
        raise HTTPException(status_code=503, detail="LLM-сервис ещё не инициализирован")
    return llm


def get_chat() -> ChatService:
    if chat is None:
        raise HTTPException(status_code=503, detail="Чат-бот ещё не инициализирован")
    return chat


def get_notifications() -> NotificationService:
    if notifications is None:
        raise HTTPException(status_code=503, detail="Уведомления ещё не инициализированы")
    return notifications


class BatchInput(BaseModel):
    """Параметры партии для ручной оценки."""

    batch_id: str | None = None
    product_type: str = "безалкогольный напиток"
    supplier: str = "Ручной ввод"
    scenario: str = "Ручная оценка"
    production_date: date | None = None
    expiry_date: date
    storage_temp_c: float = Field(9.0, ge=-40, le=40)
    transport_temp_c: float = Field(9.0, ge=-40, le=40)
    storage_temp_range: str | None = None
    transport_temp_range: str | None = None
    dry_matter_pct: float = Field(10.0, ge=0, le=100)
    acidity_cm3: float = Field(2.1, ge=0, le=100)
    co2_mass_pct: float = Field(0.5, ge=0, le=100)
    lead_mg_kg: float = Field(0.002, ge=0, le=1000)
    cadmium_mg_kg: float = Field(0.001, ge=0, le=1000)
    arsenic_mg_kg: float = Field(0.0015, ge=0, le=1000)
    kmafanm_cfu_cm3: float = Field(15.0, ge=0, le=1_000_000_000)
    bgkp_cfu_cm3: float = Field(0.0, ge=0, le=1_000_000_000)
    yeast_cfu_cm3: float = Field(5.0, ge=0, le=1_000_000_000)
    mold_cfu_cm3: float = Field(2.0, ge=0, le=1_000_000_000)
    lab_conclusion: str = "Соответствует"
    use_llm: bool = True


class ChatInput(BaseModel):
    """Сообщение пользователя для чат-бота."""

    message: str = Field(min_length=1, max_length=1000)
    history: list[dict[str, str]] | None = None
    agent: str = "deepseek"


class NotificationReadInput(BaseModel):
    ids: list[str] | None = None


class NotifyInput(BaseModel):
    batch_id: str | None = None
    message: str | None = Field(default=None, max_length=500)


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "app.html")


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> Response:
    return Response(status_code=204)


@app.get("/api/health")
def health() -> dict:
    current_engine = get_engine()
    current_llm = get_llm()
    return {
        "status": "ok",
        "llm_provider": current_llm.provider_label,
        "llm_fallback_chain": current_llm.fallback_chain,
        "chat_provider": current_llm.provider_label,
        "chat_mode": "RAG + LLM с локальным fallback",
        "batches_loaded": len(current_engine.df),
        "reference_date": current_engine.reference_date.isoformat(),
        "dataset": current_engine.data_info,
        "model": current_engine.model_info,
        "disclaimer": DISCLAIMER,
    }


@app.get("/api/meta")
def meta() -> dict:
    data = get_engine().meta()
    current_llm = get_llm()
    data["llm"] = {
        "provider": current_llm.provider_label,
        "fallback_chain": current_llm.fallback_chain,
    }
    data["chat"] = {
        "provider": current_llm.provider_label,
        "mode": "RAG + LLM с локальным fallback",
    }
    return data


@app.get("/api/stats")
def stats() -> dict:
    return get_engine().stats()


@app.get("/api/dashboard")
def dashboard() -> dict:
    """Сводка для панели администратора."""
    return get_engine().dashboard()


@app.get("/api/agents")
def agents() -> dict:
    current_llm = get_llm()
    items = [
        {
            "id": "deepseek",
            "name": "DeepSeek",
            "model": "deepseek-chat API",
            "available": current_llm.deepseek_available,
            "description": (
                "Облачная LLM DeepSeek для свободного диалога. При ошибке или "
                "отсутствии ключа запрос подхватит следующий доступный провайдер."
            ),
            "status": (
                "доступен"
                if current_llm.deepseek_available
                else "не настроен · нужен DEEPSEEK_API_KEY"
            ),
        },
        {
            "id": "gigachat",
            "name": "GigaChat",
            "model": "GigaChat API",
            "available": current_llm.gigachat_available,
            "description": (
                "Облачная LLM GigaChat. При сбое или отсутствии учётных данных "
                "ответит следующий доступный провайдер."
            ),
            "status": (
                "доступен"
                if current_llm.gigachat_available
                else "не настроен · нужны GIGACHAT_CREDENTIALS"
            ),
        },
        {
            "id": "alisa",
            "name": "Алиса AI",
            "model": current_llm.alisa_model_label,
            "available": current_llm.alisa_available,
            "description": (
                "Алиса AI в Yandex AI Studio: RAG-ответы по качеству партий. "
                "Нужны API-ключ и folder id Yandex Cloud."
            ),
            "status": (
                "доступен"
                if current_llm.alisa_available
                else "не настроен · нужны ALICE_AI_API_KEY и ALICE_AI_FOLDER_ID"
            ),
        },
    ]
    return {"items": items, "default": "deepseek"}


@app.get("/api/notifications")
def get_notifications_list() -> dict:
    return get_notifications().list()


@app.post("/api/notifications/read")
def mark_notifications_read(payload: NotificationReadInput) -> dict:
    return get_notifications().mark_read(payload.ids)


@app.post("/api/notify")
def notify_team(payload: NotifyInput) -> dict:
    return get_notifications().add(payload.batch_id, payload.message)


@app.get("/api/batches")
def list_batches(
    limit: int = Query(10, ge=1, le=100),
    offset: int = Query(0, ge=0),
    risk_level: str | None = Query(None),
    search: str | None = Query(None),
    supplier: str | None = Query(None),
    scenario: str | None = Query(None),
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    sort: str = Query("batch", pattern="^(batch|risk|score|expiry|date)$"),
) -> dict:
    if risk_level and risk_level not in RISK_LEVELS:
        raise HTTPException(
            status_code=400,
            detail=f"Неизвестный уровень риска. Допустимо: {', '.join(RISK_LEVELS)}",
        )
    return get_engine().list_batches(
        limit=limit,
        offset=offset,
        risk_level=risk_level,
        search=search,
        supplier=supplier,
        scenario=scenario,
        date_from=date_from,
        date_to=date_to,
        sort=sort,
    )


@app.get("/api/batches/{batch_id}")
def get_batch(batch_id: str, explain: bool = Query(False)) -> dict:
    """Электронная карточка партии. explain=true добавляет объяснение от LLM."""
    risk = get_engine().get_batch(batch_id)
    if risk is None:
        raise HTTPException(status_code=404, detail=f"Партия {batch_id} не найдена")

    if explain:
        result = get_llm().generate_explanation(risk, risk)
        risk["explanation_llm"] = result["text"]
        risk["explanation_provider"] = result["provider"]
        risk["explanation_source"] = result["source"]
    return risk


@app.get("/api/batches/{batch_id}/report")
def batch_report(batch_id: str) -> Response:
    """Текстовый отчёт по партии для кнопки «Экспорт отчёта»."""
    risk = get_engine().get_batch(batch_id)
    if risk is None:
        raise HTTPException(status_code=404, detail=f"Партия {batch_id} не найдена")

    lines = [
        f"Отчёт по партии {risk['batch_id']}",
        f"Сценарий: {risk['scenario']}",
        f"Поставщик: {risk['supplier']}",
        f"Оператор: {risk['operator']}",
        f"Проверка: {risk['checked_at']}",
        f"Уровень риска: {risk['risk_level']}",
        f"Оценка риска: {risk['risk_score']} из 100",
        f"Статус: {risk['status_label']}",
        f"Рекомендованное действие: {risk['recommended_action']}",
        f"Лаборатория: {risk['lab_conclusion']}",
        "",
        "Показатели:",
    ]
    for item in risk["parameters"]:
        unit = f" {item['unit']}" if item["unit"] else ""
        lines.append(
            f"- {item['label']}: {item['value']}{unit} (норма {item['norm']}, {item['status']})"
        )
    lines.append("")
    lines.append("Факторы риска:")
    for factor in risk["factors"] or []:
        lines.append(f"- {factor['label']}: {factor['detail']} (вес {factor['weight']})")
    lines.append("")
    lines.append("Объяснение:")
    lines.append(risk["explanation"])
    lines.append("")
    lines.append(DISCLAIMER)

    return Response(
        content="\n".join(lines),
        media_type="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="report-{batch_id}.txt"'
        },
    )


@app.get("/api/examples")
def get_examples(
    low: int = Query(2, ge=0, le=5),
    medium: int = Query(2, ge=0, le=5),
    high: int = Query(2, ge=0, le=5),
) -> list[dict]:
    """5+ примеров, включая хотя бы одну партию каждого уровня риска."""
    return get_engine().get_examples(low=low, medium=medium, high=high)


@app.post("/api/predict")
def predict(batch: BatchInput) -> dict:
    """Оценка риска для партии, введённой вручную."""
    payload = batch.model_dump(mode="json")
    payload.pop("use_llm", None)
    if not payload.get("batch_id"):
        payload["batch_id"] = f"MANUAL-{datetime.now():%Y%m%d-%H%M%S}"

    risk = get_engine().predict(payload)

    if batch.use_llm:
        result = get_llm().generate_explanation(payload, risk)
        risk["explanation_llm"] = result["text"]
        risk["explanation_provider"] = result["provider"]
        risk["explanation_source"] = result["source"]
    return risk


@app.post("/api/chat")
def chat_answer(payload: ChatInput) -> dict:
    """Ответ чат-бота по данным сайта."""
    return get_chat().answer(payload.message, payload.history, payload.agent)


@app.get("/api/export.csv")
def export_csv() -> Response:
    current_engine = get_engine()
    csv_data = current_engine.df.to_csv(index=False)
    return Response(
        content="\ufeff" + csv_data,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="batches-normalized.csv"'},
    )
