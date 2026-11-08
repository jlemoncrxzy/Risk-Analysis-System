"use strict";

const $ = (id) => document.getElementById(id);
const VIEWS = ["chat", "dashboard", "history", "notifications"];
const RISK_CLASS = { "низкий": "low", "средний": "medium", "высокий": "high" };
const RISK_LABEL = { "низкий": "Низкий", "средний": "Средний", "высокий": "Высокий" };
const STATUS_BY_LEVEL = {
  "низкий": "Одобрено",
  "средний": "Имеются дефекты",
  "высокий": "Брак",
};
const PARAM_STATUS = {
  ok: "в норме",
  warning: "внимание",
  critical: "критично",
  info: "справочно",
};
const PALETTE = ["#9a6bff", "#60a5fa", "#34d399", "#fbbf24", "#f87171", "#c084fc", "#22d3ee"];
const DEFAULT_SUGGESTIONS = [
  "Покажи статистику по уровням риска",
  "Топ-10 поставщиков по риску",
  "Сравни поставщиков",
  "Топ-5 партий по свинцу",
  "Покажи динамику брака (график)",
  "Покажи партию RE-2025-001",
];

const state = {
  view: "chat",
  agent: "deepseek",
  agents: [],
  dashboard: null,
  summaryBatch: null,
  summaryContext: null,
  chat: {
    messages: [],
    history: [],
    lastQuestion: "",
    loading: false,
  },
  history: {
    page: 0,
    pageSize: 8,
    total: 0,
    filters: { status: "", search: "", dateFrom: "", dateTo: "" },
  },
  notifications: {
    items: [],
    unread: 0,
    filter: "all",
    period: "7",
    updatedAt: null,
  },
};

let chartSequence = 0;
let toastTimer = null;

/* ------------------------------------------------------------------ utils */

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[char]));
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
      /* ignore */
    }
    throw new Error(message);
  }
  return response.json();
}

function riskClass(level) {
  return RISK_CLASS[level] || "low";
}

function statusLabel(item) {
  return item.status_label || STATUS_BY_LEVEL[item.risk_level] || "—";
}

function colorForLevel(level) {
  if (level === "высокий") return "#f87171";
  if (level === "средний") return "#fbbf24";
  return "#34d399";
}

function formatNumber(value, digits = 1) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "—";
  return number.toFixed(digits).replace(/\.0+$/, "");
}

function formatDateTime(value) {
  if (!value) return "—";
  const [datePart, timePart] = String(value).split("T");
  const parts = (datePart || "").split("-");
  if (parts.length !== 3) return esc(value);
  const time = timePart ? timePart.slice(0, 5) : "";
  return `${parts[2]}.${parts[1]}.${parts[0]}${time ? ` ${time}` : ""}`;
}

function formatDateShort(value) {
  if (!value) return "—";
  const parts = String(value).slice(0, 10).split("-");
  if (parts.length !== 3) return esc(value);
  return `${parts[2]}.${parts[1]}.${parts[0]}`;
}

function formatNotifTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return esc(value);
  const now = new Date();
  const time = date.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
  const sameDay = date.toDateString() === now.toDateString();
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (sameDay) return `Сегодня, ${time}`;
  if (date.toDateString() === yesterday.toDateString()) return `Вчера, ${time}`;
  return `${date.toLocaleDateString("ru-RU")}, ${time}`;
}

function timeNow() {
  return new Date().toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
}

function setSyncTimes() {
  const time = timeNow();
  ["sync-time", "hist-updated", "notif-updated"].forEach((id) => {
    const element = $(id);
    if (element) element.textContent = time;
  });
}

function chartColor(label, index) {
  const text = String(label || "").toLowerCase();
  if (text.includes("одобр") || text.includes("низк")) return "#34d399";
  if (text.includes("дефект") || text.includes("средн")) return "#fbbf24";
  if (text.includes("брак") || text.includes("высок")) return "#f87171";
  return PALETTE[index % PALETTE.length];
}

function formatChartValue(value, unit = "") {
  const number = Number(value);
  if (!Number.isFinite(number)) return esc(value);
  let text;
  if (Math.abs(number) >= 100) text = number.toFixed(0);
  else if (Math.abs(number) >= 1) text = number.toFixed(1).replace(/\.0$/, "");
  else text = number.toFixed(4).replace(/0+$/, "").replace(/\.$/, "");
  return unit ? `${text} ${esc(unit)}` : text;
}

function showToast(message) {
  const toast = $("toast");
  toast.textContent = message;
  toast.hidden = false;
  window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => {
    toast.hidden = true;
  }, 3200);
}

function downloadUrl(url) {
  const link = document.createElement("a");
  link.href = url;
  link.rel = "noopener";
  document.body.appendChild(link);
  link.click();
  link.remove();
}

function openModal(html) {
  $("modal-content").innerHTML = html;
  $("modal").hidden = false;
  document.body.classList.add("modal-open");
}

function closeModal() {
  $("modal").hidden = true;
  document.body.classList.remove("modal-open");
  $("modal-content").innerHTML = "";
}

/* --------------------------------------------------------------- routing */

function currentView() {
  const hash = window.location.hash.replace("#", "");
  return VIEWS.includes(hash) ? hash : "chat";
}

