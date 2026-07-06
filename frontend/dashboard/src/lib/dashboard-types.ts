export type JsonPrimitive = string | number | boolean | null
export type JsonValue = JsonPrimitive | JsonObject | JsonValue[]
export type JsonObject = { [key: string]: JsonValue | undefined }

export type HealthStatus = "normal" | "degraded" | "blocked" | "unknown"
export type RunStatus =
  | "initializing"
  | "running"
  | "paused"
  | "stopped"
  | "degraded"
  | "blocked"
export type StatusColor = "green" | "blue" | "yellow" | "red" | "gray"
export type DocumentType = "document1" | "document2" | "document3"
export type VersionStatus = "current" | "historical"
export type DocumentReasonLabel =
  | "workflow_generated"
  | "agent_refreshed"
  | "manual_activated"
  | "monitoring_policy_reviewed"
  | "unknown"
export type ActionType = "DTC" | "EBA" | "NULL" | "Irrelevant"
export type MonitorMode = "message_monitoring" | "paper_trading" | "broker_trading"
export type BacktestPeriod = "7d" | "15d" | "30d"
export type BacktestRunStatus =
  | "queued"
  | "initializing_documents"
  | "collecting_dataset"
  | "replaying"
  | "draining_runtime"
  | "completed"
  | "failed"
  | "cancelled"
export type Period = "today" | "7d" | "30d"
export type AuditStatus =
  | "not_started"
  | "calculating"
  | "completed"
  | "failed"
  | "missing"
  | "partial"

export interface ApiMeta {
  request_id: string
  generated_at: string
  source: string
}

export interface ApiResponse<T> {
  data: T
  meta: ApiMeta
}

export interface ApiErrorPayload {
  error: {
    code: string
    message: string
    retryable: boolean
    details?: JsonObject
  }
  request_id: string
}

export interface PageInfo {
  limit: number
  next_cursor: string | null
  has_more: boolean
  total_count?: number
}

export interface PageResult<T> {
  items: T[]
  page: PageInfo
}

export interface TickerCard {
  ticker: string
  status: RunStatus
  status_label: string
  health: HealthStatus
  session_phase: string
  monitor_mode?: MonitorMode | string
  startup_progress?: StartupProgress | null
  started_at: string | null
  updated_at: string | null
  last_message_at: string | null
  last_worker_processed_at: string | null
  today_dtc_count: number
  today_cost_usd: number | null
  last_error: string | null
}

export interface BacktestProgress {
  total_events: number
  collected_events: number
  injected_events: number
  processed_events: number
  failed_events: number
  percent: number
}

export interface BacktestDatasetInfo {
  dataset_id: string | null
  source_type_counts: Record<string, number>
  diagnostics: string[]
  source: JsonObject
}

export interface BacktestRuntimeInfo {
  runtime_sqlite_path: string | null
  execution_count: number
  trade_intent_count: number
  known_event_patch_count: number
  exception_count: number
}

export interface BacktestRun {
  run_id: string
  ticker: string
  period: BacktestPeriod | string
  period_days: number
  status: BacktestRunStatus | string
  status_label: string
  health: HealthStatus
  force_initialize: boolean
  replay_interval_ms: number
  progress: BacktestProgress
  dataset: BacktestDatasetInfo
  runtime: BacktestRuntimeInfo
  current_event_id: string | null
  current_event_time: string | null
  last_error: string | null
  cancel_requested: boolean
  can_cancel: boolean
  created_at: string
  started_at: string | null
  completed_at: string | null
  updated_at: string
}

export interface StartupProgress {
  status: "running" | "blocked" | "completed" | string
  status_label: string
  current_step_id: string | null
  retryable: boolean
  message: string | null
  updated_at: string | null
  steps: StartupProgressStep[]
}

export interface StartupProgressStep {
  step_id: string
  label: string
  status: "pending" | "running" | "completed" | "blocked" | string
  progress: number
}

export interface OverviewState {
  generated_at: string
  system: {
    container_status: HealthStatus
    current_session_phase?: string
    current_session_label?: "运行时段" | "盘后休眠" | string
    dashboard_api_status: HealthStatus
    message_bus_status: HealthStatus
    status_color: StatusColor
  }
  kpis: {
    running_ticker_count: number
    today_message_count: number
    today_dtc_count: number
    today_token_cost_usd: number | null
    exception_count: number
  }
  tickers: TickerCard[]
}

export interface TickerDetail {
  ticker: string
  state: {
    status: RunStatus
    health: HealthStatus
    session_phase: string
    monitor_mode?: MonitorMode | string
    document_run_id: string | null
    last_error: string | null
  }
  document_status: JsonObject
  message_bus_status: JsonObject
  runtime_status: JsonObject
  audit_summary: JsonObject
}

export interface OperationResult {
  operation: "start" | "pause" | "delete" | "restart" | "monitor_mode"
  status: string
  ticker: string
  ticker_state?: {
    status: RunStatus
    health: HealthStatus
    monitor_mode?: MonitorMode | string
  }
  audit_id?: string
  history_deleted?: boolean
}

export interface DocumentField {
  key: string
  label: string
  value: JsonValue
}

export interface DocumentCard {
  card_id: string
  title: string
  updated_at: string | null
  summary: string | null
  fields: DocumentField[]
}

export interface DashboardDocument {
  document_type: DocumentType
  document_type_label: string
  document_id: string
  generated_at: string | null
  updated_at: string | null
  version_status: VersionStatus
  availability: string
  cards: DocumentCard[]
  raw?: JsonObject
}

export interface DocumentsCurrent {
  ticker: string
  document_run_id: string | null
  documents: DashboardDocument[]
}

