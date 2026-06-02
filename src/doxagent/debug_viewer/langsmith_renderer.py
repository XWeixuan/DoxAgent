"""Raw-first LangSmith custom output renderer HTML."""

# ruff: noqa: E501

from __future__ import annotations

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
      --code-bg: #101828;
      --code-ink: #e7edf8;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font: 14px/1.45 ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    main { max-width: 1280px; margin: 0 auto; padding: 18px; }
    h1 { margin: 0 0 6px; font-size: 22px; }
    h2 { margin: 0 0 10px; font-size: 16px; }
    h3 { margin: 12px 0 6px; font-size: 14px; }
    .hint { color: var(--muted); margin: 0 0 12px; }
    .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
      margin-bottom: 12px;
    }
    .grid { display: grid; grid-template-columns: repeat(12, 1fr); gap: 12px; }
    .span-12 { grid-column: span 12; }
    .kv { display: grid; grid-template-columns: 180px minmax(0, 1fr); gap: 6px 10px; overflow-wrap: anywhere; }
    .muted { color: var(--muted); }
    .badge {
      display: inline-flex;
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 2px 8px;
      margin: 2px 4px 2px 0;
      color: var(--accent);
      background: var(--soft);
      font-weight: 700;
    }
    .tree {
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fbfcfe;
      padding: 8px 10px;
      overflow: auto;
      max-height: 620px;
    }
    .tree details { margin: 2px 0 2px 14px; }
    .tree details.root { margin-left: 0; }
    .tree summary { cursor: pointer; font-weight: 700; overflow-wrap: anywhere; }
    .tree .leaf { margin: 2px 0 2px 20px; overflow-wrap: anywhere; }
    .key { color: #344054; font-weight: 700; }
    .string { color: #05603a; white-space: pre-wrap; }
    .number, .boolean { color: #175cd3; }
    .nullish { color: var(--muted); font-style: italic; }
    pre {
      overflow: auto;
      max-height: 520px;
      padding: 10px;
      background: var(--code-bg);
      color: var(--code-ink);
      border-radius: 6px;
      font-size: 12px;
      white-space: pre-wrap;
    }
    details.raw-block { margin-top: 8px; }
    details.raw-block > summary { cursor: pointer; font-weight: 700; }
    ul { margin: 6px 0 0 18px; padding: 0; }
    li { margin: 3px 0; }
    .empty { color: var(--muted); padding: 20px; text-align: center; }
    .long-text {
      max-height: 280px;
      overflow: auto;
      white-space: pre-wrap;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 8px;
      background: #fbfcfe;
    }
    @media (max-width: 760px) { .kv { grid-template-columns: 1fr; } }
  </style>
</head>
<body>
  <main>
    <h1>DoxAgent LangSmith Renderer</h1>
    <p class="hint">Raw-first renderer: complete LangSmith message, metadata, inputs, and outputs are shown before DoxAgent-specific summaries.</p>
    <div id="root" class="empty">Waiting for LangSmith output message...</div>
  </main>
  <script>
    const root = document.getElementById("root");

    function esc(value) {
      return String(value ?? "").replace(/[&<>"']/g, ch => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
      }[ch]));
    }
    function parseMaybeJson(value) {
      if (typeof value !== "string") return value;
      const trimmed = value.trim();
      if (!trimmed) return value;
      if (!trimmed.startsWith("{") && !trimmed.startsWith("[")) return value;
      try { return JSON.parse(trimmed); } catch (_err) { return value; }
    }
    function isObj(value) {
      return value && typeof value === "object" && !Array.isArray(value);
    }
    function obj(value) {
      return isObj(value) ? value : {};
    }
    function arr(value) {
      return Array.isArray(value) ? value : [];
    }
    function hasValue(value) {
      if (value === undefined || value === null) return false;
      if (typeof value === "string") return value.length > 0;
      if (Array.isArray(value)) return value.length > 0;
      if (isObj(value)) return Object.keys(value).length > 0;
      return true;
    }
    function firstValue(...values) {
      return values.find(hasValue);
    }
    function jsonText(value) {
      try { return JSON.stringify(value, null, 2); } catch (_err) { return String(value); }
    }
    function rawDetails(label, value, open = false) {
      return `<details class="raw-block" ${open ? "open" : ""}><summary>${esc(label)}</summary><pre>${esc(jsonText(value))}</pre></details>`;
    }
    function scalarHtml(value) {
      if (value === null || value === undefined) return `<span class="nullish">${esc(value)}</span>`;
      if (typeof value === "string") return `<span class="string">${esc(value)}</span>`;
      if (typeof value === "number") return `<span class="number">${esc(value)}</span>`;
      if (typeof value === "boolean") return `<span class="boolean">${esc(value)}</span>`;
      return esc(String(value));
    }
    function renderTree(value, label = "root", depth = 0) {
      const parsed = parseMaybeJson(value);
      if (Array.isArray(parsed)) {
        const children = parsed.map((item, idx) => renderTree(item, `[${idx}]`, depth + 1)).join("");
        return `<details class="${depth === 0 ? "root" : ""}" open><summary><span class="key">${esc(label)}</span> <span class="muted">array(${parsed.length})</span></summary>${children || "<div class='leaf muted'>empty array</div>"}</details>`;
      }
      if (isObj(parsed)) {
        const entries = Object.entries(parsed);
        const children = entries.map(([key, item]) => renderTree(item, key, depth + 1)).join("");
        return `<details class="${depth === 0 ? "root" : ""}" open><summary><span class="key">${esc(label)}</span> <span class="muted">object(${entries.length})</span></summary>${children || "<div class='leaf muted'>empty object</div>"}</details>`;
      }
      return `<div class="leaf"><span class="key">${esc(label)}</span>: ${scalarHtml(parsed)}</div>`;
    }
    function treePanel(title, value, rawOpen = false) {
      return `<div class="panel span-12"><h2>${esc(title)}</h2><div class="tree">${renderTree(value, title, 0)}</div>${rawDetails(`${title} raw JSON`, value, rawOpen)}</div>`;
    }
    function list(items, formatter) {
      const rows = arr(items).map(item => `<li>${formatter(item)}</li>`).join("");
      return rows ? `<ul>${rows}</ul>` : `<span class="muted">none</span>`;
    }
    function collectTextBlocks(value, blocks = [], path = "output") {
      const parsed = parseMaybeJson(value);
      if (typeof parsed === "string") {
        if (parsed.trim().length > 40) blocks.push({path, text: parsed, parsed_json: parseMaybeJson(parsed)});
        return blocks;
      }
      if (Array.isArray(parsed)) {
        parsed.forEach((item, idx) => collectTextBlocks(item, blocks, `${path}[${idx}]`));
        return blocks;
      }
      if (isObj(parsed)) {
        Object.entries(parsed).forEach(([key, item]) => collectTextBlocks(item, blocks, `${path}.${key}`));
      }
      return blocks;
    }
    function extractJsonCandidates(value, candidates = [], path = "output") {
      const parsed = parseMaybeJson(value);
      if (typeof value === "string" && typeof parsed === "object" && parsed !== null) {
        candidates.push({path, value: parsed});
      }
      if (Array.isArray(parsed)) parsed.forEach((item, idx) => extractJsonCandidates(item, candidates, `${path}[${idx}]`));
      if (isObj(parsed)) Object.entries(parsed).forEach(([key, item]) => extractJsonCandidates(item, candidates, `${path}.${key}`));
      return candidates;
    }
    function findAgentResult(output, jsonCandidates) {
      const roots = [output, output?.final_payload, output?.payload, output?.structured, output?.data, ...jsonCandidates.map(item => item.value)];
      for (const root of roots) {
        const parsed = parseMaybeJson(root);
        if (!isObj(parsed)) continue;
        const payload = isObj(parsed.payload) ? parsed.payload : {};
        if ("final_payload" in parsed || "proposed_patches" in parsed || "tool_calls" in parsed || "delegations" in parsed) return parsed;
        if ("final_payload" in payload || "proposed_patches" in payload || "tool_calls" in payload || "delegations" in payload) return payload;
      }
      return null;
    }
    function renderDoxAgentSummary(inputs, output, jsonCandidates) {
      const task = obj(firstValue(inputs.task_summary, inputs.task_spec, inputs.task, inputs.metadata?.task_summary));
      const permissions = obj(firstValue(task.permissions, inputs.permissions));
      const agentResult = findAgentResult(output, jsonCandidates);
      const finalPayload = obj(firstValue(agentResult?.final_payload, agentResult?.payload?.final_payload, agentResult?.payload, {}));
      const patches = arr(firstValue(agentResult?.proposed_patches, finalPayload.proposed_patches, agentResult?.payload?.proposed_patches));
      const toolCalls = arr(firstValue(agentResult?.tool_calls, finalPayload.tool_calls));
      const delegations = arr(firstValue(agentResult?.delegations, finalPayload.delegations));
      return `<div class="panel span-12"><h2>DoxAgent Enhanced Summary</h2>
        <div class="kv">
          <div class="muted">task_id</div><div>${esc(task.task_id ?? inputs.task_id ?? "")}</div>
          <div class="muted">ticker</div><div>${esc(task.ticker ?? inputs.ticker ?? "")}</div>
          <div class="muted">agent</div><div>${esc(task.agent_name ?? inputs.agent_name ?? "")}</div>
          <div class="muted">node</div><div>${esc(task.workflow_node ?? inputs.workflow_node ?? "")}</div>
          <div class="muted">task_type</div><div>${esc(task.task_type ?? inputs.task_type ?? "")}</div>
          <div class="muted">required_schema</div><div>${esc(task.required_output_schema ?? inputs.required_output_schema ?? "")}</div>
          <div class="muted">status</div><div><span class="badge">${esc(agentResult?.status ?? output?.status ?? "unknown")}</span></div>
          <div class="muted">is_complete</div><div>${esc(agentResult?.is_complete ?? finalPayload.is_complete ?? "unknown")}</div>
          <div class="muted">allowed_tools</div><div>${list(permissions.allowed_tools, esc)}</div>
          <div class="muted">tool_calls</div><div>${list(toolCalls, item => esc(item.tool_name || item.name || JSON.stringify(item)))}</div>
          <div class="muted">delegations</div><div>${list(delegations, item => esc(item.target_agent || item.agent || JSON.stringify(item)))}</div>
          <div class="muted">proposed_patches</div><div>${list(patches, item => esc(item.patch_id || item.operation || item.after?.expectation_name || JSON.stringify(item)))}</div>
        </div>
        ${rawDetails("recognized final_payload", finalPayload)}
      </div>`;
    }
    function renderProviderTextBlocks(blocks) {
      if (!blocks.length) return "";
      const rows = blocks.slice(0, 20).map(block => {
        const parsed = block.parsed_json;
        const parsedSection = (typeof parsed === "object" && parsed !== null)
          ? `<h3>Parsed JSON</h3><div class="tree">${renderTree(parsed, "parsed_json", 0)}</div>`
          : "";
        return `<details class="raw-block" open><summary>${esc(block.path)}</summary><div class="long-text">${esc(block.text)}</div>${parsedSection}</details>`;
      }).join("");
      return `<div class="panel span-12"><h2>Provider Text / Reasoning / Message Content</h2>${rows}</div>`;
    }
    function normalizeMessage(eventData) {
      const parsed = parseMaybeJson(eventData);
      if (isObj(parsed)) return parsed;
      return {data: parsed};
    }
    function deriveParts(message) {
      const metadata = obj(firstValue(message.metadata, message.data?.metadata, message.run?.metadata, {}));
      const inputs = firstValue(metadata.inputs, message.inputs, message.data?.inputs, message.outputs?.metadata?.inputs, {});
      const output = firstValue(message.data, message.outputs, message.output, message.result, message);
      return {metadata, inputs, output};
    }
    function render(eventData) {
      const message = normalizeMessage(eventData);
      const {metadata, inputs, output} = deriveParts(message);
      const jsonCandidates = extractJsonCandidates(output);
      const textBlocks = collectTextBlocks(output);
      root.className = "";
      root.innerHTML = `<div class="grid">
        ${treePanel("metadata.inputs", inputs)}
        ${treePanel("outputs / data", output)}
        ${renderProviderTextBlocks(textBlocks)}
        ${renderDoxAgentSummary(obj(inputs), output, jsonCandidates)}
        ${treePanel("metadata", metadata)}
        ${treePanel("complete LangSmith postMessage payload", message)}
        <div class="panel span-12">${rawDetails("complete raw JSON", message, true)}</div>
      </div>`;
    }
    window.addEventListener("message", event => render(event.data));
    window.__doxagentRenderLangSmithOutput = render;
  </script>
</body>
</html>
"""