function setView(name) {
  const view = VIEWS.includes(name) ? name : "chat";
  state.view = view;
  document.querySelectorAll(".view").forEach((section) => {
    section.classList.toggle("active", section.id === `view-${view}`);
  });
  document.querySelectorAll("[data-nav]").forEach((item) => {
    item.classList.toggle("active", item.getAttribute("data-nav") === view);
  });
  if (view === "dashboard") loadDashboard();
  if (view === "history") loadHistory(state.history.page);
  if (view === "notifications") loadNotifications();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

/* ---------------------------------------------------------------- agents */

function agentInitials(agent) {
  if (agent.id === "gigachat") return "GC";
  if (agent.id === "alisa") return "AA";
  if (agent.id === "deepseek") return "DS";
  return "AI";
}

function currentAgent() {
  return state.agents.find((item) => item.id === state.agent) || {
    id: "deepseek",
    name: "DeepSeek",
    model: "deepseek-chat API",
    description: "Облачная LLM DeepSeek с RAG-поиском по данным проекта.",
    available: false,
    status: "не настроен",
  };
}

async function loadAgents() {
  try {
    const data = await api("/api/agents");
    state.agents = data.items || [];
    if (!state.agents.some((item) => item.id === state.agent)) {
      state.agent = data.default || "deepseek";
    }
  } catch {
    state.agents = [];
  }
  renderAgents();
}

function renderAgents() {
  const list = $("agent-list");
  if (!list) return;
  if (!state.agents.length) {
    list.innerHTML = `<button class="agent-item active" data-agent="deepseek">
      <span class="agent-icon">DS</span>
      <span><span class="agent-name">DeepSeek</span><span class="agent-status"><i></i>не настроен</span></span>
    </button>`;
    return;
  }
  list.innerHTML = state.agents
    .map((agent) => {
      const active = agent.id === state.agent;
      const online = agent.available;
      return `<button class="agent-item ${active ? "active" : ""}" type="button" data-agent="${esc(agent.id)}">
        <span class="agent-icon">${agentInitials(agent)}</span>
        <span>
          <span class="agent-name">${esc(agent.name)}</span>
          <span class="agent-status ${online ? "online" : ""}"><i></i>${esc(agent.status || (online ? "доступен" : "недоступен"))}</span>
        </span>
      </button>`;
    })
    .join("");

  const agent = currentAgent();
  $("agent-profile").innerHTML = `
    <div class="profile-top">
      <div class="profile-avatar">${agentInitials(agent)}</div>
      <div>
        <div class="profile-name">${esc(agent.name)}</div>
        <div class="profile-model">${esc(agent.model || "")}</div>
      </div>
    </div>
    <p class="profile-description">${esc(agent.description || "")}</p>`;
}

function selectAgent(id) {
  state.agent = id;
  renderAgents();
  const agent = currentAgent();
  if (!agent.available) {
    showToast(`${agent.name} недоступен — запрос подхватит следующий доступный провайдер.`);
  }
}

/* ---------------------------------------------------------------- charts */

function renderBarWidget(block) {
  const labels = block.labels || [];
  const values = (block.values || []).map((value) => Number(value) || 0);
  const unit = block.unit || "";
  const max = Math.max(1, ...values);
  const rows = labels
    .map((label, index) => {
      const value = values[index] || 0;
      const width = Math.max(2, Math.round((value / max) * 100));
      return `<div class="bar-row">
        <span class="bar-label">${esc(label)}</span>
        <div class="bar-track"><span class="bar-fill" style="width:${width}%;background:${chartColor(label, index)}"></span></div>
        <span class="bar-value">${formatChartValue(value, unit)}</span>
      </div>`;
    })
    .join("");
  return `<div class="widget"><div class="widget-title">${esc(block.title || "График")}</div><div class="bar-widget">${rows}</div></div>`;
}

function renderDonutWidget(block) {
  const labels = block.labels || [];
  const values = (block.values || []).map((value) => Number(value) || 0);
  const total = values.reduce((sum, value) => sum + value, 0);
  let cursor = 0;
  const stops = values.map((value, index) => {
    const start = cursor;
    cursor += total ? (value / total) * 100 : 0;
    return `${chartColor(labels[index], index)} ${start.toFixed(2)}% ${cursor.toFixed(2)}%`;
  });
  const legend = labels
    .map((label, index) => {
      const share = total ? Math.round((values[index] / total) * 100) : 0;
      return `<li><i style="background:${chartColor(label, index)}"></i><span>${esc(label)}</span><b>${values[index]} · ${share}%</b></li>`;
    })
    .join("");
  const background = total
    ? `conic-gradient(${stops.join(", ")})`
    : "#241c49";
  const title = block.title ? `<div class="widget-title">${esc(block.title)}</div>` : "";
  return `<div class="widget">
    ${title}
    <div class="donut-wrap">
      <div class="donut" style="background:${background}">
        <div class="donut-center"><strong>${total}</strong><span>партий</span></div>
      </div>
      <ul class="legend-list">${legend}</ul>
    </div>
  </div>`;
}

function niceAxis(rawMax, unit) {
  const target = Math.max(Number(rawMax) || 0, 1);
  const candidates = unit === "%"
    ? [1, 2, 2.5, 4, 5, 8, 10, 12.5, 20, 25, 50, 100]
    : [0.5, 1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 5000];
  for (const step of candidates) {
    const max = Math.ceil(target / step) * step;
    if (max / step <= 4.01) return { max, step };
  }
  const step = Math.ceil(target / 4);
  return { max: step * 4, step };
}

function smoothPath(points) {
  if (points.length < 2) return points.length ? `M${points[0][0]},${points[0][1]}` : "";
  const tension = 0.18;
  let path = `M${points[0][0].toFixed(2)},${points[0][1].toFixed(2)}`;
  for (let index = 0; index < points.length - 1; index += 1) {
    const p0 = points[index - 1] || points[index];
    const p1 = points[index];
    const p2 = points[index + 1];
    const p3 = points[index + 2] || p2;
    const c1x = p1[0] + (p2[0] - p0[0]) * tension;
    const c1y = p1[1] + (p2[1] - p0[1]) * tension;
    const c2x = p2[0] - (p3[0] - p1[0]) * tension;
    const c2y = p2[1] - (p3[1] - p1[1]) * tension;
    path += ` C${c1x.toFixed(2)},${c1y.toFixed(2)} ${c2x.toFixed(2)},${c2y.toFixed(2)} ${p2[0].toFixed(2)},${p2[1].toFixed(2)}`;
  }
  return path;
}

function formatAxisLabel(label) {
  const text = String(label || "");
  const match = text.match(/^(\d{2})\.(\d{2})$/);
  if (!match) return text;
  const months = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"];
  const month = months[Number(match[2]) - 1] || match[2];
  return `${match[1]} ${month}`;
}

function renderLineWidget(block) {
  const labels = block.labels || [];
  const values = (block.values || []).map((value) => Number(value) || 0);
  const unit = block.unit || "";
  if (!values.length) return `<div class="widget"><p class="muted">Нет данных для графика.</p></div>`;

  const width = 760;
  const height = 280;
  const left = 46;
  const right = 18;
  const top = 30;
  const bottom = 44;
  const plotWidth = width - left - right;
  const plotHeight = height - top - bottom;
  const axis = niceAxis(Math.max(...values, 0), unit);
  const maxValue = axis.max || 1;
  const stepX = values.length > 1 ? plotWidth / (values.length - 1) : plotWidth;
  const points = values.map((value, index) => {
    const x = left + index * stepX;
    const y = top + (1 - value / maxValue) * plotHeight;
    return [x, y];
  });
  const linePath = smoothPath(points);
  const areaPath = `${linePath} L${(left + plotWidth).toFixed(2)},${(top + plotHeight).toFixed(2)} L${left.toFixed(2)},${(top + plotHeight).toFixed(2)} Z`;

  let grid = "";
  for (let value = maxValue; value >= 0.0001; value -= axis.step) {
    const y = top + (1 - value / maxValue) * plotHeight;
    grid += `<line class="grid-line" x1="${left}" y1="${y.toFixed(2)}" x2="${width - right}" y2="${y.toFixed(2)}"></line>
      <text class="axis-label" x="${left - 9}" y="${(y + 3).toFixed(2)}" text-anchor="end">${formatChartValue(value, unit)}</text>`;
  }
  grid += `<line class="grid-line base-line" x1="${left}" y1="${top + plotHeight}" x2="${width - right}" y2="${top + plotHeight}"></line>`;

  const dots = points
    .map(([x, y], index) => `<g class="point-group">
      <circle class="point-halo" cx="${x.toFixed(2)}" cy="${y.toFixed(2)}" r="6"></circle>
      <circle class="point" cx="${x.toFixed(2)}" cy="${y.toFixed(2)}" r="3.4"><title>${esc(labels[index] || "")}: ${formatChartValue(values[index], unit)}</title></circle>
      ${values.length <= 12 ? `<text class="point-label" x="${x.toFixed(2)}" y="${(y - 12).toFixed(2)}" text-anchor="middle">${formatChartValue(values[index], unit)}</text>` : ""}
    </g>`)
    .join("");
  const xLabels = labels
    .map((label, index) => {
      const x = left + index * stepX;
      return `<text class="x-label" x="${x.toFixed(2)}" y="${height - 12}" text-anchor="middle">${esc(formatAxisLabel(label))}</text>`;
    })
    .join("");
  const gradientId = `lineGrad${++chartSequence}`;
  const title = block.title ? `<div class="widget-title">${esc(block.title)}</div>` : "";
  const footer = block.footer
    ? `<div class="line-chart-footer">
        <span>${esc(block.footer.left || "")}</span>
        ${block.footer.delta != null ? `<span class="chart-delta ${Number(block.footer.delta) <= 0 ? "good" : "bad"}">${Number(block.footer.delta) > 0 ? "+" : ""}${Number(block.footer.delta).toFixed(1)} п.п. за период</span>` : ""}
      </div>`
    : "";

  return `<div class="widget">
    ${title}
    <svg class="line-chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="${esc(block.title || "Динамика брака")}">
      <defs>
        <linearGradient id="${gradientId}" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="#8b5cf6" stop-opacity="0.42"></stop>
          <stop offset="55%" stop-color="#7c4dff" stop-opacity="0.16"></stop>
          <stop offset="100%" stop-color="#7c4dff" stop-opacity="0"></stop>
        </linearGradient>
        <linearGradient id="${gradientId}Stroke" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0%" stop-color="#a78bfa"></stop>
          <stop offset="100%" stop-color="#7c9cff"></stop>
        </linearGradient>
      </defs>
      ${grid}
      <path class="area-path" d="${areaPath}" fill="url(#${gradientId})"></path>
      <path class="line-path" d="${linePath}" stroke="url(#${gradientId}Stroke)"></path>
      ${dots}
      ${xLabels}
    </svg>
    ${footer}
  </div>`;
}

function renderGaugeWidget(block) {
  const value = Number(block.value) || 0;
  const max = Number(block.max) || 100;
  const percent = Math.max(0, Math.min(100, (value / max) * 100));
  const color = colorForLevel(block.level);
  return `<div class="widget">
    <div class="widget-title">${esc(block.title || "Оценка риска")}</div>
    <div class="gauge-widget">
      <div class="gauge" style="--value:${percent};--gauge-color:${color}">
        <span class="gauge-value">${value}<small>/${max}</small></span>
      </div>
      <div class="gauge-caption">
        <strong>${esc(block.label || block.level || "")}</strong>
        <span>${esc(block.note || `Порог ${block.threshold || 70}%`)}</span>
      </div>
    </div>
  </div>`;
}

function renderTableWidget(block) {
  const columns = block.columns || [];
  const rows = block.rows || [];
  const header = columns.map((column) => `<th>${esc(column.label)}</th>`).join("");
  const body = rows
    .map((row) => `<tr>${columns.map((column) => `<td>${esc(row[column.key])}</td>`).join("")}</tr>`)
    .join("");
  return `<div class="widget">
    <div class="widget-title">${esc(block.title || "Таблица")}</div>
    <div class="table-wrap"><table class="data-table">
      <thead><tr>${header}</tr></thead><tbody>${body}</tbody>
    </table></div>
  </div>`;
}

function renderListWidget(block) {
  const items = (block.items || []).map((item) => `<li>${esc(item)}</li>`).join("");
  return `<div class="widget">
    <div class="widget-title">${esc(block.title || "Список")}</div>
    <ul class="widget-list">${items}</ul>
  </div>`;
}

function renderBlocks(blocks) {
  return (blocks || [])
    .map((block) => {
      if (block.type === "text") {
        return `<p class="widget-text" style="margin-top:10px">${esc(block.text)}</p>`;
      }
      if (block.type === "list") return renderListWidget(block);
      if (block.type === "table") return renderTableWidget(block);
      if (block.type === "gauge") return renderGaugeWidget(block);
      if (block.type === "chart") {
        if (block.chart_type === "line") return renderLineWidget(block);
        if (block.chart_type === "pie" || block.chart_type === "donut") return renderDonutWidget(block);
        return renderBarWidget(block);
      }
      return "";
    })
    .join("");
}

/* ------------------------------------------------------------------ chat */

function scrollThread() {
  const thread = $("chat-thread");
  thread.scrollTop = thread.scrollHeight;
}

function renderChatSuggestions(items) {
  const list = (items && items.length ? items : DEFAULT_SUGGESTIONS).slice(0, 6);
  $("chat-suggestions").innerHTML = list
    .map((item) => `<button class="suggestion-chip" type="button" data-suggestion="${esc(item)}">${esc(item)}</button>`)
    .join("");
}

function appendUserMessage(text) {
  const thread = $("chat-thread");
  const node = document.createElement("div");
  node.className = "msg user";
  node.innerHTML = `<div class="msg-body"><div class="bubble"><p class="msg-text">${esc(text)}</p></div></div>`;
  thread.appendChild(node);
  scrollThread();
}

function appendTyping() {
  const thread = $("chat-thread");
  const node = document.createElement("div");
  node.id = "chat-typing";
  node.className = "msg bot";
  node.innerHTML = `<div class="msg-avatar">${agentInitials(currentAgent())}</div>
    <div class="msg-body"><div class="bubble typing"><span></span><span></span><span></span><em class="typing-label">RAG-поиск и анализ…</em></div></div>`;
  thread.appendChild(node);
  scrollThread();
}

function removeTyping() {
  const typing = $("chat-typing");
  if (typing) typing.remove();
}

function appendBotMessage(response) {
  const thread = $("chat-thread");
  const agent = currentAgent();
  const batch = response.context && response.context.batch;
  const showChips = Boolean(response.ui && response.ui.show_chips);
  const chips = [];
  if (showChips && batch) {
    chips.push(`<span class="msg-chip risk-${riskClass(batch.risk_level)}">Риск: ${batch.risk_score}%</span>`);
    if (batch.confidence != null) {
      chips.push(`<span class="msg-chip">Доверие: ${Math.round(batch.confidence * 100)}%</span>`);
    }
    chips.push(`<span class="msg-chip">${esc(batch.scenario || "")}</span>`);
  }
  const steps = response.context && response.context.steps;
  const details = steps && steps.length
    ? `<details class="thinking"><summary>Ход анализа</summary><ul>${steps.map((step) => `<li>${esc(step)}</li>`).join("")}</ul></details>`
    : "";
  const note = response.agent_note
    ? `<div class="agent-note">${esc(response.agent_note)}</div>`
    : "";

  const node = document.createElement("div");
  node.className = "msg bot";
  node.innerHTML = `
    <div class="msg-avatar">${agentInitials(agent)}</div>
    <div class="msg-body">
      <div class="msg-meta"><strong>${esc(agent.name)}</strong><span>${timeNow()}</span><span>${esc(response.provider || "")}</span></div>
      <div class="bubble">
        <p class="msg-text">${esc(response.answer || "")}</p>
        ${chips.length ? `<div class="msg-chips">${chips.join("")}</div>` : ""}
        ${renderBlocks(response.blocks)}
        ${details}
        ${note}
      </div>
    </div>`;
  thread.appendChild(node);
  scrollThread();
}

async function sendChat(rawQuestion) {
  const question = String(rawQuestion || "").trim();
  if (!question || state.chat.loading) return;
  if (state.view !== "chat") {
    window.location.hash = "chat";
    setView("chat");
  }
  state.chat.lastQuestion = question;
  $("chat-input").value = "";
  appendUserMessage(question);
  state.chat.history.push({ role: "user", content: question });
  state.chat.loading = true;
  $("chat-send").disabled = true;
  appendTyping();

  try {
    const response = await api("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message: question,
        agent: state.agent,
        history: state.chat.history.slice(-6),
      }),
    });
    removeTyping();
    appendBotMessage(response);
    state.chat.history.push({ role: "assistant", content: response.answer });
    renderChatSuggestions(response.suggestions || DEFAULT_SUGGESTIONS);
    if (response.context) updateSummary(response.context);
  } catch (error) {
    removeTyping();
    appendBotMessage({
      answer: `Не удалось получить ответ: ${error.message}`,
      blocks: [],
      provider: "ошибка",
      context: null,
    });
  } finally {
    state.chat.loading = false;
    $("chat-send").disabled = false;
    $("chat-input").focus();
  }
}

