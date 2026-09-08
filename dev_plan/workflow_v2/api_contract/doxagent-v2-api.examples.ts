/** Synthetic contract examples; no observed production values and no executable requests. */
import type {
  Coverage, ErrorResponse, GraphDelta, Metric, Resource, StartTickerRequest, Value,
} from "./doxagent-v2-api.types";

export const confirmedZero = { state: "AVAILABLE", value: 0, reason: null } satisfies Value<number>;
export const missingCachedInput = {
  state: "NOT_RECORDED", value: null, reason: "CACHED_INPUT_MISSING",
} satisfies Value<number>;
export const codexCost = {
  state: "NOT_APPLICABLE", value: null, reason: "CODEX_SUBSCRIPTION_NOT_PRICED",
} satisfies Value<string>;

const complete = {
  state: "COMPLETE", reasons: [], known_count: 0, excluded_count: 0,
  observed_through: "2026-09-04T06:00:00Z",
} satisfies Coverage;

export const zeroBaseComparison = {
  metric_id: "messages", unit: "COUNT",
  current: { state: "AVAILABLE", value: "3", reason: null },
  previous: { state: "AVAILABLE", value: "0", reason: null },
  change_pct: { state: "NOT_APPLICABLE", value: null, reason: "PREVIOUS_ZERO" },
  current_coverage: { ...complete, known_count: 3 }, previous_coverage: complete,
  provisional: false,
} satisfies Metric;

export const knownEmpty = {
  state: "EMPTY", data: [], reason: null, coverage: complete,
} satisfies Resource<string[]>;

export const startMonitoring = {
  ticker: "MU", monitor_mode: "MESSAGE_MONITORING", initialization: "FORCE_INITIALIZE",
} satisfies StartTickerRequest;

export const staleBinding = {
  error: {
    code: "REVISION_CONFLICT", message: "Binding has changed; reload it before saving.",
    retryable: false, request_id: "example-request-1", fields: [], content_id: null,
  },
} satisfies ErrorResponse;

// Counts replace previous values. Replaying this delta must not add another execution.
export const graphFillCorrection = {
  previous_graph_revision: 10, graph_revision: 11,
  upsert_cases: [], remove_case_ids: [], replace_nodes: [],
  replace_edges: [{ edge_id: "W3:TRADE_EXECUTION", from: "W3", to: "TRADE_EXECUTION", case_count: 2 }],
} satisfies GraphDelta;