export interface DocumentRevision {
  ticker: string
  document_run_id: string | null
  document1_updated_at: string | null
  document2_updated_at: string | null
  document3_updated_at: string | null
  known_events_updated_at: string | null
  policies_updated_at: string | null
}

export interface DocumentVersion {
  version_id: string
  document_run_id: string
  document_id: string
  document_type: DocumentType
  generated_at: string | null
  updated_at: string | null
  version_status: VersionStatus
  summary: string | null
  reason_label?: DocumentReasonLabel
  reason_text?: string | null
  updated_by_label?: string | null
}

export interface DocumentVersionDetail {
  ticker: string
  version: DocumentVersion
  document: DashboardDocument
}

export interface KnownEvent {
  event_id: string
  event_name: string
  event_time_or_window: string | null
  description: string | null
  related_expectation_ids: string[]
  duplicate_detection_keys: string[]
  source: string
  updated_at: string | null
}

export interface Policy {
  policy_id: string
  expectation_id: string | null
  action_type: ActionType
  title: string
  trigger_condition: string | null
  severity: string | null
  updated_at: string | null
}

export interface MessageBusOverview {
  ticker: string
  uptime_seconds: number
  today_raw_message_count: number
  today_event_count: number
  media_enrichment_success_rate: number | null
  healthy_channel_count: number
  total_channel_count: number
  last_error_message: string | null
}

export interface MessageItem {
  message_id: string
  raw_message_id: string
  ticker: string
  source_id: string
  source_label: string
  source_type: string
  collected_at: string | null
  published_at: string | null
  title: string
  summary: string | null
  body: string | null
  url: string | null
  processing_status: string
  runtime_execution_id: string | null
}

export interface MessageSourceConfig {
  source_id: string
  display_name: string
  source_type: string
  interface_type: string
  enabled: boolean
  poll_interval_seconds: number
  binding: {
    binding_id: string
    ticker: string
    source_id: string
    enabled: boolean
    parameters: Record<string, JsonValue>
  }
  poll_state: {
    status: string
    last_success_at: string | null
    last_error_message: string | null
    last_poll_new_message_count?: number | null
    last_latency_ms?: number | null
  }
  user_only_fields: string[]
  agent_mutable_fields: string[]
}

export interface MessageBusConfig {
  ticker: string
  sources: MessageSourceConfig[]
  missing_source_ids: string[]
}

export interface RuntimeOverview {
  ticker: string
  queue_message_count: number
  w1_today_count: number
  w1_avg_latency_ms: number | null
  w2_today_count: number
  w2_avg_latency_ms: number | null
  o3_today_count: number
  o3_avg_latency_ms: number | null
  dtc_today_count: number
  eba_today_count: number
  failed_task_count: number
  avg_processing_latency_ms: number | null
}

export interface RuntimeNode {
  node_id: string
  label: string
  status: HealthStatus
  in_count: number
  out_count: number
  failed_count: number
}

export interface RuntimeEdge {
  edge_id: string
  from: string
  to: string
  label: string
  count: number
}

export interface RuntimeGraph {
  nodes: RuntimeNode[]
  edges: RuntimeEdge[]
}

export interface RuntimeNodeRecord {
  execution_id: string
  source_message_id: string
  status: string
  input_summary: string | null
  output_summary: string | null
  duration_ms: number | null
  created_at: string | null
}

export interface RuntimeNodeDetail {
  node: {
    node_id: string
    label: string
    status: HealthStatus
    last_processed_at: string | null
    today_count: number
    today_failed_count: number
    avg_latency_ms: number | null
    last_error: string | null
  }
  recent_records: RuntimeNodeRecord[]
  page?: PageInfo
}

export interface RuntimeExecution {
  execution_id: string
  source_message_id: string
  message_title?: string | null
  ticker: string
  source_type: string
  final_route: string
  status: string
  message_statuses: string[]
  node_durations_ms: Record<string, number>
  exception_types: string[]
  created_at: string | null
}

export interface RevenueAudit {
  ticker: string
  audit_date: string
  period: Period
  status: AuditStatus
  exit_rule: string
  kpis: {
    today_trade_intent_count: number
    audited_trade_count: number
    today_pnl_usd: number | null
    today_return_pct: number | null
    win_rate: number | null
  }
  trend: Array<{
    date: string
    pnl_usd: number | null
    trade_intent_count: number
  }>
  trade_intents: TradeIntent[]
}

export interface TradeIntent {
  record_id: string
  time: string | null
  ticker: string
  trigger_message_id: string | null
  trigger_policy_id: string | null
  action: string
  theoretical_entry_price: number | null
  estimated_entry_price: number | null
  exit_price: number | null
  slippage_pct: number | null
  pnl_usd: number | null
  status: string
}

export interface CostAudit {
  ticker: string
  period: Period
  status: AuditStatus
  group_by?: "node" | "model" | "ticker"
  kpis: {
    today_input_tokens: number | null
    today_output_tokens: number | null
    today_total_tokens: number | null
    today_total_cost_usd: number | null
    highest_cost_node: string | null
    retry_cost_usd: number | null
  }
  trend: Array<{
    date: string
    total_cost_usd: number | null
    total_tokens: number | null
  }>
  breakdown: {
    by_node: CostBreakdown[]
    by_model: CostBreakdown[]
  }
}

export interface CostBreakdown {
  key: string
  label: string
  cost_usd: number | null
  total_tokens?: number | null
}

export interface CostRecord {
  cost_record_id: string
  time: string | null
  ticker: string
  node: string
  model: string
  input_tokens: number
  output_tokens: number
  total_tokens: number
  cost_usd: number | null
  is_retry: boolean
  status: string
  source_ref: JsonObject
}

export interface DashboardEvent {
  event_id: string
  event_type: string
  ticker: string | null
  occurred_at: string
  payload: JsonObject
}