function initChatWelcome() {
  appendBotMessage({
    answer:
      "Здравствуйте! Я ИИ-помощник «Симулякрума». " +
      "Ищу ответ в данных проекта через RAG и разбираю партии, поставщиков и показатели. " +
      "Выберите слева DeepSeek, GigaChat или Алису AI. По умолчанию отвечаю только текстом; " +
      "таблицы добавляю для статистики или по маркеру (таблица), график — (график), шкалу — (виджет).",
    blocks: [],
    provider: "RAG · LLM",
    context: null,
  });
  renderChatSuggestions(DEFAULT_SUGGESTIONS);
}

/* --------------------------------------------------------------- summary */

function renderGauge(value, level) {
  const percent = Math.max(0, Math.min(100, Number(value) || 0));
  const color = colorForLevel(level);
  return `<div class="gauge" style="--value:${percent};--gauge-color:${color}">
    <span class="gauge-value">${Math.round(percent)}<small>/100</small></span>
  </div>`;
}

function updateSummary(context) {
  if (!context) return;
  state.summaryContext = context;
  const batch = context.batch;
  const level = batch ? batch.risk_level : null;
  const pill = $("summary-level");
  if (pill) {
    pill.hidden = !batch;
    pill.className = `risk-pill ${level ? riskClass(level) : ""}`;
    pill.textContent = batch ? RISK_LABEL[level] : "—";
  }

  const content = $("summary-content");
  if (batch) {
    state.summaryBatch = batch;
    content.innerHTML = `
      <div class="summary-batch-id">Партия № ${esc(batch.batch_id)}</div>
      <div class="summary-scenario">${esc(batch.scenario || "")} · ${esc(batch.supplier || "")}</div>
      <div class="summary-gauge">
        ${renderGauge(batch.risk_score, batch.risk_level)}
        <div class="gauge-caption">
          <strong style="color:${colorForLevel(batch.risk_level)}">${esc(batch.status_label || STATUS_BY_LEVEL[batch.risk_level] || "")}</strong>
          <span>${esc(batch.recommended_action || "")}</span>
        </div>
      </div>
      <div class="summary-metrics">
        <div><span>Оператор</span><strong>${esc(batch.operator || "—")}</strong></div>
        <div><span>Проверка</span><strong>${formatDateTime(batch.checked_at)}</strong></div>
        <div><span>Уверенность</span><strong>${batch.confidence != null ? `${Math.round(batch.confidence * 100)}%` : "—"}</strong></div>
        <div><span>Критический порог</span><strong>70%</strong></div>
      </div>
      ${batch.top_factors && batch.top_factors.length
        ? `<div class="summary-factors">${batch.top_factors.map((factor) => `<span class="factor-chip">${esc(factor)}</span>`).join("")}</div>`
        : ""}`;
  } else {
    state.summaryBatch = null;
    content.innerHTML = `<div class="summary-metrics">${(context.metrics || [])
      .map((metric) => `<div><span>${esc(metric.label)}</span><strong>${esc(metric.value)}</strong></div>`)
      .join("")}</div>`;
  }

  const logs = context.logs || [];
  $("system-logs").innerHTML = logs
    .map((log) => `<li><span>${esc(log.text)}</span><time>${esc(log.time)}</time></li>`)
    .join("");
}

