"use strict";

const RISK_CLASS = {
  "низкий": "low",
  "средний": "medium",
  "высокий": "high",
};

const state = {
  page: 0,
  pageSize: 10,
  total: 0,
  meta: null,
  health: null,
  filters: { search: "", risk: "", supplier: "", scenario: "", sort: "batch" },
  searchTimer: null,
};

const CHAT_DEFAULT_SUGGESTIONS = [
  "Покажи статистику по уровням риска",
  "Сравни поставщиков",
  "Топ-5 партий по свинцу",
  "Какие бывают сценарии партий?",
  "Что делать с опасным браком?",
  "Покажи партию RE-2025-001",
];

const chatState = {
  open: false,
  loading: false,
  history: [],
};

const $ = (id) => document.getElementById(id);

const ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ESCAPES[char]);
}

function riskClass(level) {
  return RISK_CLASS[level] || "low";
}

function formatDate(value) {
  if (!value) return "—";
  const parts = String(value).slice(0, 10).split("-");
  if (parts.length !== 3) return esc(value);
  return `${parts[2]}.${parts[1]}.${parts[0]}`;
}

function formatNumber(value, digits = 1) {
  const number = Number(value);
  return Number.isFinite(number) ? number.toFixed(digits) : "—";
}

function shortProvider(provider) {
  if (!provider) return "—";
  if (provider.startsWith("GigaChat")) return "GigaChat";
  if (provider.startsWith("local:")) return "локальная LLM";
  return "rule-based";
}

function toISODate(value) {
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, "0");
  const day = String(value.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  if (!response.ok) {
    let message = `HTTP ${response.status}`;
    try {
      const body = await response.json();
      if (body && body.detail) {
        message = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
      }
    } catch {
      /* тело ответа не JSON — оставляем HTTP-код */
    }
    throw new Error(message);
  }
  return response.json();
}

function fillSelect(select, values, placeholder) {
  if (!select) return;
  select.innerHTML = "";
  if (placeholder) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = placeholder;
    select.appendChild(option);
  }
  for (const value of values || []) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = value;
    select.appendChild(option);
  }
}

function setDefaultDates() {
  const now = new Date();
  const expiry = new Date();
  expiry.setDate(expiry.getDate() + 7);
  $("f-production").value = toISODate(now);
  $("f-expiry").value = toISODate(expiry);
}

/* ------------------------------------------------------------------ health */

async function loadHealth() {
  const badge = $("status-badge");
  try {
    state.health = await api("/api/health");
    const model = state.health.model && state.health.model.available
      ? `дерево решений ${Math.round(state.health.model.accuracy * 100)}%`
      : "правила";
    badge.textContent = `LLM: ${shortProvider(state.health.llm_provider)} · ${state.health.batches_loaded} партий · ${model}`;
    badge.className = "badge ok";
  } catch {
    badge.textContent = "Сервер недоступен";
    badge.className = "badge fail";
  }
}

/* -------------------------------------------------------------------- meta */

async function loadMeta() {
  try {
    const meta = await api("/api/meta");
    state.meta = meta;

    fillSelect($("filter-risk"), meta.risk_levels, "Все уровни");
    fillSelect($("filter-supplier"), meta.suppliers, "Все поставщики");
    fillSelect(
      $("filter-scenario"),
      (meta.scenarios || []).map((item) => item.title),
      "Все сценарии"
    );
    fillSelect($("f-product"), meta.product_types);
    fillSelect($("f-supplier"), [...(meta.suppliers || []), "Ручной ввод"]);
    $("f-supplier").value = "Ручной ввод";

    renderModelInfo(meta.model || {});
    renderLlmInfo(meta);
  } catch (error) {
    fillSelect($("filter-risk"), ["низкий", "средний", "высокий"], "Все уровни");
    fillSelect($("filter-supplier"), [], "Все поставщики");
    fillSelect($("filter-scenario"), [], "Все сценарии");
    fillSelect($("f-product"), ["безалкогольный напиток"]);
    fillSelect($("f-supplier"), ["Ручной ввод"]);
    $("model-info").textContent = `Метаданные недоступны: ${error.message}`;
    $("llm-info").textContent = `Метаданные недоступны: ${error.message}`;
  }
}

