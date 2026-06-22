"""Small stdlib HTTP server for the local Brief State debug viewer."""

# ruff: noqa: E501

from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from doxagent.debug_viewer.langsmith_renderer import (
    LANGSMITH_RENDERER_HTML as RAW_FIRST_LANGSMITH_RENDERER_HTML,
)
from doxagent.debug_viewer.query import DebugRunQueryService

CLIENT_DISCONNECT_ERRORS = (BrokenPipeError, ConnectionAbortedError, ConnectionResetError)


def run_server(*, host: str = "127.0.0.1", port: int = 8765) -> None:
    service = DebugRunQueryService()

    class DebugViewerHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
            _handle_get(self, service)

        def do_OPTIONS(self) -> None:  # noqa: N802 - stdlib handler API
            _write_preflight(self)

        def log_message(self, format: str, *args: Any) -> None:
            print(f"[debug-viewer] {self.address_string()} - {format % args}")

    server = ThreadingHTTPServer((host, port), DebugViewerHandler)
    print(f"DoxAgent debug viewer listening on http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDoxAgent debug viewer stopped.")
    finally:
        server.server_close()


def _handle_get(handler: BaseHTTPRequestHandler, service: DebugRunQueryService) -> None:
    parsed = urlparse(handler.path)
    path = parsed.path
    query = parse_qs(parsed.query)
    try:
        if path in {"/", "/index.html"}:
            _write_html(handler, INDEX_HTML)
            return
        if path == "/langsmith-renderer.html":
            _write_html(handler, LANGSMITH_RENDERER_HTML)
            return
        if path == "/api/config":
            _write_json(handler, service.status())
            return
        if path == "/api/runs":
            ticker = _first(query.get("ticker"))
            limit = _safe_int(_first(query.get("limit")), default=50)
            _write_json(handler, {"runs": service.list_runs(ticker=ticker, limit=limit)})
            return
        if path.startswith("/api/runs/") and path.endswith("/brief-state"):
            run_id = path.removeprefix("/api/runs/").removesuffix("/brief-state").strip("/")
            _write_json(handler, service.brief_state(run_id))
            return
        if path.startswith("/api/runs/") and path.endswith("/agent-metrics"):
            run_id = path.removeprefix("/api/runs/").removesuffix("/agent-metrics").strip("/")
            _write_json(handler, service.agent_metrics(run_id))
            return
        _write_json(handler, {"error": "not_found", "path": path}, status=HTTPStatus.NOT_FOUND)
    except Exception as exc:  # pragma: no cover - exercised through manual server use
        _write_json(
            handler,
            {
                "error": "debug_viewer_error",
                "message": _safe_error_message(exc),
            },
            status=HTTPStatus.INTERNAL_SERVER_ERROR,
        )


def _write_html(handler: BaseHTTPRequestHandler, html: str) -> None:
    body = html.encode("utf-8")
    try:
        handler.send_response(HTTPStatus.OK)
        _write_common_headers(handler, "text/html; charset=utf-8", len(body))
        handler.end_headers()
        handler.wfile.write(body)
    except CLIENT_DISCONNECT_ERRORS:
        return


def _write_json(
    handler: BaseHTTPRequestHandler,
    payload: dict[str, Any],
    *,
    status: HTTPStatus = HTTPStatus.OK,
) -> None:
    body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    try:
        handler.send_response(status)
        _write_common_headers(handler, "application/json; charset=utf-8", len(body))
        handler.end_headers()
        handler.wfile.write(body)
    except CLIENT_DISCONNECT_ERRORS:
        return


def _write_preflight(handler: BaseHTTPRequestHandler) -> None:
    try:
        handler.send_response(HTTPStatus.NO_CONTENT)
        _write_common_headers(handler, "text/plain; charset=utf-8", 0)
        handler.end_headers()
    except CLIENT_DISCONNECT_ERRORS:
        return


def _write_common_headers(
    handler: BaseHTTPRequestHandler,
    content_type: str,
    content_length: int,
) -> None:
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(content_length))
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type")
    handler.send_header("Access-Control-Allow-Private-Network", "true")


def _first(value: list[str] | None) -> str | None:
    if not value:
        return None
    item = value[0].strip()
    return item or None