function summaryFromDashboard(dashboard) {
  if (!dashboard) return;
  const batch = dashboard.latest_high;
  const metrics = [
    { label: "Всего партий", value: String(dashboard.total) },
    { label: "Доля брака", value: `${Math.round(dashboard.defect_rate * 100)}%` },
    { label: "Средний риск", value: `${dashboard.average_score}/100` },
    { label: "Точность модели", value: `${Math.round((dashboard.model.accuracy || 0) * 100)}%` },
  ];
  updateSummary({
    batch,
    metrics,
    steps: [],
    logs: [
      { time: timeNow(), text: "Данные из data/ синхронизированы" },
      { time: timeNow(), text: "Модель риска загружена" },
      { time: timeNow(), text: "Сводка готова" },
    ],
  });
}

async function exportSummary() {
  if (state.summaryBatch) {
    downloadUrl(`/api/batches/${encodeURIComponent(state.summaryBatch.batch_id)}/report`);
  } else {
    downloadUrl("/api/export.csv");
  }
}

async function notifySummary() {
  if (!state.summaryBatch) {
    showToast("Сначала откройте партию или задайте вопрос о ней.");
    return;
  }
  try {
    await api("/api/notify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        batch_id: state.summaryBatch.batch_id,
        message: `Команда уведомлена по партии ${state.summaryBatch.batch_id}. Риск ${state.summaryBatch.risk_score}%.`,
      }),
    });
    showToast("Уведомление отправлено команде.");
    await loadNotifications();
  } catch (error) {
    showToast(`Не удалось отправить уведомление: ${error.message}`);
  }
}

function repeatAnalysis() {
  if (!state.chat.lastQuestion) {
    showToast("Пока нет запроса для повторного анализа.");
    return;
  }
  sendChat(state.chat.lastQuestion);
}

/* ------------------------------------------------------------- dashboard */