function renderModelInfo(model) {
  const container = $("model-info");
  if (!container) return;
  if (!model.available) {
    container.innerHTML = `<p>Модель недоступна, используется только rule-based логика.</p>
      <p class="muted small">${esc(model.reason || "")}</p>`;
    return;
  }
  const top = (model.features || [])
    .slice(0, 3)
    .map((feature) => `${esc(feature.label)} — ${Math.round(feature.importance * 100)}%`)
    .join(", ");
  container.innerHTML = `
    <p><strong>Accuracy:</strong> ${(model.accuracy * 100).toFixed(1)}% ·
       <strong>глубина:</strong> ${model.depth} ·
       <strong>листьев:</strong> ${model.leaves}</p>
    <p><strong>Важные признаки:</strong> ${top || "—"}</p>`;
}

function renderLlmInfo(meta) {
  const container = $("llm-info");
  if (!container) return;
  const llm = meta.llm || {};
  const chain = (llm.fallback_chain || []).map((item) => esc(item)).join(" → ");
  container.innerHTML = `
    <p><strong>Активный провайдер:</strong> ${esc(llm.provider || "—")}</p>
    <p><strong>Цепочка:</strong> ${chain || "—"}</p>`;
}

/* ------------------------------------------------------------------- stats */

async function loadStats() {
  const grid = $("stat-grid");
  try {
    const stats = await api("/api/stats");
    const counts = stats.risk_counts || {};
    const cards = [
      {
        label: "Всего партий",
        value: stats.total,
        hint: `опорная дата ${formatDate(stats.reference_date)}`,
        cls: "neutral",
      },
      {
        label: "Низкий риск",
        value: counts["низкий"]?.count ?? 0,
        hint: `${Math.round((counts["низкий"]?.share ?? 0) * 100)}% · допуск к реализации`,
        cls: "low",
      },
      {
        label: "Средний риск",
        value: counts["средний"]?.count ?? 0,
        hint: `${Math.round((counts["средний"]?.share ?? 0) * 100)}% · доп. проверка`,
        cls: "medium",
      },
      {
        label: "Высокий риск",
        value: counts["высокий"]?.count ?? 0,
        hint: `${Math.round((counts["высокий"]?.share ?? 0) * 100)}% · изоляция`,
        cls: "high",
      },
    ];

    grid.innerHTML = cards
      .map(
        (card) => `<article class="stat-card ${card.cls}">
          <span class="stat-label">${esc(card.label)}</span>
          <strong class="stat-value">${card.value}</strong>
          <span class="stat-hint">${esc(card.hint)}</span>
        </article>`
      )
      .join("");

    renderDistribution(counts, stats);

    const modelLabel = stats.model && stats.model.available
      ? `дерево решений (accuracy ${(stats.model.accuracy * 100).toFixed(1)}%)`
      : "только правила";
    const scenarioCount = Object.keys(stats.scenario_counts || {}).length;
    $("overview-subtitle").textContent =
      `${stats.total} партий · ${scenarioCount} сценариев · ${stats.suppliers_count || 0} поставщиков · ` +
      `опорная дата ${formatDate(stats.reference_date)} · ${modelLabel}`;
  } catch (error) {
    grid.innerHTML = `<article class="stat-card neutral"><span class="stat-label">Ошибка</span><strong class="stat-value">—</strong><span class="stat-hint">${esc(error.message)}</span></article>`;
  }
}

function renderDistribution(counts, stats) {
  const container = $("distribution");
  container.innerHTML = ["низкий", "средний", "высокий"]
    .map((level) => {
      const item = counts[level] || { count: 0, share: 0 };
      const cls = riskClass(level);
      return `<div class="dist-row risk-${cls}">
        <span class="dist-label">${esc(level)}</span>
        <div class="dist-track"><div class="dist-fill" style="width:${Math.max(2, Math.round(item.share * 100))}%"></div></div>
        <span class="dist-value">${item.count} · ${Math.round(item.share * 100)}%</span>
      </div>`;
    })
    .join("");

  $("distribution-note").textContent =
    `Средняя оценка риска: ${stats.average_score}/100 · жёстких нарушений: ${stats.hard_violations}`;
}

