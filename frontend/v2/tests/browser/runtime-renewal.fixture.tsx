// Synthetic stale-cache scenario; real Runtime page, query client and renewal hook.
import { createRoot } from "react-dom/client";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import type { CaseSummary, Period } from "@contract";
import { createRuntime, RuntimeProvider } from "../../src/core/runtime";
import RuntimePage from "../../src/pages/runtime";
import { createTestAuth } from "../fixtures/auth";
import * as wire from "../fixtures/wire";
import { ApiFailure } from "../../src/core/api";
import "../../src/styles.css";
import "../../src/business.css";
import "../../src/refinement.css";

const runtime = createRuntime(createTestAuth());
const now = new Date().toISOString();
const meta = {
  contract_version: "2.0.0-draft.2",
  workflow_generation: "V2",
  request_id: "fixture",
  view_id: null,
  scope_key: "fixture",
  as_of: now,
  representation_revision: "1",
  freshness: "FRESH",
  refresh_error: null,
} as const;
const period: Period = "CURRENT_TRADING_DAY";
const context = (view: string, expired = false) => ({
  meta,
  data: {
    ...wire.context(period),
    page: "RUNTIME",
    ticker: "SNOW",
    view_id: view,
    expires_at: new Date(Date.now() + (expired ? -1000 : 900000)).toISOString(),
    clock: {
      ...wire.clock,
      next_minute_at: new Date(Date.now() + 60000).toISOString(),
    },
  },
});
const page = (view: string, items: CaseSummary[]) => ({
  meta,
  data: wire.resource({
    items,
    has_more: false,
    next_cursor: null,
    snapshot_id: view,
    limit: 20,
  }),
});
const metricNames = [
  "processed_cases",
  "new_cases",
  "old_cases",
  "hit_cases",
  "related_cases",
  "hot_path_mean_seconds",
  "w3_mean_seconds",
  "non_model_mean_seconds",
  "long_w3_cases",
  "executed_cases",
];
const metrics = (count: number) => ({
  meta,
  data: wire.resource(
    Object.fromEntries(
      metricNames.map((name) => [name, wire.metric(name, String(count))]),
    ),
  ),
});
const graph = (view: string, items: CaseSummary[]) => ({
  meta,
  data: wire.resource({
    graph_revision: 1,
    stream_cursor: "",
    nodes: [],
    edges: [],
    cases: page(view, items).data.data,
  }),
});
const rows: CaseSummary[] = Array.from({ length: 5 }, (_, i) => ({
  case_id: `case-${i}`,
  revision: 1,
  semantic_day: "2026-11-19",
  runtime_mode: "REALTIME",
  first_round_shape: "PARALLEL",
  status: "COMPLETED",
  technical_status: "OK",
  title: wire.value(`Snapshot recovery case ${i + 1}`),
  source: {
    source_id: "news",
    binding_id: "SNOW:news",
    name: "Fixture News",
    kind: "api",
  },
  received_at: now,
  completed_at: wire.value(now),
  duration_seconds: wire.value(1),
  initial_route: "ARCHIVE",
  resolved_route: "ARCHIVE",
  w3_status: null,
  first_round_tags: [],
  final_novelty: wire.value("OLD"),
  final_policy_hit: wire.value(false),
  results: ["ARCHIVE"],
  trade: { intent_count: 0, state: "NOT_APPLICABLE", reason_codes: [] },
  result_settled: true,
  trade_disposition: "NOT_EVALUATED",
  stream_item_id: `stream-${i}`,
  member_count: 1,
}));
const calls: string[] = [];
Object.assign(window, { runtimeRenewalCalls: calls });
let serial = 0;
runtime.api.request = (async (name: string, path: string) => {
  calls.push(`${name}:${path}`);
  if (name === "ReadContext") return context(`fresh-${++serial}`);
  const view = new URL(path, location.origin).searchParams.get("view_id")!;
  if (!view.startsWith("fresh-"))
    throw new ApiFailure("VIEW_EXPIRED", "快照已过期，请手动刷新。", 410);
  if (name === "Cases") return page(view, rows);
  if (name === "RuntimeMetrics") return metrics(5);
  if (name === "Graph") return graph(view, rows);
  throw new Error(`Unexpected fixture request: ${name}`);
}) as typeof runtime.api.request;

// Reopen an old empty context after a minute refresh advanced only the list view.
// Renewal must not depend on successfully reading that expired minute view.
runtime.query.setQueryData(
  [runtime.scope, "SNOW", "RUNTIME", "context", period],
  context("old", true),
);
runtime.query.setQueryData(
  [runtime.scope, "runtime-minute-view", "old"],
  context("expired-minute", true),
);
const root = "/tickers/SNOW/runtime";
runtime.query.setQueryData(
  [
    runtime.scope,
    "pages",
    "Cases",
    root + "/cases?view_id=expired-minute&limit=20",
    "expired-minute",
  ],
  { pages: [page("expired-minute", [])], pageParams: [undefined] },
);
runtime.query.setQueryData(
  [
    runtime.scope,
    "read",
    "RuntimeMetrics",
    root + "/metrics?view_id=expired-minute",
    "expired-minute",
  ],
  metrics(0),
);
runtime.query.setQueryData(
  [runtime.scope, "read", "Graph", root + "/graph?view_id=old&limit=20", "old"],
  graph("old", []),
);

createRoot(document.getElementById("root")!).render(
  <RuntimeProvider runtime={runtime}>
    <MemoryRouter initialEntries={["/ticker/SNOW/runtime"]}>
      <Routes>
        <Route path="/ticker/:ticker/runtime" element={<RuntimePage />} />
      </Routes>
    </MemoryRouter>
  </RuntimeProvider>,
);