async function loadDashboard() {
  try {
    const dashboard = await api("/api/dashboard");
    state.dashboard = dashboard;
    if (dashboard.period && dashboard.period.from) {
      if (!$("hist-date-from").value) $("hist-date-from").value = dashboard.period.from;
      if (!$("hist-date-to").value) $("hist-date-to").value = dashboard.period.to;
      if (!state.history.filters.dateFrom) state.history.filters.dateFrom = dashboard.period.from;
      if (!state.history.filters.dateTo) state.history.filters.dateTo = dashboard.period.to;
    }
    renderDashboard(dashboard);
    if (!state.summaryContext) summaryFromDashboard(dashboard);
    setSyncTimes();
  } catch (error) {
    $("dash-stats").innerHTML = `<div class="stat-card"><span class="muted">Ошибка: ${esc(error.message)}</span></div>`;
  }
  loadExamples();
}

function renderDashboard(dashboard) {
  $("dash-period").textContent = dashboard.period && dashboard.period.from
    ? `${formatDateShort(dashboard.period.from)} — ${formatDateShort(dashboard.period.to)}`
    : "—";

  const statuses = dashboard.statuses || [];
  const statusMap = Object.fromEntries(statuses.map((item) => [item.key, item]));
  const low = statusMap.low || { count: 0 };
  const medium = statusMap.medium || { count: 0 };
  const high = statusMap.high || { count: 0 };

  $("dash-stats").innerHTML = `
    <article class="stat-card">
      <div class="stat-top"><span>Всего проверено</span><span class="stat-icon">▤</span></div>
      <div class="stat-value">${dashboard.total}</div>
      <div class="stat-foot">за период проверки</div>
    </article>
    <article class="stat-card">
      <div class="stat-top"><span>Доля брака</span><span class="stat-icon">▥</span></div>
      <div class="stat-value">${(dashboard.defect_rate * 100).toFixed(1)}<small>%</small></div>
      <div class="stat-foot red">${high.count} из ${dashboard.total}</div>
    </article>
    <article class="stat-card">
      <div class="stat-top"><span>Средний риск</span><span class="stat-icon">⌁</span></div>
      <div class="stat-value">${dashboard.average_score}<small>/100</small></div>
      <div class="stat-foot">по всем партиям</div>
    </article>
    <article class="stat-card">
      <div class="stat-top"><span>Статусы партий</span><span class="stat-icon">◔</span></div>
      <div class="mini-status">
        <span><i style="background:#34d399"></i>${low.count}</span>
        <span><i style="background:#fbbf24"></i>${medium.count}</span>
        <span><i style="background:#f87171"></i>${high.count}</span>
      </div>
      <div class="stat-foot">одобрено / дефекты / брак</div>
    </article>`;

  const daily = dashboard.daily || [];
  const firstRate = daily.length ? daily[0].defect_rate : 0;
  const lastRate = daily.length ? daily[daily.length - 1].defect_rate : 0;
  const lineBlock = {
    title: "",
    labels: daily.map((item) => item.label),
    values: daily.map((item) => Math.round(item.defect_rate * 1000) / 10),
    unit: "%",
    footer: {
      left: `${dashboard.total} проверок · ${high.count} забракованных партий`,
      delta: (lastRate - firstRate) * 100,
    },
  };
  $("dash-line").innerHTML = renderLineWidget(lineBlock).replace('class="widget"', 'class="widget" style="margin-top:0;padding-top:0;border-top:0"');

  const donutBlock = {
    title: "",
    labels: statuses.map((item) => item.label),
    values: statuses.map((item) => item.count),
  };
  $("dash-donut").innerHTML = renderDonutWidget(donutBlock).replace('class="widget"', 'class="widget" style="margin-top:0;padding-top:0;border-top:0"');
  $("dash-donut-note").textContent = `Все проверки: ${dashboard.total} партий · период ${dashboard.period.from ? formatDateShort(dashboard.period.from) : "—"} — ${dashboard.period.to ? formatDateShort(dashboard.period.to) : "—"}`;

  const reasons = dashboard.top_reasons || [];
  const maxReason = Math.max(1, ...reasons.map((item) => item.count));
  $("dash-defects").textContent = `${high.count} партий с браком`;
  $("dash-reasons").innerHTML = reasons.length
    ? reasons
        .map(
          (item) => `<div class="reason-row">
            <span class="reason-label">${esc(item.label)}</span>
            <div class="reason-track"><span class="reason-fill" style="width:${Math.max(3, Math.round((item.count / maxReason) * 100))}%"></span></div>
            <span class="reason-count">${item.count}</span>
          </div>`
        )
        .join("")
    : '<p class="muted">Причины брака не найдены.</p>';

  renderDashboardRisk(dashboard.latest_high);
}

function renderDashboardRisk(batch) {
  const container = $("dash-risk");
  if (!batch) {
    container.innerHTML = `<div class="panel-head"><h3>Риск брака</h3></div><p class="muted">Партий высокого риска нет.</p>`;
    return;
  }
  const score = Number(batch.risk_score) || 0;
  const color = colorForLevel(batch.risk_level);
  container.innerHTML = `
    <div class="risk-head">
      <div>
        <h3>Риск брака</h3>
        <p class="risk-batch">Партия № <strong>${esc(batch.batch_id)}</strong></p>
        <p class="risk-explain">${esc((batch.top_factors || []).join(" · ") || "Факторы уточняются")}</p>
      </div>
      <div style="text-align:right">
        <div class="risk-score-big" style="color:${color}">${score}%</div>
        <div class="risk-level-text" style="color:${color}">${RISK_LABEL[batch.risk_level] || ""}</div>
      </div>
    </div>
    <div class="risk-bar"><i style="left:${Math.min(100, score)}%"></i></div>
    <div class="muted small">Проверка: ${formatDateTime(batch.checked_at)} · оператор ${esc(batch.operator || "—")} · уверенность ${batch.confidence != null ? `${Math.round(batch.confidence * 100)}%` : "—"}</div>
    <button class="wide-btn primary" type="button" data-batch="${esc(batch.batch_id)}">Открыть анализ →</button>`;
}

async function loadExamples() {
  try {
    const examples = await api("/api/examples?low=2&medium=2&high=2");
    $("dash-examples").innerHTML = examples
      .map(
        (item) => `<article class="example-card" data-batch="${esc(item.batch_id)}">
          <div class="example-top">
            <span class="example-id">${esc(item.batch_id)}</span>
            <span class="status-pill ${riskClass(item.risk_level)}">${esc(statusLabel(item))}</span>
          </div>
          <h4>${esc(item.scenario)}</h4>
          <p class="example-supplier">${esc(item.supplier)}</p>
          <div class="example-score"><span>Риск</span><strong>${item.risk_score}%</strong></div>
        </article>`
      )
      .join("");
  } catch {
    $("dash-examples").innerHTML = '<p class="muted">Не удалось загрузить примеры.</p>';
  }
}

/* --------------------------------------------------------------- history */