/* ---------------------------------------------------------------- examples */

async function loadExamples() {
  const container = $("examples-container");
  try {
    const examples = await api("/api/examples?low=2&medium=2&high=2");
    if (!examples.length) {
      container.innerHTML = '<p class="muted">Примеры не найдены.</p>';
      return;
    }
    container.innerHTML = examples.map(renderExampleCard).join("");
  } catch (error) {
    container.innerHTML = `<p class="muted">Ошибка загрузки примеров: ${esc(error.message)}</p>`;
  }
}

function renderExampleCard(item) {
  const cls = riskClass(item.risk_level);
  const topFactors = [...(item.factors || [])]
    .sort((left, right) => right.weight - left.weight)
    .slice(0, 3);

  return `<article class="example-card risk-${cls}">
    <div class="example-top">
      <div>
        <span class="batch-id">${esc(item.batch_id)}</span>
        <h3>${esc(item.scenario)}</h3>
        <p class="muted small">${esc(item.product_type)} · ${esc(item.supplier)}</p>
      </div>
      <span class="risk-tag ${cls}">${esc(item.risk_level)}</span>
    </div>

    <div class="mini-score">
      <span>Оценка риска</span>
      <strong>${item.risk_score}<small>/100</small></strong>
    </div>

    <div class="chips">
      ${topFactors.length
        ? topFactors.map((factor) => `<span class="chip ${factor.severity}">${esc(factor.label)}</span>`).join("")
        : '<span class="chip ok">отклонений нет</span>'}
    </div>

    <p class="example-explanation">${esc(item.explanation)}</p>

    <div class="example-footer">
      <span class="action-text">${esc(item.recommended_action)}</span>
      <button class="btn btn-small" type="button" data-batch="${esc(item.batch_id)}">Карточка партии</button>
    </div>
  </article>`;
}

/* ---------------------------------------------------------------- registry */

async function loadBatches() {
  const tbody = document.querySelector("#batches-table tbody");
  const params = new URLSearchParams({
    limit: String(state.pageSize),
    offset: String(state.page * state.pageSize),
    sort: state.filters.sort,
  });
  if (state.filters.risk) params.set("risk_level", state.filters.risk);
  if (state.filters.supplier) params.set("supplier", state.filters.supplier);
  if (state.filters.scenario) params.set("scenario", state.filters.scenario);
  if (state.filters.search.trim()) params.set("search", state.filters.search.trim());

  try {
    const data = await api(`/api/batches?${params.toString()}`);
    state.total = data.total;

    const pages = Math.max(1, Math.ceil(data.total / state.pageSize));
    if (state.page >= pages && state.page > 0) {
      state.page = pages - 1;
      return loadBatches();
    }

    renderRows(tbody, data.items);
    updatePagination(data);
  } catch (error) {
    tbody.innerHTML = `<tr><td colspan="11" class="muted">Ошибка загрузки: ${esc(error.message)}</td></tr>`;
  }
}

function renderRows(tbody, items) {
  if (!items.length) {
    tbody.innerHTML = '<tr><td colspan="11" class="muted">Ничего не найдено. Измените фильтры.</td></tr>';
    return;
  }

  tbody.innerHTML = items
    .map((item) => {
      const cls = riskClass(item.risk_level);
      return `<tr class="batch-row risk-${cls}" tabindex="0" data-batch="${esc(item.batch_id)}">
        <td class="mono">${esc(item.batch_id)}</td>
        <td>${esc(item.scenario)}</td>
        <td>${esc(item.supplier)}</td>
        <td>${item.days_to_expiry}</td>
        <td>${formatNumber(item.acidity_cm3)}</td>
        <td>${formatNumber(item.co2_mass_pct, 2)}%</td>
        <td>${formatNumber(item.lead_mg_kg, 4)}</td>
        <td>${formatNumber(item.kmafanm_cfu_cm3, 0)}</td>
        <td><span class="score-pill ${cls}">${item.risk_score}</span></td>
        <td><span class="risk-tag ${cls}">${esc(item.risk_level)}</span></td>
        <td class="action-cell">${esc(item.recommended_action)}</td>
      </tr>`;
    })
    .join("");
}