def _safe_int(value: str | None, *, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _safe_error_message(exc: Exception) -> str:
    message = str(exc)
    if "postgresql://" in message:
        return "Database connection failed; check DOXAGENT_DATABASE_URL."
    return message[:500]


INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>DoxAgent Brief State Viewer</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f7f8fb;
      --panel: #ffffff;
      --ink: #172033;
      --muted: #687385;
      --line: #d9dee8;
      --accent: #1967d2;
      --accent-soft: #e8f0fe;
      --bad: #b3261e;
      --warn: #a66300;
      --ok: #137333;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font: 14px/1.45 ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    header {
      position: sticky;
      top: 0;
      z-index: 3;
      background: rgba(247, 248, 251, 0.96);
      border-bottom: 1px solid var(--line);
      padding: 14px 22px;
      backdrop-filter: blur(8px);
    }
    h1 { margin: 0 0 10px; font-size: 22px; }
    h2 { margin: 0 0 12px; font-size: 18px; }
    h3 { margin: 0 0 8px; font-size: 15px; }
    .controls {
      display: grid;
      grid-template-columns: minmax(120px, 180px) minmax(260px, 1fr) auto auto;
      gap: 10px;
      align-items: center;
    }
    input, select, button {
      min-height: 36px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: var(--panel);
      color: var(--ink);
      padding: 7px 10px;
      font: inherit;
    }
    button {
      cursor: pointer;
      background: var(--accent);
      border-color: var(--accent);
      color: white;
      font-weight: 600;
    }
    button.secondary {
      background: var(--panel);
      color: var(--accent);
    }
    main { padding: 18px 22px 36px; max-width: 1500px; margin: 0 auto; }
    .tabs { display: flex; gap: 8px; margin-bottom: 14px; }
    .tab {
      background: var(--panel);
      color: var(--ink);
      border-color: var(--line);
    }
    .tab.active { background: var(--accent-soft); border-color: #b7cdf8; color: var(--accent); }
    .grid { display: grid; grid-template-columns: repeat(12, 1fr); gap: 14px; }
    .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
      min-width: 0;
    }
    .span-12 { grid-column: span 12; }
    .span-8 { grid-column: span 8; }
    .span-6 { grid-column: span 6; }
    .span-4 { grid-column: span 4; }
    .muted { color: var(--muted); }
    .status {
      display: inline-flex;
      align-items: center;
      min-height: 24px;
      padding: 2px 8px;
      border-radius: 999px;
      background: var(--accent-soft);
      color: var(--accent);
      font-size: 12px;
      font-weight: 700;
      text-transform: uppercase;
    }
    .status.missing, .status.failed { background: #fce8e6; color: var(--bad); }
    .status.warn, .status.warning { background: #fff4df; color: var(--warn); }
    .status.passed { background: #e6f4ea; color: var(--ok); }
    .kv {
      display: grid;
      grid-template-columns: 130px minmax(0, 1fr);
      gap: 6px 10px;
      overflow-wrap: anywhere;
    }
    .section {
      border-top: 1px solid var(--line);
      padding-top: 12px;
      margin-top: 12px;
    }
    .section:first-child { border-top: 0; padding-top: 0; margin-top: 0; }
    .text {
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      color: #253149;
    }
    details {
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 8px 10px;
      background: #fbfcfe;
      margin-top: 8px;
    }
    summary { cursor: pointer; font-weight: 700; }
    pre {
      overflow: auto;
      max-height: 520px;
      padding: 10px;
      background: #101828;
      color: #e7edf8;
      border-radius: 6px;
      font-size: 12px;
    }
    table { width: 100%; border-collapse: collapse; }
    th, td { border-bottom: 1px solid var(--line); padding: 8px; text-align: left; vertical-align: top; }
    th { color: var(--muted); font-size: 12px; text-transform: uppercase; }
    .bar {
      height: 8px;
      min-width: 2px;
      border-radius: 99px;
      background: var(--accent);
    }
    .metric-cell { min-width: 90px; }
    .empty {
      padding: 30px;
      text-align: center;
      color: var(--muted);
      border: 1px dashed var(--line);
      border-radius: 8px;
      background: var(--panel);
    }
    .pill-list { display: flex; flex-wrap: wrap; gap: 6px; }
    .pill { border: 1px solid var(--line); border-radius: 999px; padding: 2px 8px; color: var(--muted); }
    .readable-list { display: grid; gap: 10px; margin-top: 8px; }
    .readable-card {
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 10px;
      background: #fff;
      min-width: 0;
    }
    .readable-card h4 { margin: 0 0 6px; font-size: 14px; }
    .readable-card .text { margin: 0 0 8px; }
    .mini-kv { grid-template-columns: 120px minmax(0, 1fr); font-size: 13px; }
    .event-columns {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
      margin-top: 8px;
    }
    .event-column {
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 10px;
      background: #fff;
      min-width: 0;
    }
    .event-column h4 { margin: 0 0 6px; font-size: 14px; }
    .event-column ul { margin: 0; padding-left: 18px; }
    .event-column li { margin: 4px 0; overflow-wrap: anywhere; }
    @media (max-width: 900px) {
      .controls { grid-template-columns: 1fr; }
      .span-8, .span-6, .span-4 { grid-column: span 12; }
      .event-columns { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <header>
    <h1>DoxAgent Brief State Viewer</h1>
    <div class="controls">
      <input id="tickerInput" placeholder="Ticker filter, e.g. ASTS" />
      <select id="runSelect"></select>
      <button id="refreshButton">Refresh</button>
      <button id="latestButton" class="secondary">Latest</button>
    </div>
  </header>
  <main>
    <div class="tabs">
      <button class="tab active" data-tab="brief">Brief State</button>
      <button class="tab" data-tab="metrics">Agent Metrics</button>
    </div>
    <div id="message"></div>
    <section id="briefTab"></section>
    <section id="metricsTab" hidden></section>
  </main>
  <script>
    const state = { runs: [], selectedRunId: null, activeTab: "brief" };
    const els = {
      ticker: document.getElementById("tickerInput"),
      run: document.getElementById("runSelect"),
      refresh: document.getElementById("refreshButton"),
      latest: document.getElementById("latestButton"),
      message: document.getElementById("message"),
      brief: document.getElementById("briefTab"),
      metrics: document.getElementById("metricsTab"),
    };

    function esc(value) {
      return String(value ?? "").replace(/[&<>"']/g, ch => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
      }[ch]));
    }
    function jsonBlock(value) {
      return `<pre>${esc(JSON.stringify(value, null, 2))}</pre>`;
    }
    function status(value) {
      const text = String(value || "unknown");
      const lowered = text.toLowerCase();
      const klass = ["missing", "failed", "error"].includes(lowered)
        ? " missing"
        : ["warn", "warning"].includes(lowered)
          ? " warning"
          : ["passed", "completed", "clear", "success", "succeeded"].includes(lowered)
            ? " passed"
            : "";
      return `<span class="status${klass}">${esc(text)}</span>`;
    }
    function details(label, value) {
      return `<details><summary>${esc(label)}</summary>${jsonBlock(value)}</details>`;
    }
    function isObject(value) {
      return value && typeof value === "object" && !Array.isArray(value);
    }
    function hasValue(value) {
      if (value === null || value === undefined) return false;
      if (Array.isArray(value)) return value.length > 0;
      if (isObject(value)) return Object.keys(value).length > 0;
      return String(value).trim().length > 0;
    }
    function asList(value) {
      if (Array.isArray(value)) return value;
      return hasValue(value) ? [value] : [];
    }
    function pickText(value, keys) {
      if (!isObject(value)) return "";
      for (const key of keys) {
        if (hasValue(value[key])) return humanText(value[key]);
      }
      return "";
    }
    function humanText(value) {
      if (value === null || value === undefined) return "";
      if (Array.isArray(value)) return value.map(item => humanText(item)).filter(Boolean).join("; ");
      if (isObject(value)) {
        return pickText(value, ["description", "summary", "text", "name", "title", "value"])
          || JSON.stringify(value);
      }
      return String(value);
    }
    function kvField(label, value) {
      const text = humanText(value).trim();
      return text ? `<div class="muted">${esc(label)}</div><div>${esc(text)}</div>` : "";
    }
    function parseLabeledFactText(value) {
      const text = humanText(value).trim();
      const matches = [...text.matchAll(/(?:^\s*|[;；]\s*)(fact|when|why_it_matters|pricing_status)\s*[:：]\s*/gi)];
      if (!matches.length || matches[0].index !== 0) return {};
      const result = {};
      matches.forEach((match, index) => {
        const key = match[1].toLowerCase();
        const start = match.index + match[0].length;
        const end = index + 1 < matches.length ? matches[index + 1].index : text.length;
        const fieldText = text.slice(start, end).replace(/^[;；]\s*|\s*[;；]\s*$/g, "").trim();
        if (fieldText) result[key] = fieldText;
      });
      return result;
    }
    function factDescriptionFields(value) {
      if (isObject(value)) {
        return {
          fact: humanText(value.fact || value.description || value.summary || ""),
          when: humanText(value.when || value.date || value.time || ""),
          why_it_matters: humanText(value.why_it_matters || value.why || value.importance || ""),
          pricing_status: humanText(value.pricing_status || value.market_pricing || ""),
        };
      }
      return parseLabeledFactText(value);
    }
    function renderEvidence(refs) {
      const items = asList(refs);
      return items.length ? details(`Evidence (${items.length})`, items) : "";
    }
    function renderRealizedFacts(items) {
      const facts = asList(items);
      return `<div class="section">
        <h3>Realized Facts (${facts.length})</h3>
        ${facts.length ? `<div class="readable-list">${facts.map((item, index) => {
          const fact = isObject(item) ? item : { description: item };
          const price = isObject(fact.price_reaction) ? fact.price_reaction : {};
          const descriptionValue = fact.description || fact.summary || fact.fact || fact.event;
          const descriptionFields = factDescriptionFields(descriptionValue);
          const hasDescriptionFields = Object.values(descriptionFields).some(hasValue);
          const description = hasDescriptionFields ? "" : humanText(descriptionValue);
          const eventId = pickText(fact, ["event_id", "id"]);
          const rows = [
            kvField("Fact", descriptionFields.fact),
            kvField("When", descriptionFields.when),
            kvField("Why It Matters", descriptionFields.why_it_matters),
            kvField("Pricing Status", descriptionFields.pricing_status),
            kvField("Event ID", eventId),
            kvField("Price Change", pickText(price, ["price_change", "change"])),
            kvField("Price Pattern", pickText(price, ["price_pattern", "pattern"])),
            kvField("Interpretation", pickText(price, ["interpretation", "summary", "text"])),
            !isObject(fact.price_reaction) ? kvField("Price Reaction", fact.price_reaction) : "",
          ].join("");
          return `<div class="readable-card">
            <h4>Fact ${index + 1}${eventId ? ` <span class="pill">${esc(eventId)}</span>` : ""}</h4>
            ${description ? `<p class="text">${esc(description)}</p>` : ""}
            ${rows ? `<div class="kv mini-kv">${rows}</div>` : ""}
            ${renderEvidence(fact.evidence_refs)}
            ${isObject(price) ? renderEvidence(price.evidence_refs) : ""}
            ${details("Raw fact JSON", fact)}
          </div>`;
        }).join("")}</div>` : `<div class="empty">No realized facts available.</div>`}
      </div>`;
    }
    function renderKeyVariables(items) {
      const variables = asList(items);
      return `<div class="section">
        <h3>Key Variables (${variables.length})</h3>
        ${variables.length ? `<div class="readable-list">${variables.map((item, index) => {
          const variable = isObject(item) ? item : { name: item };
          const name = pickText(variable, ["name", "variable", "variable_name"]) || `Variable ${index + 1}`;
          const rows = [
            kvField("Variable ID", pickText(variable, ["variable_id", "id"])),
            kvField("Current Status", pickText(variable, ["current_status", "current_state", "status"])),
            kvField("Certainty", pickText(variable, ["certainty", "confidence"])),
            kvField("Unresolved", pickText(variable, ["unresolved", "unknowns", "open_questions"])),
          ].join("");
          return `<div class="readable-card">
            <h4>${esc(name)}</h4>
            ${rows ? `<div class="kv mini-kv">${rows}</div>` : ""}
            ${renderEvidence(variable.evidence_refs)}
            ${details("Raw variable JSON", variable)}
          </div>`;
        }).join("")}</div>` : `<div class="empty">No key variables available.</div>`}
      </div>`;
    }
    function renderEventItems(items, emptyText) {
      const events = asList(items);
      return events.length
        ? `<ul>${events.map(item => `<li>${esc(humanText(item))}</li>`).join("")}</ul>`
        : `<div class="muted">${esc(emptyText)}</div>`;
    }
    function renderEventMonitoringDirection(value) {
      const monitoring = isObject(value) ? value : {};
      const knownNotice = pickText(monitoring, ["known_event_notice", "notice", "summary", "text"]);
      return `<div class="section">
        <h3>Event Monitoring Direction</h3>
        ${knownNotice ? `<p class="text">${esc(knownNotice)}</p>` : `<div class="empty">No event monitoring direction available.</div>`}
        <div class="event-columns">
          <div class="event-column">
            <h4>Positive Events</h4>
            ${renderEventItems(monitoring.positive_events, "No positive event triggers listed.")}
          </div>
          <div class="event-column">
            <h4>Negative Events</h4>
            ${renderEventItems(monitoring.negative_events, "No negative event triggers listed.")}
          </div>
        </div>
        ${details("Raw Event Monitoring JSON", value || {})}
      </div>`;
    }
    async function getJson(url) {
      const response = await fetch(url);
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.message || payload.error || response.statusText);
      return payload;
    }
    function setMessage(html) {
      els.message.innerHTML = html ? `<div class="panel span-12">${html}</div>` : "";
    }
    async function loadRuns(preferLatest = false) {
      const ticker = els.ticker.value.trim();
      const params = new URLSearchParams({ limit: "50" });
      if (ticker) params.set("ticker", ticker.toUpperCase());
      const payload = await getJson(`/api/runs?${params.toString()}`);
      state.runs = payload.runs || [];
      if (!state.runs.length) {
        els.run.innerHTML = `<option>No persisted runs found</option>`;
        state.selectedRunId = null;
        renderEmpty();
        return;
      }
      if (preferLatest || !state.selectedRunId || !state.runs.some(run => run.run_id === state.selectedRunId)) {
        state.selectedRunId = state.runs[0].run_id;
      }
      els.run.innerHTML = state.runs.map(run => {
        const label = `${run.ticker} | ${run.workflow_state} | ${run.created_at} | ${run.run_id}`;
        return `<option value="${esc(run.run_id)}"${run.run_id === state.selectedRunId ? " selected" : ""}>${esc(label)}</option>`;
      }).join("");
      await loadSelectedRun();
    }
    async function loadSelectedRun() {
      if (!state.selectedRunId) return;
      setMessage("");
      if (state.activeTab === "brief") {
        const payload = await getJson(`/api/runs/${encodeURIComponent(state.selectedRunId)}/brief-state`);
        renderBrief(payload);
      } else {
        const payload = await getJson(`/api/runs/${encodeURIComponent(state.selectedRunId)}/agent-metrics`);
        renderMetrics(payload);
      }
    }
    function renderEmpty() {
      els.brief.innerHTML = `<div class="empty">No persisted runs found. Run an initialization workflow with DOXAGENT_STORAGE_MODE=postgres, then refresh this page.</div>`;
      els.metrics.innerHTML = "";
    }
    function runPanel(payload) {
      const cp = payload.latest_checkpoint || {};
      return `<div class="panel span-12">
        <h2>Run</h2>
        <div class="kv">
          <div class="muted">Run ID</div><div>${esc(payload.run?.run_id)}</div>
          <div class="muted">Ticker</div><div>${esc(payload.run?.ticker)}</div>
          <div class="muted">State</div><div>${status(payload.run?.workflow_state)}</div>
          <div class="muted">Created</div><div>${esc(payload.run?.created_at)}</div>
          <div class="muted">Updated</div><div>${esc(payload.run?.updated_at)}</div>
          <div class="muted">Next Node</div><div>${esc(cp.next_node || "none")}</div>
          <div class="muted">Completed</div><div class="pill-list">${(cp.completed_nodes || []).map(x => `<span class="pill">${esc(x)}</span>`).join("")}</div>
        </div>
      </div>`;
    }
    function renderValidators(payload) {
      const hard = payload.hard_validators || {};
      const validators = hard.validators || [];
      return `<div class="panel span-12">
        <h2>Hard Validators ${status(hard.status)}</h2>
        <div class="kv">
          <div class="muted">Validators</div><div>${esc(hard.summary?.validator_count || 0)}</div>
          <div class="muted">Failed</div><div>${esc(hard.summary?.failed_count || 0)}</div>
          <div class="muted">Warnings</div><div>${esc(hard.summary?.warning_count || 0)}</div>
          <div class="muted">Findings</div><div>${esc(hard.summary?.finding_count || 0)}</div>
        </div>
        ${validators.map(item => `<div class="section">
          <h3>${esc(item.title || item.validator_id)} ${status(item.status)}</h3>
          <div class="kv">
            <div class="muted">Checked Items</div><div>${esc(item.checked_items || 0)}</div>
            <div class="muted">Errors</div><div>${esc(item.summary?.error_count || 0)}</div>
            <div class="muted">Warnings</div><div>${esc(item.summary?.warning_count || 0)}</div>
            <div class="muted">Scope</div><div>${esc(item.scope || "")}</div>
          </div>
          ${details(`Findings (${(item.findings || []).length})`, item.findings || [])}
          ${Object.keys(item.metadata || {}).length ? details("Metadata", item.metadata || {}) : ""}
        </div>`).join("") || `<div class="empty">No hard validator results available.</div>`}
      </div>`;
    }
    function renderBrief(payload) {
      const global = payload.global_research || {};
      const expectations = payload.expectation_units || [];
      els.brief.hidden = false;
      els.metrics.hidden = true;
      els.brief.innerHTML = `<div class="grid">
        ${runPanel(payload)}
        ${renderValidators(payload)}
        <div class="panel span-12">
          <h2>Document 1: Global Research ${status(global.status)}</h2>
          ${(global.sections || []).map(section => `<div class="section">
            <h3>${esc(section.label)} ${status(section.status)}</h3>
            <div class="kv">
              <div class="muted">Author</div><div>${esc(section.author_agent || "unknown")}</div>
              <div class="muted">Reviewers</div><div>${esc((section.reviewer_agents || []).join(", ") || "none")}</div>
              <div class="muted">Summary</div><div>${esc(section.summary || "")}</div>
            </div>
            <p class="text">${esc(section.text || "")}</p>
            ${details(`Evidence (${(section.evidence_refs || []).length})`, section.evidence_refs || [])}
          </div>`).join("") || `<div class="empty">${esc(global.message || "Document 1 missing.")}</div>`}
        </div>
        <div class="panel span-12">
          <h2>Document 2: Expectation Units</h2>
          ${expectations.length ? expectations.map(exp => `<div class="section">
            <h3>${esc(exp.expectation_name || exp.expectation_id)} ${status(exp.direction)}</h3>
            <div class="kv">
              <div class="muted">Expectation ID</div><div>${esc(exp.expectation_id)}</div>
              <div class="muted">Blocked</div><div>${exp.blockers?.is_blocked ? status("blocked") : status("clear")}</div>
              <div class="muted">Why It Matters</div><div>${esc(exp.why_it_matters || "")}</div>
              <div class="muted">Realized Facts</div><div>${esc(exp.realized_facts_summary || "")}</div>
            </div>
            <div class="section"><h3>Market View</h3><p class="text">${esc(exp.market_view?.text || "")}</p></div>
            ${renderRealizedFacts(exp.realized_facts)}
            ${renderKeyVariables(exp.key_variables)}
            ${renderEventMonitoringDirection(exp.event_monitoring_direction)}
            ${details(`Commit Trace (${(exp.commit_trace || []).length})`, exp.commit_trace || [])}
            ${details("Blockers", exp.blockers || {})}
          </div>`).join("") : `<div class="empty">expectation_unit is missing, blocked, or not yet promoted.</div>`}
        </div>
        <div class="panel span-6">${details(`Working Memory (${(payload.working_memory || []).length})`, payload.working_memory || [])}</div>
        <div class="panel span-6">${details(`Commit Log (${(payload.commit_log || []).length})`, payload.commit_log || [])}</div>
        <div class="panel span-12">${details(`Evidence Refs (${(payload.evidence_refs || []).length})`, payload.evidence_refs || [])}</div>
      </div>`;
    }
    function renderMetrics(payload) {
      const agents = payload.agents || [];
      const maxLoops = Math.max(1, ...agents.map(a => Number(a.agent_loops || 0)));
      els.brief.hidden = true;
      els.metrics.hidden = false;
      els.metrics.innerHTML = `<div class="grid">
        ${runPanel(payload)}
        <div class="panel span-12">
          <h2>Agent Metrics</h2>
          <div class="kv">
            <div class="muted">Total Loops</div><div>${esc(payload.totals?.agent_loops || 0)}</div>
            <div class="muted">Total Tool Calls</div><div>${esc(payload.totals?.tool_calls || 0)}</div>
            <div class="muted">Delegations</div><div>${esc(payload.totals?.delegations || 0)}</div>
            <div class="muted">Objections</div><div>${esc(payload.totals?.objections || 0)}</div>
          </div>
          <table>
            <thead><tr><th>Agent</th><th>Loops</th><th>Tools</th><th>Delegations</th><th>Objections</th><th>Failures</th><th>Audit</th></tr></thead>
            <tbody>${agents.map(agent => `<tr>
              <td><strong>${esc(agent.agent)}</strong></td>
              <td class="metric-cell">${esc(agent.agent_loops)}<div class="bar" style="width:${Math.max(3, Number(agent.agent_loops || 0) / maxLoops * 100)}%"></div></td>
              <td>${esc(agent.tool_call_total)} ${details("tool counts", agent.tool_counts || {})}</td>
              <td>${esc(agent.delegation_total)}</td>
              <td>${esc(agent.objection_total)}</td>
              <td>${esc(agent.failed_results || 0)}</td>
              <td>${esc(agent.audit_status || "unknown")}</td>
            </tr>
            <tr><td colspan="7">${details("trajectory / warnings / objects", {
              warnings: agent.warnings || [],
              trajectory: agent.trajectory || [],
              delegations: agent.delegations || [],
              objections: agent.objections || [],
              checkpoint_errors: agent.checkpoint_errors || []
            })}</td></tr>`).join("")}</tbody>
          </table>
        </div>
      </div>`;
    }
    document.querySelectorAll(".tab").forEach(button => {
      button.addEventListener("click", async () => {
        document.querySelectorAll(".tab").forEach(item => item.classList.remove("active"));
        button.classList.add("active");
        state.activeTab = button.dataset.tab;
        await loadSelectedRun();
      });
    });
    els.refresh.addEventListener("click", () => loadRuns(false).catch(err => setMessage(`<span class="status missing">Error</span> ${esc(err.message)}`)));
    els.latest.addEventListener("click", () => loadRuns(true).catch(err => setMessage(`<span class="status missing">Error</span> ${esc(err.message)}`)));
    els.run.addEventListener("change", async () => {
      state.selectedRunId = els.run.value;
      await loadSelectedRun();
    });
    els.ticker.addEventListener("keydown", event => {
      if (event.key === "Enter") loadRuns(true).catch(err => setMessage(`<span class="status missing">Error</span> ${esc(err.message)}`));
    });
    loadRuns(true).catch(err => {
      renderEmpty();
      setMessage(`<span class="status missing">Error</span> ${esc(err.message)}`);
    });
  </script>
</body>
</html>
"""

LANGSMITH_RENDERER_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>DoxAgent LangSmith Renderer</title>
  <style>
    :root {
      --bg: #f7f8fb;
      --panel: #ffffff;
      --ink: #172033;
      --muted: #667085;
      --line: #d9dee8;
      --accent: #1967d2;
      --soft: #e8f0fe;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font: 14px/1.45 ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    main { max-width: 1180px; margin: 0 auto; padding: 18px; }
    h1 { margin: 0 0 12px; font-size: 22px; }
    h2 { margin: 0 0 10px; font-size: 16px; }
    .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
      margin-bottom: 12px;
    }
    .grid { display: grid; grid-template-columns: repeat(12, 1fr); gap: 12px; }
    .span-12 { grid-column: span 12; }
    .span-6 { grid-column: span 6; }
    .kv { display: grid; grid-template-columns: 150px minmax(0, 1fr); gap: 6px 10px; overflow-wrap: anywhere; }
    .muted { color: var(--muted); }
    .pill {
      display: inline-flex;
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 2px 8px;
      margin: 2px;
      color: var(--muted);
      background: #fbfcfe;
    }
    ul { margin: 6px 0 0 18px; padding: 0; }
    li { margin: 3px 0; }
    pre {
      overflow: auto;
      max-height: 420px;
      padding: 10px;
      background: #101828;
      color: #e7edf8;
      border-radius: 6px;
      font-size: 12px;
    }
    details { margin-top: 8px; }
    summary { cursor: pointer; font-weight: 700; }
    .empty { color: var(--muted); padding: 20px; text-align: center; }
    .status { color: var(--accent); background: var(--soft); border-radius: 999px; padding: 2px 8px; font-weight: 700; }
    @media (max-width: 760px) { .span-6 { grid-column: span 12; } .kv { grid-template-columns: 1fr; } }
  </style>
</head>
<body>
  <main>
    <h1>DoxAgent LangSmith Renderer</h1>
    <div id="root" class="empty">Waiting for LangSmith output message...</div>
  </main>
  <script>
    const root = document.getElementById("root");
    function esc(value) {
      return String(value ?? "").replace(/[&<>"']/g, ch => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
      }[ch]));
    }
    function jsonBlock(value) {
      return `<pre>${esc(JSON.stringify(value, null, 2))}</pre>`;
    }
    function details(label, value) {
      return `<details><summary>${esc(label)}</summary>${jsonBlock(value)}</details>`;
    }
    function object(value) {
      return value && typeof value === "object" && !Array.isArray(value) ? value : {};
    }
    function array(value) {
      return Array.isArray(value) ? value : [];
    }
    function firstObject(...values) {
      for (const value of values) {
        const candidate = object(value);
        if (Object.keys(candidate).length) return candidate;
      }
      return {};
    }
    function dig(value, path) {
      let current = value;
      for (const key of path) {
        if (!current || typeof current !== "object") return undefined;
        current = current[key];
      }
      return current;
    }
    function list(items, formatter) {
      const rows = array(items).map(item => `<li>${formatter(item)}</li>`).join("");
      return rows ? `<ul>${rows}</ul>` : `<span class="muted">none</span>`;
    }
    function renderPermissions(permissions) {
      return `<div class="panel span-6"><h2>Permissions</h2><div class="kv">
        <div class="muted">read</div><div>${list(permissions.readable_context_scopes, esc)}</div>
        <div class="muted">write</div><div>${list(permissions.writable_targets, esc)}</div>
        <div class="muted">tools</div><div>${list(permissions.allowed_tools, esc)}</div>
        <div class="muted">delegate</div><div>${esc(permissions.can_delegate)}</div>
        <div class="muted">raise_objection</div><div>${esc(permissions.can_raise_objection)}</div>
        <div class="muted">private_memory</div><div>${esc(permissions.can_access_private_memory)}</div>
      </div></div>`;
    }
    function renderContext(inputs) {
      const context = firstObject(inputs.context_snapshot, inputs.context, dig(inputs, ["task_summary", "input_context"]));
      const completed = array(context.completed_nodes || dig(context, ["workflow", "completed_nodes"]));
      const stableDocs = array(context.stable_documents || dig(context, ["belief_state", "stable_documents"]));
      const workingMemory = array(context.working_memory || context.working_memory_summary);
      const sections = object(dig(context, ["global_research_context", "sections"]) || dig(context, ["global_research", "sections"]));
      return `<div class="panel span-6"><h2>Context</h2>
        <div class="muted">completed_nodes</div>${list(completed, esc)}
        <div class="muted">stable_documents</div>${list(stableDocs, item => esc(typeof item === "object" ? JSON.stringify(item) : item))}
        <div class="muted">working_memory</div>${list(workingMemory, item => esc(`${item.author_agent || item.agent || "?"}: ${item.content_type || item.type || JSON.stringify(item)}`))}
        <div class="muted">global_research_sections</div>${list(Object.entries(sections), item => esc(`${item[0]}: ${item[1]?.summary || item[1]?.text || ""}`))}
        ${details("context raw", context)}
      </div>`;
    }
    function renderExpected(inputs) {
      const task = object(inputs.task_summary || inputs.task_spec || inputs.task);
      const contract = object(inputs.output_contract || inputs.expected_output);
      return `<div class="panel span-12"><h2>Expected Output</h2>
        <div class="kv">
          <div class="muted">required_schema</div><div>${esc(task.required_output_schema)}</div>
          <div class="muted">contract</div><div>${Object.keys(contract).length ? esc(JSON.stringify(contract)) : "AgentResult-compatible JSON"}</div>
        </div>
      </div>`;
    }
    function patchSummary(patch) {
      const after = object(patch.after);
      return `${after.expectation_name || after.document_id || patch.patch_id || "patch"} · ${after.direction || ""} · ${patch.operation || ""} · ${patch.rationale || ""}`;
    }
    function toolSummary(tool) {
      return `${tool.tool_name || tool.name || "tool"} · ${tool.status || ""} · ${tool.output_summary || ""}`;
    }
    function delegationSummary(delegation) {
      return `${delegation.target_agent || "agent"} · ${delegation.task_type || ""} · ${delegation.question || ""}`;
    }
    function renderOutput(data) {
      const structured = firstObject(data.final_payload, data.structured, data.payload?.structured, data.payload, data);
      const finalPayload = firstObject(data.final_payload, structured.final_payload, structured);
      const patches = array(data.proposed_patches || structured.proposed_patches || finalPayload.proposed_patches);
      const toolCalls = array(data.tool_calls || structured.tool_calls || finalPayload.tool_calls);
      const delegations = array(data.delegations || structured.delegations || finalPayload.delegations);
      return `<div class="panel span-12"><h2>LLM Output</h2>
        <div class="kv">
          <div class="muted">is_complete</div><div><span class="status">${esc(data.is_complete ?? structured.is_complete ?? "unknown")}</span></div>
          <div class="muted">completion_reason</div><div>${esc(data.completion_reason || structured.completion_reason || "")}</div>
          <div class="muted">status</div><div>${esc(data.status || structured.status || "")}</div>
        </div>
        <div class="muted">tool_calls</div>${list(toolCalls, toolSummary)}
        <div class="muted">delegations</div>${list(delegations, delegationSummary)}
        <div class="muted">proposed_patches</div>${list(patches, patchSummary)}
        ${details("final_payload", finalPayload)}
      </div>`;
    }
    function render(eventData) {
      const message = object(eventData);
      const data = message.data ?? message;
      const inputs = object(message.metadata?.inputs || message.inputs);
      const task = object(inputs.task_summary || inputs.task_spec || inputs.task);
      root.className = "";
      root.innerHTML = `<div class="grid">
        <div class="panel span-12"><h2>DoxAgent Run</h2><div class="kv">
          <div class="muted">task_id</div><div>${esc(task.task_id)}</div>
          <div class="muted">ticker</div><div>${esc(task.ticker)}</div>
          <div class="muted">agent</div><div>${esc(task.agent_name)}</div>
          <div class="muted">node</div><div>${esc(task.workflow_node)}</div>
          <div class="muted">task_type</div><div>${esc(task.task_type)}</div>
          <div class="muted">required_schema</div><div>${esc(task.required_output_schema)}</div>
        </div></div>
        ${renderPermissions(object(task.permissions))}
        ${renderContext(inputs)}
        ${renderExpected(inputs)}
        ${renderOutput(object(data))}
        <div class="panel span-12">${details("Raw JSON", message)}</div>
      </div>`;
    }
    window.addEventListener("message", event => render(event.data));
    window.__doxagentRenderLangSmithOutput = render;
  </script>
</body>
</html>
"""

LANGSMITH_RENDERER_HTML = RAW_FIRST_LANGSMITH_RENDERER_HTML