function historyParams() {
  const params = new URLSearchParams({
    limit: String(state.history.pageSize),
    offset: String(state.history.page * state.history.pageSize),
    sort: "date",
  });
  if (state.history.filters.status) params.set("risk_level", state.history.filters.status);
  if (state.history.filters.search.trim()) params.set("search", state.history.filters.search.trim());
  if (state.history.filters.dateFrom) params.set("date_from", state.history.filters.dateFrom);
  if (state.history.filters.dateTo) params.set("date_to", state.history.filters.dateTo);
  return params;
}

async function loadHistory(page = state.history.page) {
  state.history.page = Math.max(0, page);
  try {
    const data = await api(`/api/batches?${historyParams().toString()}`);
    state.history.total = data.total;
    const pages = Math.max(1, Math.ceil(data.total / state.history.pageSize));
    if (state.history.page >= pages) {
      state.history.page = pages - 1;
      return loadHistory(state.history.page);
    }
    renderHistory(data);
    setSyncTimes();
  } catch (error) {
    $("hist-body").innerHTML = `<tr><td colspan="5" class="muted">Ошибка: ${esc(error.message)}</td></tr>`;
  }
}

function renderHistory(data) {
  const items = data.items || [];
  $("hist-count").textContent = data.total;
  $("hist-body").innerHTML = items.length
    ? items
        .map((item) => {
          const cls = riskClass(item.risk_level);
          const color = colorForLevel(item.risk_level);
          return `<tr data-batch="${esc(item.batch_id)}">
            <td>${formatDateTime(item.checked_at)}</td>
            <td class="mono">${esc(item.batch_id)}</td>
            <td><span class="status-pill ${cls}">${esc(statusLabel(item))}</span></td>
            <td><div class="risk-cell"><div class="risk-track"><i style="width:${item.risk_score}%;background:${color}"></i></div><span class="risk-num">${item.risk_score}%</span></div></td>
            <td><div class="operator"><span class="operator-avatar">${esc(item.operator_initials || "—")}</span><span>${esc(item.operator || "—")}</span></div></td>
          </tr>`;
        })
        .join("")
    : '<tr><td colspan="5" class="muted">Ничего не найдено. Измените фильтры.</td></tr>';

  const from = data.total ? state.history.page * state.history.pageSize + 1 : 0;
  const to = Math.min((state.history.page + 1) * state.history.pageSize, data.total);
  $("hist-info").textContent = `Показано ${from}–${to} из ${data.total} проверок`;
  renderPagination(data.total);
}

function renderPagination(total) {
  const pages = Math.max(1, Math.ceil(total / state.history.pageSize));
  const current = state.history.page;
  const numbers = [];
  const start = Math.max(0, Math.min(current - 2, pages - 5));
  const end = Math.min(pages, start + 5);
  for (let index = start; index < end; index += 1) numbers.push(index);
  $("hist-pages").innerHTML = numbers
    .map((index) => `<button type="button" class="${index === current ? "active" : ""}" data-page="${index}">${index + 1}</button>`)
    .join("");
  $("hist-prev").disabled = current <= 0;
  $("hist-next").disabled = current >= pages - 1;
}

function applyHistoryFilters() {
  state.history.filters.status = $("hist-status").value;
  state.history.filters.search = $("hist-search").value;
  state.history.filters.dateFrom = $("hist-date-from").value;
  state.history.filters.dateTo = $("hist-date-to").value;
  state.history.page = 0;
  loadHistory(0);
}

function resetHistoryFilters() {
  $("hist-status").value = "";
  $("hist-search").value = "";
  $("hist-date-from").value = "";
  $("hist-date-to").value = "";
  state.history.filters = { status: "", search: "", dateFrom: "", dateTo: "" };
  state.history.page = 0;
  loadHistory(0);
}

/* ---------------------------------------------------------- notifications */

async function loadNotifications() {
  try {
    const data = await api("/api/notifications");
    state.notifications.items = data.items || [];
    state.notifications.unread = data.unread || 0;
    state.notifications.updatedAt = data.updated_at;
    renderNotifications();
    setSyncTimes();
  } catch {
    /* ignore */
  }
}

function updateNotifBadge() {
  const badge = $("notif-badge");
  if (!badge) return;
  badge.hidden = state.notifications.unread <= 0;
  badge.textContent = state.notifications.unread;
}

function notificationVisible(item) {
  if (state.notifications.filter === "unread" && item.read) return false;
  if (state.notifications.filter === "read" && !item.read) return false;
  const period = state.notifications.period;
  if (period === "all") return true;
  const days = Number(period);
  const created = new Date(item.created_at);
  if (Number.isNaN(created.getTime())) return true;
  const cutoff = Date.now() - days * 24 * 60 * 60 * 1000;
  return created.getTime() >= cutoff;
}

function renderNotifications() {
  const items = state.notifications.items || [];
  const unread = items.filter((item) => !item.read).length;
  const read = items.length - unread;
  $("notif-count-all").textContent = items.length;
  $("notif-count-unread").textContent = unread;
  $("notif-count-read").textContent = read;
  $("notif-summary").textContent = `${unread} непрочитанных уведомлений · требуют вашего внимания`;
  updateNotifBadge();

  const visible = items.filter(notificationVisible);
  const list = $("notif-list");
  if (!visible.length) {
    list.innerHTML = '<div class="notif-empty">Уведомлений нет.</div>';
    return;
  }
  list.innerHTML = visible
    .map((item) => {
      const icon = item.level === "critical" ? "!" : item.level === "warning" ? "⚠" : item.level === "success" ? "✓" : "i";
      const action = item.batch_id ? "open-notification" : "open-notification";
      return `<article class="notif-card ${item.read ? "" : "unread"}">
        <span class="notif-icon ${esc(item.level)}">${icon}</span>
        <div class="notif-body">
          <div class="notif-meta"><span class="category">${esc(item.category)}</span><span>${formatNotifTime(item.created_at)}</span></div>
          <div class="notif-title">${esc(item.title)}</div>
          <div class="notif-text">${esc(item.text)}</div>
        </div>
        <div class="notif-side">
          <span class="notif-new">${item.read ? "Прочитано" : "Новое"}</span>
          <button class="notif-open" type="button" data-action="${action}" data-notification-id="${esc(item.id)}" ${item.batch_id ? `data-batch="${esc(item.batch_id)}"` : 'data-view="history"'}>Перейти →</button>
        </div>
      </article>`;
    })
    .join("");
}

async function markAllRead() {
  try {
    await api("/api/notifications/read", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    showToast("Все уведомления отмечены прочитанными.");
    await loadNotifications();
  } catch (error) {
    showToast(`Не удалось обновить уведомления: ${error.message}`);
  }
}

async function openNotification(button) {
  const id = button.getAttribute("data-notification-id");
  const batchId = button.getAttribute("data-batch");
  if (id) {
    try {
      await api("/api/notifications/read", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ids: [id] }),
      });
      await loadNotifications();
    } catch {
      /* ignore */
    }
  }
  if (batchId) {
    openBatch(batchId);
  } else {
    window.location.hash = "history";
    setView("history");
  }
}