function updatePagination(data) {
  const from = data.total ? state.page * state.pageSize + 1 : 0;
  const to = Math.min((state.page + 1) * state.pageSize, data.total);
  const pages = Math.max(1, Math.ceil(data.total / state.pageSize));

  $("table-info").textContent = `Показано ${from}–${to} из ${data.total}`;
  $("page-indicator").textContent = `Страница ${state.page + 1} из ${pages}`;
  $("prev-page").disabled = state.page <= 0;
  $("next-page").disabled = state.page >= pages - 1;
}

/* ------------------------------------------------------------------- card */

function renderBatchCard(item, options = {}) {
  const cls = riskClass(item.risk_level);
  const factors = [...(item.factors || [])].sort((left, right) => right.weight - left.weight);
  const maxWeight = Math.max(1, ...factors.map((factor) => factor.weight));
  const parameters = item.parameters || [];
  const hardViolations = item.hard_violations || [];
  const model = item.model;
  const heading = options.modal ? "h2" : "h3";
  const headingId = options.modal ? 'id="modal-title"' : "";

  const llmBlock = item.explanation_llm
    ? `<div class="llm-block">
        <div class="llm-head">
          <span class="pill">LLM</span>
          <span class="muted small">${esc(item.explanation_provider || "")}</span>
        </div>
        <p>${esc(item.explanation_llm)}</p>
      </div>`
    : options.llmButton
      ? `<button class="btn btn-ghost btn-small" type="button" data-llm="${esc(item.batch_id)}">Сгенерировать объяснение через LLM</button>`
      : "";

  return `<article class="batch-card risk-${cls}">
    <header class="batch-card-head">
      <div>
        <p class="eyebrow">${esc(item.scenario || "Электронная карточка партии")}</p>
        <${heading} ${headingId}>${esc(item.batch_id)}</${heading}>
        <p class="muted">${esc(item.product_type)} · ${esc(item.supplier)}</p>
      </div>
      <div class="score-block">
        <div class="score-ring" style="--score:${Math.max(0, Math.min(100, item.risk_score))}">
          <span>${item.risk_score}</span>
        </div>
        <span class="risk-tag ${cls}">${esc(item.risk_level)}</span>
        <span class="muted small">оценка риска</span>
      </div>
    </header>

    <div class="action-callout ${cls}">
      <strong>Рекомендованное действие:</strong> ${esc(item.recommended_action)}
    </div>

    ${item.scenario_description
      ? `<p class="muted small scenario-description">${esc(item.scenario_description)}</p>`
      : ""}

    ${hardViolations.length
      ? `<div class="hard-alert"><strong>Жёсткие нарушения:</strong> ${hardViolations.map((value) => esc(value)).join("; ")}</div>`
      : ""}

    <div class="batch-grid">
      <div class="card-block">
        <h4>Параметры партии</h4>
        <div class="param-list">
          ${parameters
            .map(
              (parameter) => `<div class="param-row status-${parameter.status}">
                <span class="param-label">${esc(parameter.label)}</span>
                <span class="param-value">${esc(parameter.value)}${parameter.unit ? ` ${esc(parameter.unit)}` : ""}</span>
                <span class="param-norm">норма: ${esc(parameter.norm)}</span>
              </div>`
            )
            .join("")}
        </div>
      </div>

      <div class="card-block">
        <h4>Факторы риска</h4>
        ${factors.length
          ? `<ul class="factor-list">${factors
              .map(
                (factor) => `<li class="factor ${factor.severity}">
                  <div class="factor-top">
                    <span>${esc(factor.label)}</span>
                    <span class="factor-weight">${factor.weight}</span>
                  </div>
                  <div class="factor-bar"><i style="width:${Math.round((factor.weight / maxWeight) * 100)}%"></i></div>
                  <p class="factor-detail">${esc(factor.detail)}</p>
                </li>`
              )
              .join("")}</ul>`
          : '<p class="muted">Явных факторов риска не выявлено.</p>'}
      </div>
    </div>

    <div class="card-block">
      <h4>Объяснение</h4>
      <p class="explanation">${esc(item.explanation)}</p>
      ${llmBlock}
    </div>

    <div class="card-meta">
      <span><strong>Сценарий:</strong> ${esc(item.scenario)}</span>
      <span><strong>Выпуск:</strong> ${formatDate(item.production_date)}</span>
      <span><strong>Годен до:</strong> ${formatDate(item.expiry_date)}</span>
      <span><strong>Осталось:</strong> ${item.days_to_expiry} дн.</span>
      <span><strong>Лаборатория:</strong> ${esc(item.lab_conclusion)}</span>
      ${model
        ? `<span><strong>Дерево решений:</strong> ${esc(model.risk_level)} · ${Math.round((model.confidence || 0) * 100)}%${model.agrees === false ? " · расхождение с правилами" : ""}</span>`
        : ""}
    </div>
  </article>`;
}