/* ------------------------------------------------------------ batch modal */

function renderBatchModal(batch) {
  const cls = riskClass(batch.risk_level);
  const color = colorForLevel(batch.risk_level);
  const params = (batch.parameters || [])
    .map(
      (item) => `<tr>
        <td>${esc(item.label)}</td>
        <td>${esc(item.value)}${item.unit ? ` ${esc(item.unit)}` : ""}</td>
        <td>${esc(item.norm)}</td>
        <td>${esc(PARAM_STATUS[item.status] || item.status)}</td>
      </tr>`
    )
    .join("");
  const factors = (batch.factors || []).length
    ? `<ul class="widget-list">${batch.factors
        .map(
          (factor) => `<li><strong>${esc(factor.label)}</strong> — ${esc(factor.detail)} <span class="muted">(вес ${factor.weight})</span></li>`
        )
        .join("")}</ul>`
    : '<p class="muted">Явных факторов риска не выявлено.</p>';

  return `<div>
    <p class="muted small" style="text-transform:uppercase;letter-spacing:.08em">${esc(batch.scenario)}</p>
    <h2 id="modal-title" class="mono">${esc(batch.batch_id)}</h2>
    <p class="modal-sub">${esc(batch.product_type)} · ${esc(batch.supplier)} · оператор ${esc(batch.operator || "—")} · ${formatDateTime(batch.checked_at)}</p>

    <div class="modal-grid">
      <div class="modal-card">
        <h3>Оценка риска</h3>
        <div class="summary-gauge">
          ${renderGauge(batch.risk_score, batch.risk_level)}
          <div class="gauge-caption">
            <strong style="color:${color}">${RISK_LABEL[batch.risk_level] || ""} риск · ${esc(batch.status_label || "")}</strong>
            <span>${esc(batch.recommended_action)}</span>
            <span>Порог высокого риска: 70%</span>
          </div>
        </div>
        ${batch.hard_violations && batch.hard_violations.length
          ? `<p class="form-error" style="margin-top:8px">Жёсткие нарушения: ${batch.hard_violations.map((item) => esc(item)).join("; ")}</p>`
          : ""}
      </div>
      <div class="modal-card">
        <h3>Лаборатория и модель</h3>
        <div class="summary-metrics">
          <div><span>Заключение</span><strong>${esc(batch.lab_conclusion)}</strong></div>
          <div><span>Дерево решений</span><strong>${batch.model ? `${esc(batch.model.risk_level)} · ${Math.round((batch.model.confidence || 0) * 100)}%` : "—"}</strong></div>
          <div><span>Годен до</span><strong>${formatDateShort(batch.expiry_date)}</strong></div>
          <div><span>Осталось</span><strong>${batch.days_to_expiry} дн.</strong></div>
        </div>
      </div>
    </div>

    <div class="modal-grid">
      <div class="modal-card">
        <h3>Параметры партии</h3>
        <div class="table-wrap"><table class="data-table">
          <thead><tr><th>Показатель</th><th>Значение</th><th>Норма</th><th>Статус</th></tr></thead>
          <tbody>${params}</tbody>
        </table></div>
      </div>
      <div class="modal-card">
        <h3>Факторы риска</h3>
        ${factors}
      </div>
    </div>

    <div class="modal-card" style="margin-top:12px">
      <h3>Объяснение</h3>
      <p class="widget-text">${esc(batch.explanation)}</p>
    </div>

    <div class="modal-actions">
      <button class="action-btn primary" type="button" data-action="export-batch" data-batch="${esc(batch.batch_id)}">⭳ Экспорт отчёта</button>
      <button class="action-btn" type="button" data-action="notify-batch" data-batch="${esc(batch.batch_id)}">⚠ Уведомить команду</button>
      <button class="action-btn ghost" type="button" data-action="ask-batch" data-batch="${esc(batch.batch_id)}">Спросить агента →</button>
      <button class="action-btn ghost" type="button" data-close-modal>Закрыть</button>
    </div>
  </div>`;
}

async function openBatch(batchId) {
  try {
    const batch = await api(`/api/batches/${encodeURIComponent(batchId)}`);
    openModal(renderBatchModal(batch));
    updateSummary({
      batch: {
        batch_id: batch.batch_id,
        scenario: batch.scenario,
        supplier: batch.supplier,
        risk_level: batch.risk_level,
        risk_score: batch.risk_score,
        status_label: batch.status_label,
        operator: batch.operator,
        checked_at: batch.checked_at,
        recommended_action: batch.recommended_action,
        top_factors: (batch.factors || []).map((factor) => factor.label).slice(0, 3),
        confidence: batch.model ? batch.model.confidence : null,
      },
      metrics: [],
      steps: [],
      logs: [
        { time: timeNow(), text: `Открыта партия ${batch.batch_id}` },
        { time: timeNow(), text: "Проверены правила и дерево решений" },
        { time: timeNow(), text: "Сводка обновлена" },
      ],
    });
  } catch (error) {
    showToast(`Не удалось открыть партию: ${error.message}`);
  }
}

/* -------------------------------------------------------------- checker */