async function openBatchCard(batchId) {
  const modal = $("modal");
  const content = $("modal-content");
  content.innerHTML = `<div class="modal-loading"><div class="spinner" aria-hidden="true"></div><p class="muted">Загрузка карточки ${esc(batchId)}…</p></div>`;
  modal.hidden = false;
  document.body.classList.add("modal-open");

  try {
    const item = await api(`/api/batches/${encodeURIComponent(batchId)}`);
    content.innerHTML = renderBatchCard(item, { modal: true, llmButton: true });
  } catch (error) {
    content.innerHTML = `<div class="empty-state"><p>Не удалось загрузить карточку: ${esc(error.message)}</p></div>`;
  }
}

function closeModal() {
  $("modal").hidden = true;
  document.body.classList.remove("modal-open");
  $("modal-content").innerHTML = "";
}

async function requestLlm(button) {
  const batchId = button.getAttribute("data-llm");
  if (!batchId) return;

  button.disabled = true;
  button.textContent = "Генерация…";
  try {
    const item = await api(`/api/batches/${encodeURIComponent(batchId)}?explain=true`);
    $("modal-content").innerHTML = renderBatchCard(item, { modal: true, llmButton: false });
  } catch (error) {
    button.disabled = false;
    button.textContent = "Сгенерировать объяснение через LLM";
    const note = document.createElement("p");
    note.className = "form-error";
    note.textContent = `Ошибка LLM: ${error.message}`;
    button.after(note);
  }
}

/* ------------------------------------------------------------------- form */

function showFormError(message) {
  const container = $("form-error");
  container.textContent = message;
  container.hidden = false;
}

function numberValue(id) {
  const value = Number.parseFloat($(id).value);
  return Number.isFinite(value) ? value : 0;
}

async function submitForm(event) {
  event.preventDefault();
  $("form-error").hidden = true;

  const form = $("check-form");
  if (!form.reportValidity()) {
    showFormError("Проверьте обязательные поля формы.");
    return;
  }

  const payload = {
    batch_id: $("f-batch-id").value.trim() || null,
    product_type: $("f-product").value,
    supplier: $("f-supplier").value,
    production_date: $("f-production").value || null,
    expiry_date: $("f-expiry").value,
    storage_temp_c: numberValue("f-storage"),
    transport_temp_c: numberValue("f-transport"),
    dry_matter_pct: numberValue("f-dry"),
    acidity_cm3: numberValue("f-acidity"),
    co2_mass_pct: numberValue("f-co2"),
    lead_mg_kg: numberValue("f-lead"),
    cadmium_mg_kg: numberValue("f-cadmium"),
    arsenic_mg_kg: numberValue("f-arsenic"),
    kmafanm_cfu_cm3: numberValue("f-kmafanm"),
    bgkp_cfu_cm3: numberValue("f-bgkp"),
    yeast_cfu_cm3: numberValue("f-yeast"),
    mold_cfu_cm3: numberValue("f-mold"),
    lab_conclusion: $("f-lab").value,
    use_llm: $("f-use-llm").checked,
  };

  const submitButton = form.querySelector('button[type="submit"]');
  submitButton.disabled = true;
  submitButton.textContent = "Оценка…";

  try {
    const result = await api("/api/predict", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    $("check-result").innerHTML = renderBatchCard(result, { llmButton: false });
    $("check-result").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    showFormError(`Не удалось получить оценку: ${error.message}`);
  } finally {
    submitButton.disabled = false;
    submitButton.textContent = "Оценить риск";
  }
}

function resetForm() {
  $("check-form").reset();
  setDefaultDates();
  $("f-supplier").value = "Ручной ввод";
  $("form-error").hidden = true;
  $("check-result").innerHTML = `<div class="empty-state">
    <div class="empty-icon" aria-hidden="true">✓</div>
    <p>Заполните параметры партии и нажмите «Оценить риск».</p>
    <p class="muted small">Карточка с факторами риска появится здесь.</p>
  </div>`;
}

/* ------------------------------------------------------------------ chat */

function openChat() {
  const panel = $("chat-panel");
  if (!panel.classList.contains("open")) {
    panel.classList.add("open");
    chatState.open = true;
  }
  window.setTimeout(scrollChat, 80);
}

function closeChat() {
  $("chat-panel").classList.remove("open");
  chatState.open = false;
}

function scrollChat() {
  const messages = $("chat-messages");
  messages.scrollTop = messages.scrollHeight;
}

function renderChatSuggestions(items) {
  const container = $("chat-suggestions");
  const list = (items && items.length ? items : CHAT_DEFAULT_SUGGESTIONS).slice(0, 6);
  container.innerHTML = list
    .map(
      (item) =>
        `<button class="chat-chip" type="button" data-chat-suggestion="${esc(item)}">${esc(item)}</button>`
    )
    .join("");
}

function chartColor(label, index) {
  const text = String(label || "").toLowerCase();
  if (text.includes("низк")) return "#16a34a";
  if (text.includes("средн")) return "#d97706";
  if (text.includes("высок")) return "#dc2626";
  const palette = ["#2563eb", "#0ea5e9", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6", "#ec4899"];
  return palette[index % palette.length];
}

function formatChartValue(value, unit) {
  const number = Number(value);
  if (!Number.isFinite(number)) return esc(value);
  let text;
  if (Math.abs(number) >= 100) text = number.toFixed(0);
  else if (Math.abs(number) >= 1) text = number.toFixed(1).replace(/\.0$/, "");
  else text = number.toFixed(4).replace(/0+$/, "").replace(/\.$/, "");
  return unit ? `${text} ${esc(unit)}` : text;
}

function renderChatChart(block) {
  const labels = block.labels || [];
  const values = (block.values || []).map((value) => Number(value) || 0);
  const unit = block.unit || "";

  if (block.chart_type === "pie") {
    const total = values.reduce((sum, value) => sum + value, 0) || 1;
    let cursor = 0;
    const stops = values.map((value, index) => {
      const start = cursor;
      cursor += (value / total) * 100;
      const color = chartColor(labels[index], index);
      return `${color} ${start.toFixed(2)}% ${cursor.toFixed(2)}%`;
    });
    const legend = labels
      .map((label, index) => {
        const share = Math.round((values[index] / total) * 100);
        return `<li><i style="background:${chartColor(label, index)}"></i><span>${esc(label)}</span><b>${values[index]} · ${share}%</b></li>`;
      })
      .join("");
    return `<div class="chat-block"><h4>${esc(block.title || "График")}</h4>
      <div class="chat-pie-wrap">
        <div class="chat-pie" style="background:conic-gradient(${stops.join(", ")})"></div>
        <ul class="chat-legend">${legend}</ul>
      </div>
    </div>`;
  }

  const max = Math.max(1, ...values);
  const rows = labels
    .map((label, index) => {
      const value = values[index] || 0;
      const width = Math.max(2, Math.round((value / max) * 100));
      return `<div class="chat-bar-row">
        <span class="chat-bar-label">${esc(label)}</span>
        <div class="chat-bar-track"><i style="width:${width}%;background:${chartColor(label, index)}"></i></div>
        <b class="chat-bar-value">${formatChartValue(value, unit)}</b>
      </div>`;
    })
    .join("");
  return `<div class="chat-block"><h4>${esc(block.title || "График")}</h4><div class="chat-bars">${rows}</div></div>`;
}

function renderChatBlocks(blocks) {
  return (blocks || [])
    .map((block) => {
      if (block.type === "text") {
        return `<p class="chat-block-text">${esc(block.text)}</p>`;
      }
      if (block.type === "list") {
        const items = (block.items || [])
          .map((item) => `<li>${esc(item)}</li>`)
          .join("");
        return `<div class="chat-block"><h4>${esc(block.title || "Список")}</h4><ul class="chat-list">${items}</ul></div>`;
      }
      if (block.type === "table") {
        const columns = block.columns || [];
        const header = columns
          .map((column) => `<th>${esc(column.label)}</th>`)
          .join("");
        const rows = (block.rows || [])
          .map(
            (row) =>
              `<tr>${columns
                .map((column) => `<td>${esc(row[column.key])}</td>`)
                .join("")}</tr>`
          )
          .join("");
        return `<div class="chat-block"><h4>${esc(block.title || "Таблица")}</h4>
          <div class="chat-table-wrap"><table class="chat-table">
            <thead><tr>${header}</tr></thead><tbody>${rows}</tbody>
          </table></div>
        </div>`;
      }
      if (block.type === "chart") {
        return renderChatChart(block);
      }
      return "";
    })
    .join("");
}

function appendUserMessage(text) {
  const container = $("chat-messages");
  const message = document.createElement("div");
  message.className = "chat-message user";
  message.innerHTML = `<div class="chat-bubble">${esc(text)}</div>`;
  container.appendChild(message);
  scrollChat();
}

function appendBotText(text) {
  const container = $("chat-messages");
  const message = document.createElement("div");
  message.className = "chat-message bot";
  message.innerHTML = `<div class="chat-bubble"><p class="chat-text">${esc(text)}</p></div>`;
  container.appendChild(message);
  scrollChat();
}

function appendBotMessage(response) {
  const container = $("chat-messages");
  const message = document.createElement("div");
  message.className = "chat-message bot";
  message.innerHTML = `<div class="chat-bubble">
      <p class="chat-text">${esc(response.answer)}</p>
      ${renderChatBlocks(response.blocks)}
    </div>`;
  container.appendChild(message);
  scrollChat();
}

function appendTyping() {
  const container = $("chat-messages");
  const message = document.createElement("div");
  message.id = "chat-typing";
  message.className = "chat-message bot";
  message.innerHTML = '<div class="chat-bubble typing"><span></span><span></span><span></span></div>';
  container.appendChild(message);
  scrollChat();
}

function removeTyping() {
  const typing = $("chat-typing");
  if (typing) typing.remove();
}

function setChatLoading(loading) {
  chatState.loading = loading;
  const button = $("chat-form").querySelector('button[type="submit"]');
  button.disabled = loading;
  button.textContent = loading ? "Думаю…" : "Спросить";
}

async function sendChat(rawMessage) {
  const message = String(rawMessage || "").trim();
  if (!message || chatState.loading) return;

  openChat();
  $("chat-input").value = "";
  appendUserMessage(message);
  chatState.history.push({ role: "user", content: message });

  setChatLoading(true);
  appendTyping();

  try {
    const response = await api("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message,
        history: chatState.history.slice(-6),
      }),
    });
    removeTyping();
    appendBotMessage(response);
    chatState.history.push({ role: "assistant", content: response.answer });
    renderChatSuggestions(response.suggestions || CHAT_DEFAULT_SUGGESTIONS);
  } catch (error) {
    removeTyping();
    appendBotText(`Не удалось получить ответ: ${error.message}`);
  } finally {
    setChatLoading(false);
    $("chat-input").focus();
  }
}