function isoOffset(days) {
  const date = new Date();
  date.setDate(date.getDate() + days);
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${date.getFullYear()}-${month}-${day}`;
}

function openChecker() {
  openModal(`<div>
    <h2 id="modal-title">Новая проверка партии</h2>
    <p class="modal-sub">Введите показатели — Risk Engine рассчитает риск и объяснит решение.</p>
    <form id="checker-form">
      <div class="form-grid">
        <label class="filter"><span>ID партии</span><input name="batch_id" placeholder="автоматически"></label>
        <label class="filter"><span>Поставщик</span><input name="supplier" value="Ручной ввод"></label>
        <label class="filter"><span>Заключение лаборатории</span>
          <select name="lab_conclusion">
            <option>Соответствует</option>
            <option>Условно годен (Риск)</option>
            <option>Требует проверки (Нестабильно)</option>
            <option>Не соответствует (Брак)</option>
            <option>Не соответствует (Опасно)</option>
          </select>
        </label>
        <label class="filter"><span>Годен до</span><input name="expiry_date" type="date" value="${isoOffset(30)}" required></label>
        <label class="filter"><span>Температура хранения, °C</span><input name="storage_temp_c" type="number" step="0.1" value="9"></label>
        <label class="filter"><span>Температура перевозки, °C</span><input name="transport_temp_c" type="number" step="0.1" value="9"></label>
        <label class="filter"><span>Сухие вещества, %</span><input name="dry_matter_pct" type="number" step="0.1" value="10"></label>
        <label class="filter"><span>Кислотность, см³</span><input name="acidity_cm3" type="number" step="0.1" value="2.1"></label>
        <label class="filter"><span>CO₂, %</span><input name="co2_mass_pct" type="number" step="0.01" value="0.5"></label>
        <label class="filter"><span>Свинец, мг/кг</span><input name="lead_mg_kg" type="number" step="0.0001" value="0.002"></label>
        <label class="filter"><span>Кадмий, мг/кг</span><input name="cadmium_mg_kg" type="number" step="0.0001" value="0.001"></label>
        <label class="filter"><span>Мышьяк, мг/кг</span><input name="arsenic_mg_kg" type="number" step="0.0001" value="0.0015"></label>
        <label class="filter"><span>КМАФАнМ, КОЕ/см³</span><input name="kmafanm_cfu_cm3" type="number" step="1" value="15"></label>
        <label class="filter"><span>БГКП, КОЕ/см³</span><input name="bgkp_cfu_cm3" type="number" step="1" value="0"></label>
        <label class="filter"><span>Дрожжи, КОЕ/см³</span><input name="yeast_cfu_cm3" type="number" step="1" value="5"></label>
        <label class="filter"><span>Плесень, КОЕ/см³</span><input name="mold_cfu_cm3" type="number" step="1" value="2"></label>
      </div>
      <div class="modal-actions">
        <button class="action-btn primary" type="submit">Оценить риск</button>
        <button class="action-btn ghost" type="button" data-close-modal>Отмена</button>
      </div>
    </form>
  </div>`);

  $("checker-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    const number = (name, fallback = 0) => {
      const value = Number.parseFloat(data.get(name));
      return Number.isFinite(value) ? value : fallback;
    };
    const payload = {
      batch_id: String(data.get("batch_id") || "").trim() || null,
      product_type: "безалкогольный напиток",
      supplier: String(data.get("supplier") || "Ручной ввод"),
      scenario: "Ручная оценка",
      expiry_date: data.get("expiry_date"),
      lab_conclusion: data.get("lab_conclusion"),
      storage_temp_c: number("storage_temp_c", 9),
      transport_temp_c: number("transport_temp_c", 9),
      dry_matter_pct: number("dry_matter_pct", 10),
      acidity_cm3: number("acidity_cm3", 2.1),
      co2_mass_pct: number("co2_mass_pct", 0.5),
      lead_mg_kg: number("lead_mg_kg", 0.002),
      cadmium_mg_kg: number("cadmium_mg_kg", 0.001),
      arsenic_mg_kg: number("arsenic_mg_kg", 0.0015),
      kmafanm_cfu_cm3: number("kmafanm_cfu_cm3", 15),
      bgkp_cfu_cm3: number("bgkp_cfu_cm3", 0),
      yeast_cfu_cm3: number("yeast_cfu_cm3", 5),
      mold_cfu_cm3: number("mold_cfu_cm3", 2),
      use_llm: false,
    };
    const submit = form.querySelector('button[type="submit"]');
    submit.disabled = true;
    submit.textContent = "Оценка…";
    try {
      const batch = await api("/api/predict", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      $("modal-content").innerHTML = renderBatchModal(batch);
      showToast(`Партия проверена: ${RISK_LABEL[batch.risk_level]} риск.`);
    } catch (error) {
      submit.disabled = false;
      submit.textContent = "Оценить риск";
      showToast(`Ошибка проверки: ${error.message}`);
    }
  });
}

/* ------------------------------------------------------------------ init */

function bindStaticEvents() {
  $("chat-send").addEventListener("click", () => sendChat($("chat-input").value));
  $("chat-input").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      sendChat($("chat-input").value);
    }
  });
  $("chat-plus").addEventListener("click", openChecker);
  $("sidebar-new-check").addEventListener("click", openChecker);
  $("dash-new-check").addEventListener("click", openChecker);

  $("summary-export").addEventListener("click", exportSummary);
  $("summary-notify").addEventListener("click", notifySummary);
  $("summary-repeat").addEventListener("click", repeatAnalysis);

  $("dash-export").addEventListener("click", () => downloadUrl("/api/export.csv"));
  $("history-export").addEventListener("click", () => downloadUrl("/api/export.csv"));

  $("hist-apply").addEventListener("click", applyHistoryFilters);
  $("hist-reset").addEventListener("click", resetHistoryFilters);
  $("hist-search").addEventListener("keydown", (event) => {
    if (event.key === "Enter") applyHistoryFilters();
  });
  $("hist-prev").addEventListener("click", () => loadHistory(state.history.page - 1));
  $("hist-next").addEventListener("click", () => loadHistory(state.history.page + 1));

  $("notif-read-all").addEventListener("click", markAllRead);
  $("notif-period").addEventListener("change", (event) => {
    state.notifications.period = event.target.value;
    renderNotifications();
  });
  $("notif-tabs").addEventListener("click", (event) => {
    const button = event.target.closest("[data-notif-filter]");
    if (!button) return;
    state.notifications.filter = button.getAttribute("data-notif-filter");
    $("notif-tabs").querySelectorAll("button").forEach((item) => {
      item.classList.toggle("active", item === button);
    });
    renderNotifications();
  });

  $("modal").addEventListener("click", (event) => {
    if (event.target.closest("[data-close-modal]")) closeModal();
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !$("modal").hidden) closeModal();
  });

  window.addEventListener("hashchange", () => setView(currentView()));

  document.addEventListener("click", async (event) => {
    const action = event.target.closest("[data-action]");
    if (action) {
      const type = action.getAttribute("data-action");
      const batchId = action.getAttribute("data-batch");
      if (type === "export-batch" && batchId) {
        downloadUrl(`/api/batches/${encodeURIComponent(batchId)}/report`);
        return;
      }
      if (type === "notify-batch" && batchId) {
        try {
          await api("/api/notify", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ batch_id: batchId }),
          });
          showToast("Уведомление отправлено команде.");
          await loadNotifications();
        } catch (error) {
          showToast(`Ошибка уведомления: ${error.message}`);
        }
        return;
      }
      if (type === "ask-batch" && batchId) {
        closeModal();
        window.location.hash = "chat";
        setView("chat");
        sendChat(`Покажи партию ${batchId} и объясни риск`);
        return;
      }
      if (type === "open-notification") {
        await openNotification(action);
        return;
      }
      if (type === "new-check") {
        openChecker();
        return;
      }
    }

    const agentButton = event.target.closest("[data-agent]");
    if (agentButton) {
      selectAgent(agentButton.getAttribute("data-agent"));
      return;
    }

    const suggestion = event.target.closest("[data-suggestion]");
    if (suggestion) {
      sendChat(suggestion.getAttribute("data-suggestion"));
      return;
    }

    const page = event.target.closest("[data-page]");
    if (page) {
      loadHistory(Number(page.getAttribute("data-page")));
      return;
    }

    const batch = event.target.closest("[data-batch]");
    if (batch && !event.target.closest("[data-action]")) {
      openBatch(batch.getAttribute("data-batch"));
    }
  });
}

async function init() {
  bindStaticEvents();
  await Promise.allSettled([loadAgents(), loadNotifications(), loadDashboard()]);
  initChatWelcome();
  if (state.dashboard) summaryFromDashboard(state.dashboard);
  setView(currentView());
  setSyncTimes();
}

document.addEventListener("DOMContentLoaded", init);