function initChat() {
  renderChatSuggestions(CHAT_DEFAULT_SUGGESTIONS);
  appendBotText(
    "Здравствуйте! Я помогу разобраться с партиями, поставщиками, показателями и рисками. " +
      "Спросите что-нибудь или выберите подсказку ниже."
  );
}

/* ---------------------------------------------------------------- events */

function bindEvents() {
  $("refresh-btn").addEventListener("click", async () => {
    state.page = 0;
    await Promise.allSettled([loadHealth(), loadMeta()]);
    await Promise.allSettled([loadStats(), loadExamples(), loadBatches()]);
  });

  $("filter-search").addEventListener("input", (event) => {
    state.filters.search = event.target.value;
    window.clearTimeout(state.searchTimer);
    state.searchTimer = window.setTimeout(() => {
      state.page = 0;
      loadBatches();
    }, 300);
  });

  $("filter-risk").addEventListener("change", (event) => {
    state.filters.risk = event.target.value;
    state.page = 0;
    loadBatches();
  });

  $("filter-supplier").addEventListener("change", (event) => {
    state.filters.supplier = event.target.value;
    state.page = 0;
    loadBatches();
  });

  $("filter-scenario").addEventListener("change", (event) => {
    state.filters.scenario = event.target.value;
    state.page = 0;
    loadBatches();
  });

  $("filter-sort").addEventListener("change", (event) => {
    state.filters.sort = event.target.value;
    state.page = 0;
    loadBatches();
  });

  $("prev-page").addEventListener("click", () => {
    if (state.page > 0) {
      state.page -= 1;
      loadBatches();
    }
  });

  $("next-page").addEventListener("click", () => {
    state.page += 1;
    loadBatches();
  });

  document.querySelector("#batches-table tbody").addEventListener("click", (event) => {
    const row = event.target.closest("[data-batch]");
    if (row) openBatchCard(row.getAttribute("data-batch"));
  });

  document.querySelector("#batches-table tbody").addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    const row = event.target.closest("[data-batch]");
    if (!row) return;
    event.preventDefault();
    openBatchCard(row.getAttribute("data-batch"));
  });

  $("examples-container").addEventListener("click", (event) => {
    const button = event.target.closest("[data-batch]");
    if (button) openBatchCard(button.getAttribute("data-batch"));
  });

  $("modal-content").addEventListener("click", (event) => {
    const button = event.target.closest("[data-llm]");
    if (button) requestLlm(button);
  });

  $("modal").addEventListener("click", (event) => {
    if (event.target.closest("[data-close-modal]")) closeModal();
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !$("modal").hidden) closeModal();
  });

  $("check-form").addEventListener("submit", submitForm);
  $("reset-form").addEventListener("click", resetForm);

  $("chat-form").addEventListener("submit", (event) => {
    event.preventDefault();
    sendChat($("chat-input").value);
  });

  $("chat-close").addEventListener("click", closeChat);

  $("chat-suggestions").addEventListener("click", (event) => {
    const button = event.target.closest("[data-chat-suggestion]");
    if (button) sendChat(button.getAttribute("data-chat-suggestion"));
  });

  $("chat-messages").addEventListener("click", (event) => {
    const button = event.target.closest("[data-chat-suggestion]");
    if (button) sendChat(button.getAttribute("data-chat-suggestion"));
  });
}

async function init() {
  bindEvents();
  initChat();
  setDefaultDates();
  await Promise.allSettled([loadHealth(), loadMeta()]);
  await Promise.allSettled([loadStats(), loadExamples(), loadBatches()]);
}

document.addEventListener("DOMContentLoaded", init);