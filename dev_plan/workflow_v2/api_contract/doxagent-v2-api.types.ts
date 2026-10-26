/** DoxAgent V2 public wire contract 2.0.0-draft.2. Python validation is generated from this file.
 * JSON objects are closed unless explicitly JsonObject. All fields are required unless marked ?.
 * See ../DOXAGENT_V2_API_CONTRACT.md for HTTP, constraints, metric definitions and state rules.
 */
export type Id = string;
export type Instant = string; // RFC3339 UTC, Z; not a display time
export type Day = string; // YYYY-MM-DD, semantic day where specified
export type DecimalString = string; // finite base-10 decimal, no exponent
export type Count = number; // integer, 0..Number.MAX_SAFE_INTEGER
export type Json = null | boolean | number | string | Json[] | { [key: string]: Json };
export type JsonObject = { [key: string]: Json };
export type Reason =
  | "NOT_PRODUCED" | "NOT_RECORDED" | "NOT_CONFIGURED" | "NOT_SUPPORTED"
  | "NOT_APPLICABLE" | "NO_ACTIVE_REVISION" | "PINNED_ARTIFACT_MISSING"
  | "PERMISSION_DENIED" | "READ_FAILED" | "HISTORY_INSUFFICIENT"
  | "WINDOW_INCOMPLETE" | "NO_PREVIOUS_WINDOW" | "PREVIOUS_ZERO"
  | "NO_SAMPLES" | "UNKNOWN_MODEL_PRICE" | "CACHED_INPUT_MISSING"
  | "INVALID_USAGE" | "CODEX_SUBSCRIPTION_NOT_PRICED" | "NON_TRADING_DAY"
  | "CALENDAR_UNAVAILABLE" | "PROVENANCE_UNVERIFIED" | "BROKER_HISTORY_GAP"
  | "COMMISSION_PENDING" | "PROJECTION_LAG" | "SOURCE_GAP" | "UNRESOLVED_REFERENCE"
  | "HEALTH_UNKNOWN" | "POLICY_EFFECTIVENESS_UNKNOWN";
export type Value<T> =
  | { state: "AVAILABLE"; value: T; reason: null }
  | { state: "NOT_PRODUCED" | "NOT_RECORDED" | "UNAVAILABLE" | "NOT_APPLICABLE" | "FORBIDDEN" | "ERROR";
      value: null; reason: Reason };
export interface Coverage {
  state: "COMPLETE" | "PARTIAL" | "UNKNOWN";
  reasons: Reason[];
  known_count: Count | null;
  excluded_count: Count | null;
  observed_through: Instant | null;
}
export interface Resource<T> {
  state: "AVAILABLE" | "EMPTY" | "PARTIAL" | "NOT_PRODUCED" | "UNAVAILABLE" | "FORBIDDEN" | "ERROR";
  data: T | null;
  reason: Reason | null;
  coverage: Coverage;
}
export interface Meta {
  contract_version: "2.0.0-draft.2";
  workflow_generation: "V2";
  request_id: Id;
  view_id: Id | null;
  scope_key: string;
  as_of: Instant;
  representation_revision: string;
  freshness: "FRESH" | "STALE";
  refresh_error: ApiError | null;
}
export interface Response<T> { data: T; meta: Meta }
export interface ApiError {
  code: string;
  message: string;
  retryable: boolean;
  request_id: Id;
  fields: { path: string; code: string; message: string }[];
  content_id: Id | null; // only for an authorized oversized single object
}
export interface ErrorResponse { error: ApiError }
export interface Page<T> {
  items: T[];
  next_cursor: string | null;
  has_more: boolean;
  snapshot_id: Id;
  limit: Count;
}
export interface Metric {
  metric_id: string;
  unit: "COUNT" | "SECONDS" | "RATIO" | "USD" | "TOKENS";
  current: Value<DecimalString>;
  previous: Value<DecimalString>;
  change_pct: Value<DecimalString>;
  current_coverage: Coverage;
  previous_coverage: Coverage | null;
  provisional: boolean;
}
export type Period = "PREVIOUS_TRADING_DAY" | "CURRENT_TRADING_DAY" | "TRADING_DAYS_7" | "TRADING_DAYS_30" | "ALL";
export type PageKind = "OVERVIEW" | "RESEARCH" | "EXPECTATIONS" | "POLICIES" | "EVENTS" | "MESSAGE_BUS" | "RUNTIME" | "COST";
export interface DayWindow {
  start_at: Instant;
  end_at: Instant; // exclusive semantic boundary, not truncated at as_of
  observed_until: Instant;
  trading_days: Day[]; // [] only for ALL; obtain full calendar with paged calendar route
  trading_day_count: Count;
  membership: "LISTED_TRADING_DAYS" | "LISTED_SEMANTIC_DAYS" | "ALL_SEMANTIC_DAYS";
  coverage: Coverage;
}
export interface PeriodContext {
  selected: Period;
  current: DayWindow;
  previous: DayWindow | null;
  comparison_applicable: boolean;
  comparison_reasons: Reason[];
}
export interface PeriodOption { period: Period; selectable: boolean; reason: Reason | null }
export interface TradingDay {
  day: Day;
  start_at: Instant;
  end_at: Instant;
  market_open_at: Instant;
  market_close_at: Instant;
}
export interface ClockContext {
  semantic_day: Day;
  timezone: "America/New_York";
  boundary_local_time: "02:00:00";
  is_trading_day: Value<boolean>;
  previous_trading_day: Value<Day>;
  calendar_version: string;
  session: Value<"PRE_MARKET" | "REGULAR" | "POST_MARKET" | "OVERNIGHT" | "CLOSED_MAINTENANCE" | "CLOSED_SLEEP">;
  session_start_at: Value<Instant>;
  session_end_at: Value<Instant>;
  next_minute_at: Instant;
}
export interface DocumentRef {
  run_id: Id;
  artifact_id: Id;
  content_sha256: string;
  schema_version: string;
  published_at: Instant;
}
export interface LibraryRef { library_snapshot_id: Id; library_version: Count; published_at: Instant; content_sha256: string }
export interface PolicySetRef extends DocumentRef { policy_set_version: Count }
export interface MaintenanceDisposition {
  status: "MAINTAIN_COMPLETED" | "INITIALIZED";
  policy_disposition: "CONTENT_CHANGED" | "NO_CHANGE" | "INHERITED" | "UNKNOWN";
  event_disposition: "CONTENT_CHANGED" | "NO_CHANGE" | "UNKNOWN";
  from_policy_version: Count | null; to_policy_version: Count;
  from_library_version: Count | null; to_library_version: Count;
}
export interface Activation {
  maintenance?: MaintenanceDisposition | null;
  runtime_activation_id: Id;
  activated_at: Value<Instant>;
  document1: DocumentRef;
  document2: DocumentRef;
  policy_set: PolicySetRef;
  event_library: LibraryRef;
  monitoring_configuration_id: Id;
}
export interface ReadContext {
  view_id: Id;
  expires_at: Instant;
  page: PageKind;
  ticker: string | null;
  clock: ClockContext;
  period_options: PeriodOption[];
  period: PeriodContext | null;
  activation: Resource<Activation>;
  coverage: Coverage;
}
export interface AuthConfig {
  provider: "supabase";
  supabase_url: string;
  supabase_publishable_key: string;
}
export interface Principal { user_id: Id; tier: string; can_read: boolean; can_operate: boolean }
export interface Capability { available: boolean; reason: string | null; message: string | null }
export interface Capabilities {
  monitoring: Capability;
  paper_trading: Capability;
  live_trading: Capability;
  revenue_audit: Capability;
  message_stream: Capability;
  runtime_graph_stream: Capability;
}
export type MonitorMode = "MESSAGE_MONITORING" | "PAPER_TRADING" | "LIVE_TRADING";
export type RunState = "INITIALIZING" | "RUNNING" | "PAUSED" | "STOPPED";
export type Health = "NORMAL" | "DEGRADED" | "BLOCKED" | "UNKNOWN";
export interface ActionPermission { allowed: boolean; reason: string | null }
export interface TickerState {
  ticker: string;
  requested_mode: MonitorMode;
  effective_mode: Value<MonitorMode>;
  run_state: RunState;
  health: Health;
  health_reasons: string[];
  initialization_id: Id | null;
  initialization_incomplete: boolean;
  removed: boolean;
  removed_at: Instant | null;
  control_etag: string;
  control_revision: Count;
  work_epoch: Count;
  cutoff_at: Instant | null;
  mode_effective_at: Instant | null;
  existing_trades_continue: true;
  actions: { pause: ActionPermission; restart: ActionPermission; remove: ActionPermission; retry_initialization: ActionPermission };
}
export interface TickerNavigation {
  ticker: string;
  run_state: RunState;
  health: Health;
  initialization_incomplete: false;
  removed: false;
}
export interface SourceCounts { normal: Value<Count>; abnormal: Value<Count> }
export interface OverviewStatus { ib_gateway_status?: "CONNECTED" | "DISCONNECTED"; clock: ClockContext; normal_tickers: Value<Count>; blocked_tickers: Value<Count> }
export interface OverviewMetrics {
  policy_hits: Metric;
  trade_executed: Metric;
  trade_triggered: Metric;
  messages: Metric;
  api_token_cost: Metric;
  nonroutine_repairs: Metric;
  paper_realized_net_pnl: Metric;
  live_realized_net_pnl: Metric;
}
export interface TickerOverview {
  state: TickerState;
  last_standard_message_at: Value<Instant>;
  source_counts: SourceCounts;
  metrics: { policy_hits: Metric; trade_executed: Metric; trade_triggered: Metric; messages: Metric; api_token_cost: Metric };
  initialization: Resource<InitializationProgress> | null;
}
export type ParentStatus = "QUEUED" | "RUNNING" | "SUCCEEDED" | "FAILED";
export type StepKey = "RESEARCH" | "EVENT_LIBRARY" | "EXPECTATIONS" | "POLICIES" | "SOURCES_ACTIVATION" | "START_RUNTIME";
export interface InitializationStep {
  step_key: StepKey;
  status: "PENDING" | "RUNNING" | "SUCCEEDED" | "FAILED";
  first_started_at: Value<Instant>;
  settled_at: Value<Instant>;
  duration_seconds: Value<number>;
  quality_annotations: string[];
}
export interface InitializationProgress {
  initialization_id: Id;
  ticker: string;
  status: ParentStatus;
  state_seq: Count;
  control_etag: string;
  manual_resume_allowed: boolean;
  failed_node_keys: string[];
  steps: InitializationStep[]; // exactly six, StepKey order
  quality_annotations: string[];
  failure: ApiError | null;
}
export interface StartTickerRequest {
  ticker: string;
  monitor_mode: MonitorMode;
  initialization: "REUSE_ACTIVE" | "FORCE_INITIALIZE";
}
export interface Operation {
  operation_id: Id;
  kind: "START" | "PAUSE" | "RESTART" | "REMOVE" | "RESUME_INITIALIZATION";
  ticker: string;
  status: "ACCEPTED" | "RUNNING" | "SUCCEEDED" | "FAILED";
  created_at: Instant;
  completed_at: Instant | null;
  outcome: "INITIALIZATION_QUEUED" | "RUNNING" | "PAUSED" | "REMOVED" | "INITIALIZATION_RESUMED" | null;
  initialization_id: Id | null;
  ticker_state: TickerState;
  error: ApiError | null;
  retry_after_seconds: Count | null;
}
export interface RunSummary {
  run_id: Id;
  ticker: string;
  run_status: "QUEUED" | "RUNNING" | "FAILED" | "CANCELLED" | "PUBLISHED";
  publication_status: "DRAFT" | "PUBLISHED" | "FAILED";
  publication_state: "COMPLETE" | "PARTIAL" | null;
  created_at: Instant;
  published_at: Value<Instant>;
  is_active: boolean;
  quality_annotations: string[];
}
export type ResearchSection = "C1" | "C3" | "C5" | "FUTURE_NODES";
export interface ContentRef {
  content_id: Id;
  content_type: "text/markdown" | "application/json" | "text/plain";
  size_bytes: Count;
  sha256: string;
}
export interface Citation {
  citation_key: Id;
  alias: string;
  status: "RESOLVED" | "UNRESOLVED" | "INVALID";
  origin_run_id: Id | null;
  origin_attempt_id: Id | null;
  source_id: Id | null;
  url: string | null;
  title: string | null;
  warning: string | null;
}
export interface ResearchSummary {
  run: RunSummary;
  document: Resource<DocumentRef>;
  global_research_status: RunSummary["publication_status"];
  updated_at: Value<Instant>;
  sections: { section: ResearchSection; content: Resource<ContentRef> }[];
}
export interface FutureNode {
  time: string;
  future_event: string;
  relationship_to_target: string;
  source: string;
  source_published_at: string;
}
export interface ContentChunk {
  content: ContentRef;
  chunk_index: Count;
  text: string;
  next_cursor: string | null;
  complete: boolean;
}
export interface FutureNodeRow extends FutureNode { item_key: Id; ordinal: Count }
export interface ResearchDownloadManifest {
  contract_version: "2.0.0-draft.2";
  ticker: string;
  run_id: Id;
  files: {
    section: ResearchSection;
    entry: "C1.md" | "C3.md" | "C5.md" | "future_nodes.json";
    state: "INCLUDED" | "MISSING";
    artifact_id: Id | null;
    sha256: string | null;
    size_bytes: Count | null;
    reason: Reason | null;
  }[];
}
export interface ShellSummaryLegacy {
  shell_id: Id;
  ordinal: Count;
  core_question: string;
  boundary_rule: string;
  status: "COMPLETED" | "FAILED";
  unit_count: Value<Count>;
  failed_stage: "PENDING" | "STATE" | "REALIZATION" | "GAPS" | "FINALIZATION" | "COMPLETED" | "FAILED" | null;
  failure_kind: "SYSTEM" | "TRANSIENT" | "FORMAT" | "SHELL" | null;
  failure: ApiError | null;
  content: Resource<ContentRef>;
}
export interface ShellSummaryV21 {
  schema_version: "document2.shell-view.v2.1"; view_id: Id; shell_id: Id; ordinal: Count;
  name: string; scope: string; boundary: string; ref: string[];
  status: "COMPLETED" | "FAILED"; unit_count: Value<Count>;
  failed_stage: string | null; failure_kind: string | null; failure: ApiError | null; content: Resource<ContentRef>;
}
export type ShellSummary = ShellSummaryLegacy | ShellSummaryV21;
export interface ExpectationsSummary {
  run: RunSummary;
  runtime_activation_id: Id | null;
  document: DocumentRef;
  citation_status: "COMPLETE" | "PARTIAL" | "UNAVAILABLE";
  shells: Page<ShellSummary>;
  default_shell_id: Id | null;
}
export type ParameterType = "NUMBER" | "RANGE" | "TIME" | "STAGE" | "DIRECTION" | "EVIDENCE";
export type StateValueData =
  | { number: number; unit: string }
  | { lower: number; upper: number; unit: string }
  | { point: string | null; start: string | null; end: string | null; precision: string }
  | { stage: string }
  | { direction: string }
  | { stance: string; strength: string };
export interface ExpectationState {
  parameters: { parameter_id: Id; definition: string; value_type: ParameterType }[];
  values: {
    state_value_id: Id; parameter_id: Id;
    source_role: "ACTUAL" | "MANAGEMENT" | "SELL_SIDE" | "INDUSTRY_CHAIN" | "MARKET_IMPLIED";
    value: StateValueData; previous_value: StateValueData | null;
    time_scope: string; as_of: string; citation: string[];
    validity_state: "CURRENT" | "SUPERSEDED" | "DISPUTED" | "RETRACTED";
  }[];
}
export interface RealizationFactor {
  factor_id: Id; condition: string; structural_role: "REQUIRED" | "BLOCKER" | "MODIFIER";
  current_status: string; impact: string; citation: string[];
  observability: { match_condition: string };
}
export interface PotentialGap {
  gap_id: Id; possible_occurrence: string; derivation: string; citation: string[];
  expected_revision: string; recognition_criteria: string | null;
}
export interface ExpectationUnitLegacy {
  expectation_id: Id; proposition: string; horizon: string;
  state: ExpectationState; realization_factors: RealizationFactor[]; potential_gaps: PotentialGap[];
}
export interface ExpectationUnitV21 {
  schema_version: "document2.unit-view.v2.1"; view_id: Id; ordinal: Count;
  name: string; scope: string; horizon: string; ref: string[];
  state: {
    parameters: { name: string; definition: string; value_type: ParameterType; ref: string[] }[];
    values: {name: string; parameter: string; source_role: string; value: StateValueData; previous_value: StateValueData | null; time_scope: string; as_of: string; validity_state: string; ref: string[]}[];
  };
  expectation_baseline: {name: string; baseline: string; ordinary_progress: string; open_frontier: string; time_scope: string; ref: string[]}[];
  realization_factors: {name: string; mechanism: string; current_status: string; materiality_context: string; scope_boundary: string; ref: string[]}[];
  potential_gaps: {name: string; why_live: string; revision_logic: string; possibility_space: {name: string; implication: string}[]; ref: string[]}[];
}
export type ExpectationUnit = ExpectationUnitLegacy | ExpectationUnitV21;
export interface ShellContent {
  run_id: Id; shell_id: Id;
  content_status: "PUBLISHED_COMPLETE" | "PUBLISHED_PARTIAL";
  units: Page<ExpectationUnit>;
}
export type PolicyFilter = "ACTIVE" | "ADDED" | "HIT" | "MODIFIED" | "RETIRED" | "EXECUTED";
export interface PolicyLegacy {
  policy_id: Id; title: string;
  source_refs: { shell_id: Id; expectation_id: Id; gap_id: Id }[];
  decision: "LONG" | "SHORT"; match_scope: string;
  activation_conditions: {
    condition_id: Id; criterion: string;
    calibration: { reference_state: string; trigger_boundary: string };
  }[];
}
export interface PolicyV3 {
  policy_id: Id; title: string; ref: string[]; transmission: string; match_scope: string;
  activation_conditions: {condition_id: Id; decision: "LONG" | "SHORT"; trigger_layer: "OUTER" | "MIDDLE" | "INNER"; criterion: string; calibration: {reference_state: string; trigger_boundary: string}}[];
}
export type Policy = PolicyLegacy | PolicyV3;
// Download is the original canonical artifact, so native ref field names are retained.
export interface PolicySetDownloadLegacy {
  schema_version: "document3.v2.2";
  ticker: string;
  policy_set_version: Count;
  publication_state: "COMPLETE" | "PARTIAL";
  document2_ref: {
    run_id: Id; artifact_id: Id; sha256: string; published_at: Instant;
    publication_state: "COMPLETE" | "PARTIAL";
  };
  event_library_ref: {
    contract_version: string; ticker: string; version: Count; sha256: string; published_at: Instant;
  } | null;
  policies: PolicyLegacy[];
  published_at: Instant;
}
export interface PolicySetDownloadV3 {
  schema_version: "document3.v3"; workflow_version: "document3.v2.1"; ticker: string;
  policy_set_version: Count; as_of: Instant; publication_state: "COMPLETE" | "PARTIAL";
  document2_ref: PolicySetDownloadLegacy["document2_ref"] | null;
  event_library_ref: PolicySetDownloadLegacy["event_library_ref"];
  policies: PolicyV3[]; published_at: Instant;
}
export type PolicySetDownload = PolicySetDownloadLegacy | PolicySetDownloadV3;
export interface PolicySummaryLegacy {
  policy_id: Id;
  policy_revision_id: Id;
  policy_activation_revision: string;
  policy_set_version: Count;
  title: string;
  decision: "LONG" | "SHORT";
  shell_ids: Id[];
  unresolved_shell_ids: Id[];
  lifecycle: "ACTIVE" | "RETIRED";
  consumed: Value<boolean>;
  consumed_at: Value<Instant>;
  effective: Value<boolean>;
  matched_filters: PolicyFilter[];
}
export interface PolicySummaryV3 {
  schema_version: "document3.policy-summary.v3";
  policy_id: Id; policy_revision_id: Id; policy_set_version: Count; title: string;
  directions: ("LONG" | "SHORT")[]; executable_directions: ("LONG" | "SHORT")[]; consumed_directions: ("LONG" | "SHORT")[];
  shell_ids: Id[]; unresolved_shell_ids: Id[]; lifecycle: "ACTIVE" | "RETIRED";
  consumed: Value<boolean>; consumed_at: Value<Instant>; effective: Value<boolean>; matched_filters: PolicyFilter[];
}
export type PolicySummary = PolicySummaryLegacy | PolicySummaryV3;
export interface RelatedExpectation {
  run_id: Id; shell_id: Id; shell_name: string;
  unit_id: Id | null; unit_name: string | null;
  content: "ALL" | "STATE" | "BASELINE" | "FACTORS" | "GAPS";
  item: string | null; item_name: string | null; refs: string[];
}
export interface PolicyDetailLegacy { related_expectations?: RelatedExpectation[]; summary: PolicySummaryLegacy; source_document2: DocumentRef; activation_semantics: "OR"; policy: PolicyLegacy }
export interface PolicyDetailV3 { related_expectations?: RelatedExpectation[]; summary: PolicySummaryV3; source_document2: DocumentRef | null; activation_semantics: "OR"; policy: PolicyV3; policy_usage: JsonObject[] }
export type PolicyDetail = PolicyDetailLegacy | PolicyDetailV3;
export type PolicyShellSummary = Pick<ShellSummaryLegacy, "shell_id" | "ordinal" | "core_question"> | Pick<ShellSummaryV21, "shell_id" | "ordinal" | "name">;
export interface PolicyContext {
  maintenance?: MaintenanceDisposition | null;
  runtime_activation_id: Id;
  document2: DocumentRef;
  policy_set: PolicySetRef;
  source_document2: DocumentRef | null;
  source_context_consistent: Value<boolean>;
  shells: Page<PolicyShellSummary>;
  default_shell_id: Id | null;
}
export interface PolicyMetrics {
  active: Metric; long_active: Metric; short_active: Metric;
  long_ratio: Metric; short_ratio: Metric;
  added: Metric; hit: Metric; modified: Metric; retired: Metric; executed: Metric;
}
export interface ChangeEvent {
  change_id: Id; object_id: Id;
  type: "ADD" | "MODIFY" | "RETIRE" | "RESTORE";
  occurred_at: Instant;
  title: string;
  from_version: Count | null; to_version: Count;
  before_revision_id: Id | null; after_revision_id: Id | null;
  detail_revision_id: Id;
  summary: string;
  changed_paths: string[];
}
export type Precision = "TIMESTAMP" | "DAY" | "MONTH" | "QUARTER" | "YEAR" | "INTERVAL" | "UNKNOWN";
export type Assertion = "ACTUAL" | "GUIDANCE" | "FORECAST" | "PLAN" | "RUMOR" | "DENIAL" | "SCHEDULED" | "ONGOING" | "PLANNED" | "EXPECTED" | "RUMORED" | "DENIED" | "HYPOTHETICAL" | "UNKNOWN";
export interface CanonicalFact {
  fact_id: Id; proposition: string; assertion_state: Assertion;
  subject_time: string | null; fact_occurred_at: string | null;
  fact_occurrence_time_precision: Precision | null;
}
export interface EventFields {
  event_id: Id; ticker: string; title: string; event_type: string;
  occurred_at: string; occurrence_time_precision: Precision;
  status: "ACTIVE" | "SUPPRESSED" | "MERGED";
  canonical_summary: string; known_event_summary: string;
  is_important: boolean; include_in_reference_view: boolean;
  related_event_ids: Id[]; supersedes_event_id: Id | null; derived_from_event_ids: Id[];
  price_analysis: JsonObject | null;
}
export interface CanonicalEvent extends EventFields { facts: CanonicalFact[] }
export interface FactRow {
  fact_key: Id; fact: CanonicalFact; ordinal: Count; fact_revision_id: Id;
  lifecycle: "ACTIVE" | "RETIRED"; member_of_event: boolean;
}
export type EventFilter = "ACTIVE" | "ADDED" | "MODIFIED" | "RETIRED";
export interface EventSummary {
  event_key: Id; event_id: Id; event_revision_id: Id; library_snapshot_id: Id; library_version: Count;
  title: string; occurred_at: string | null; occurrence_time_precision: Precision;
  status: "ACTIVE" | "SUPPRESSED" | "MERGED";
  active_fact_count: Count; matched_filters: EventFilter[];
}
export interface EventDetail { event_key: Id; library: LibraryRef; reference_snapshot_id: Id | null; event_revision_id: Id; event: EventFields; facts: Page<FactRow> }
export interface EventMetrics {
  active_events: Metric; active_facts: Metric; added_events: Metric; added_facts: Metric;
  modified_events: Metric; modified_facts: Metric; retired_events: Metric; retired_facts: Metric;
}
export interface DeltaDay {
  semantic_day: Day; state: "AVAILABLE" | "NO_CHANGE" | "PENDING" | "FAILED" | "UNAVAILABLE";
  delta_id: Id | null; from_library_version: Count | null; to_library_version: Count | null;
  from_library_snapshot_id: Id | null; to_library_snapshot_id: Id | null;
  reason: Reason | null;
}
export interface ReferenceDelta {
  delta_id: Id; semantic_day: Day; from_library_version: Count; to_library_version: Count;
  from_library_snapshot_id: Id | null; to_library_snapshot_id: Id;
  before_reference_snapshot_id: Id; after_reference_snapshot_id: Id;
  changes: Page<ReferenceDeltaItem>;
}
export interface ReferenceDeltaItem {
  change_id: Id; type: "add" | "modify" | "remove";
  event_key: Id; event_id: Id; title: string;
  before_revision_id: Id | null; after_revision_id: Id | null;
  detail_revision_id: Id; detail_library_snapshot_id: Id; detail_library_version: Count;
  changed_paths: string[]; summary: string;
}
export type Route = "ARCHIVE" | "TRADE" | "ADD_TO_DELTA" | "W3";
export type Confidence = "normal" | "low";
export type CaseStatus = "CREATED" | "RUNNING" | "ADJUDICATED" | "COMPLETED" | "PENDING_W3" | "PENDING_RETRY" | "UNAVAILABLE" | "FAILED";
export type TechnicalStatus = "OK" | "PENDING_RETRY" | "UNAVAILABLE" | "FAILED";
export type ResultKind = FirstRoundTag | "ARCHIVE" | "EVENT_DISCOVERY" | "BADCASE" | "TRADE_INTENT" | "TRADE_EXECUTION" | "TRADE_NOT_EXECUTED" | "FAILURE";
export type ExecutionState = "PENDING" | "UNKNOWN" | "EXECUTED" | "NOT_EXECUTED";
export type CaseTradeState = "NOT_APPLICABLE" | ExecutionState;
export interface MessageKey { standard_message_id: Id; revision: Count }
export interface SourceLabel { source_id: Id; binding_id: Id; name: string; kind: "api" | "crawler" }
export interface CaseLink {
  case_id: Id | null; status: CaseStatus | null; initial_route: Route | null;
  w3_status: "PENDING" | "RUNNING" | "RESOLVED" | "FAILED_RETRYABLE" | "FAILED" | null;
  resolved_route: Route | null;
  route_group: "NOT_PROCESSED" | "ARCHIVE" | "TRADE" | "ADD_TO_DELTA" | "W3_PENDING" | "FAILED";
}
export type MessageRouteFilter = "ALL" | "ARCHIVE" | "TRADE" | "ADD_TO_DELTA" | "W3_PENDING" | "FAILED" | "NOT_PROCESSED";
export interface MessageSummary extends MessageKey {
  content_update?: { revision: Count; pending: boolean; last_error: string | null; last_checked_at: Instant | null; body_acquired_at: Instant | null };
  source_name?: string | null;
  row_revision: Count;
  title: Value<string>; source: SourceLabel; url: string;
  source_published_at: Instant; collected_at: Instant; normalized_at: Instant;
  stream_published_at: Instant; stream_item_id: Id; stream_offset: Count; member_index: Count;
  exact_duplicate_count: Value<Count>;
  case: CaseLink;
  body: ContentRef;
}
export interface MessageBaseline { messages: Page<MessageSummary>; stream_cursor: string }
export interface BusMetrics { published_revisions: Metric; body_completion_success_ratio: Metric; new_article_body?: ArticleBodyCohort }
export interface ArticleBodyCohort {
  total: Count; full: Count; pending: Count; failed: Count; ratio: number | null;
  network_attempts: Count; network_succeeded: Count; cache_reuses: Count;
  basis: "FIRST_OBSERVED_ARTICLE"; legacy_excluded: boolean;
}
export interface PollTimeliness {
  window_seconds: Count; samples: Count; mean_seconds: number | null; p95_seconds: number | null;
  deferred: Count; failed: Count; sample_limit_reached: boolean;
  cadence?: PollCadence;
}
export interface PollCadence {
  pending_cycles?: Count; settled_cycles?: Count;
  pending_intake_candidates?: Count; failed_intake_candidates?: Count;
  expected_cycles: Count; fresh_cycles: Count; missing_cycles: Count; coverage: number | null;
  max_gap_seconds: number | null; worst_request_p95_seconds: number | null;
  gap_mean_seconds: number | null; gap_p95_seconds: number | null; deferred: Count; failed: Count;
  units: Count; eligibility_unknown: Count; sample_limit_reached: boolean;
}
export interface BusStatus {
  run_state: RunState;
  continuous_run_started_at: Value<Instant>;
  continuous_run_seconds: Value<number>;
  source_counts: SourceCounts;
  average_poll_latency_seconds: Value<number>;
  latency_sample_count: Count;
  timeliness?: PollTimeliness;
}
export interface SourceStatus {
  published_today?: Value<Count>;
  cadence?: PollCadence;
  coverage_state?: "COMPLETE" | "PARTIAL" | "UNKNOWN";
  pending_query_count?: Count; pending_timestamp_count?: Count;
  stage_timings_ms?: { [stage: string]: number };
  capture_age_seconds?: number | null;
  capture_to_first_intake_seconds?: number | null;
  internal_endpoints?: IrEndpointStatus[];
  source: SourceLabel; binding_version: Count; source_version: Count;
  publication_mode: "immediate" | "buffered";
  enabled: boolean; global_enabled: boolean;
  poll_status: "never_polled" | "succeeded" | "partial" | "failed" | "disabled";
  health_group: "NORMAL" | "ABNORMAL" | "EXCLUDED";
  last_success_at: Value<Instant>; last_published_count: Value<Count>;
  last_poll_latency_seconds: Value<number>; current_error: ApiError | null;
  target_interval_seconds: Count; active_windows: ActiveWindow[];
  next_target_at: Value<Instant>;
  countdown_state: "SCHEDULED" | "PAUSED" | "DISABLED" | "WINDOW_CLOSED" | "CLOSED_SLEEP" | "UNKNOWN";
}
export interface IrEndpointStatus {
  endpoint_id: string; name: string; url: string; method: string;
  status: "never_polled" | "succeeded" | "failed" | "deferred";
  last_success_at: Instant | null; last_attempt_at: Instant | null;
  next_target_at: Instant | null; latency_ms: Count; http_status: number | null;
  error: string | null;
}
export interface ActiveWindow { timezone: string; weekdays: number[]; start_time: string; end_time: string }
export interface Polling {
  enabled: boolean; target_interval_seconds: Count; tolerance_ratio: number;
  alert_after_seconds: Count; active_windows: ActiveWindow[];
}
export interface Streaming {
  publication_mode: "immediate" | "buffered";
  buffer: { max_items: Count; max_wait_seconds: Count; max_compiled_body_chars: Count };
}
export interface BindingEditable { enabled: boolean; source_parameters: JsonObject; polling: Polling; streaming: Streaming }
export interface BindingConfig {
  binding_id: Id; ticker: string; source: SourceLabel;
  binding_version: Count; source_version: Count; control_etag: string;
  effective: BindingEditable;
  parameter_schema: JsonObject;
  editor: { parameter_form: boolean; parameter_form_id: string | null; json: true; polling_interval_form: true };
  writable_parameter_paths: string[];
  redacted_parameter_paths: string[];
  monitoring_terms_usage: { acquisition_mode: "by_ticker" | "by_search" | "by_distribution"; terms_mode: "UNIFIED" | "LEGACY" | "UNCONFIGURED"; managed_parameter_paths: string[] };
}
export interface MonitoringL1Concept { concept_id: string; expressions: Record<string, string> }
export interface MonitoringL2Term {
  literal: string | null; regex: string | null;
  field: "all" | "title" | "summary" | "body";
  case_sensitive: boolean; whole_word: boolean;
}
export interface MonitoringL2Group { id: string; any: MonitoringL2Term[]; all: MonitoringL2Term[]; none: MonitoringL2Term[] }
export interface MonitoringL2Language { groups: MonitoringL2Group[] }
export interface MonitoringTermsValue {
  l1_concepts: MonitoringL1Concept[];
  l2: Record<string, MonitoringL2Language>;
  definition: { relevant: string; irrelevant: string };
}
export interface MonitoringTermsRequest { configuration: MonitoringTermsValue }
export interface MonitoringTermsConsumer {
  source_id: string; name: string; binding_id: string;
  acquisition_mode: "by_search" | "by_distribution";
  content_language: string | null; source_enabled: boolean; binding_enabled: boolean;
  search_policy_mode: "or" | "separate" | null; jev_source_enabled: boolean | null;
  terms_mode: "UNIFIED" | "LEGACY" | "UNCONFIGURED";
  queries: { concept_ids: string[]; query: string; query_key: string }[];
  preview_issue: string | null;
}
export interface MonitoringTermsConfig {
  ticker: string; revision: number; control_etag: string; updated_at: string | null;
  configuration: MonitoringTermsValue | null;
  required_languages: string[];
  language_requirements: { language: string; source_ids: string[] }[];
  consumers: MonitoringTermsConsumer[];
}
export interface MonitoringTermsValidation {
  valid: boolean; issues: { path: string; code: string; message: string }[];
  search_previews: MonitoringTermsConsumer[];
}
export interface BindingPatch {
  enabled?: boolean;
  source_parameters?: JsonObject;
  polling?: Partial<Polling>;
  streaming?: { publication_mode?: "immediate" | "buffered"; buffer?: Partial<Streaming["buffer"]> };
}
export interface AvailableSource { source_id: Id; name: string; kind: "api"; source_version: Count }
export interface AvailableSourceDetail extends AvailableSource { defaults: BindingEditable; parameter_schema: JsonObject; parameter_form_id: string | null }
export interface BindSourceRequest { source_id: Id; source_version: Count; configuration: BindingEditable }
export interface MutationReceipt { binding_id: Id; binding_version: Count; removed: boolean; updated_at: Instant }
export interface RuntimeMetrics {
  processed_cases: Metric; hot_path_mean_seconds: Metric; w3_mean_seconds: Metric;
  new_cases: Metric; old_cases: Metric; hit_cases: Metric; hit_case_ratio: Metric;
  w3_cases: Metric; new_fact_candidates: Metric; executed_cases: Metric;
}
export type FirstRoundTag = "FIRST_ROUND_TRADE" | "POLICY_RELATED_EVENT" | "SUSPECTED_DUPLICATE_HIT";
export interface CaseSummary {
  w3_decision?: Value<"LONG" | "SHORT" | "NO_TRADE">;
  final_novelty_source?: "W1" | "W3_OPERATIONS" | null;
  final_policy_source?: "W2" | "LEGACY_W3" | null;
  source_name?: string | null;
  w3_operations_status?: string | null;
  first_round_tags?: FirstRoundTag[];
  case_id: Id; revision: Count; semantic_day: Day; runtime_mode: "REALTIME" | "CLOSED";
  first_round_shape: "PARALLEL" | "SEQUENTIAL" | "W1_ONLY" | "UNDETERMINED";
  status: CaseStatus; technical_status: TechnicalStatus;
  title: Value<string>; source: SourceLabel;
  received_at: Instant; completed_at: Value<Instant>; duration_seconds: Value<number>;
  initial_route: Route | null; resolved_route: Route | null;
  w3_status: CaseLink["w3_status"];
  final_novelty: Value<"NEW" | "OLD">;
  final_policy_hit: Value<boolean>;
  results: ResultKind[];
  trade: { intent_count: Count; state: CaseTradeState; reason_codes: string[] };
  result_settled: boolean;
  trade_disposition: "NOT_EVALUATED" | "ANALYSIS_ONLY" | "SUPPRESSED_BY_CONTROL" | "MODE_UNAVAILABLE" | "CANDIDATE" | "READY" | "DUPLICATE_POLICY" | "DUPLICATE_REALTIME_OUTPUT" | "DUPLICATE_EVENT_TRADE" | "EXPIRED_SEMANTIC_DAY" | "SELECTED_CONDITION_INVALID" | "POLICY_MIGRATION_UNRESOLVED" | "EXECUTION_ACCEPTED" | "OUTPUT_RECORDED";
  stream_item_id: Id; member_count: Count;
}
export type NodeId = "SOURCE" | "W1" | "W2" | "W3" | "ARCHIVE" | "EVENT_DISCOVERY" | "FIRST_ROUND_TRADE" | "POLICY_RELATED_EVENT" | "TRADE_INTENT" | "TRADE_EXECUTION" | "TRADE_NOT_EXECUTED" | "FAILURE";
export interface NodeCounts {
  node_id: NodeId; case_count: Count; failed_case_count: Count;
  low_confidence_case_count: Count | null;
  latest_processed_at: Value<Instant>; average_seconds: Value<number>;
  result_counts: { result: ResultKind; case_count: Count }[];
}
export interface GraphEdge { edge_id: Id; from: NodeId; to: NodeId; case_count: Count }
export interface GraphBaseline {
  graph_revision: Count; stream_cursor: string;
  nodes: NodeCounts[]; edges: GraphEdge[];
  cases: Page<CaseSummary>;
}
export interface NodeDetail { summary: NodeCounts; recent_cases: Page<CaseSummary>; path_edges?: GraphEdge[] }
export interface Timing {
  first_started_at: Value<Instant>; completed_at: Value<Instant>;
  wall_seconds: Value<number>;
  basis: "RECORDED" | "DERIVED_FROM_RECORDED_INTERVALS" | "UNAVAILABLE";
}
export interface ModelAttempt {
  phase?: "w3_template" | "trade_decision" | "post_decision_operations" | null;
  context_mode?: "EXPLICIT" | "PREVIOUS_RESPONSE" | "REPLAY_PARENT";
  attempt_id: Id; turn_id: Id | null; node_id: "W1" | "W2" | "W3";
  round: "R1" | "R2" | "R3" | "AGENT"; ordinal: Count; attempt_number: Count;
  status: "PENDING" | "RUNNING" | "SUCCEEDED" | "FAILED" | "CANCELLED";
  timing: Timing;
  error: ApiError | null;
}
export interface EventLink {
  /** Display evidence from this Case's pinned snapshot; never current-head fallback. */
  title?: Value<string>;
  facts?: { fact_id: Id; proposition: Value<string> }[];
  provisional_proposition?: Value<string>;
  kind: "CANONICAL" | "PROVISIONAL";
  event_key: Id | null; event_id: Id; fact_ids: Id[];
  library_snapshot_id: Id;
  library_version: Count; provisional_snapshot_version: Count | null;
  semantic_day: Day | null;
}
export interface PolicyLink { title?: Value<string>; policy_id: Id; policy_set_version: Count; policy_activation_revision: string | null; policy_revision_id?: Id; condition_ids: Id[] }
export interface RuntimeRound {
  round: "R1" | "R2" | "R3";
  attempt_count: Count;
  /** Only set when non-execution is proven, not inferred from a truncated attempts page. */
  not_executed_reason: "W2_SKIPPED" | "NO_POLICY_CANDIDATE" | "NOVELTY_NOT_NEW" | "NO_POLICY_HIT_OR_RELATED" | "LEGACY_CONTRACT" | "DELTA_OWNED_BY_W3" | "NO_DELTA_CAPTURE_REQUIRED" | null;
}
export interface FactAttribution { event_id: Id; fact_ids: Id[] }
export interface W1Detail {
  extraction?: Resource<{ capture_mode: "NEW_CAPTURE" | "AMBIGUOUS_CAPTURE"; candidate_count: Count }>;
  rounds?: RuntimeRound[];
  /** null or absent: historical evidence not recorded; []: recorded with no canonical Fact attribution. */
  fact_attributions?: FactAttribution[] | null;
  unresolved_reference_ids?: Id[];
  novelty: Value<"NEW" | "OLD">; confidence: Value<Confidence>; references: EventLink[];
  timing: Timing; attempts: Page<ModelAttempt>; reasoning: Resource<ContentRef>;
}
export interface ConditionNovelty { condition_ids: Id[]; is_new: boolean }
export interface W2NovelFactAlignmentDetail {
  status: "PENDING" | "RUNNING" | "SUCCEEDED" | "FAILED" | "NOT_EXECUTED" | "NOT_RECORDED" | "UNAVAILABLE";
  error?: string | null;
  related_policy_results?: { policy_id: Id; is_new: boolean }[];
  not_executed_reason: RuntimeRound["not_executed_reason"];
  policy_fact_novelty: Value<"NEW" | "OLD">;
  condition_results: ConditionNovelty[];
}
export interface W2Detail {
  policy_related: Value<boolean>;
  related_policies: PolicyLink[];
  unresolved_related_policy_ids?: Id[];
  novel_fact_alignment: W2NovelFactAlignmentDetail;
  rounds?: RuntimeRound[];
  candidate_policies?: PolicyLink[];
  unresolved_candidate_policy_ids?: Id[];
  reasoning_stage?: "R1" | "R2" | null;
  unresolved_policy_ids?: Id[];
  skipped: boolean; policy_hit: Value<boolean>; confidence: Value<Confidence>;
  policies: PolicyLink[]; timing: Timing; attempts: Page<ModelAttempt>; reasoning: Resource<ContentRef>;
}
export interface W3TradeDecision {
  decision: "NO_TRADE" | "POLICY_TRADE" | "EXPERT_TRADE";
  reason: string;
  policy_trade: {policy_id: Id; condition_id: Id} | null;
  expert_trade: {direction: "LONG" | "SHORT"; prior_expectation: string; expectation_delta: string} | null;
}
export interface W3DirectionDecision {
  decision: "LONG" | "SHORT" | "NO_TRADE";
  reason: string;
  prior_expectation: string | null;
  expectation_delta: string | null;
}
export interface W3Detail {
  trade_timing?: Timing;
  operations_timing?: Timing;
  decision?: W3TradeDecision | W3DirectionDecision | null;
  operations_result?: JsonObject | null;
  operations_status?: string | null;
  operations_error?: string | null;
  unresolved_reference_ids?: Id[];
  unresolved_policy_ids?: Id[];
  status: CaseLink["w3_status"]; mode: "UNCOVERED_NEW" | "REVALIDATE_THEN_EVALUATE";
  novelty: Value<"NEW" | "OLD">; references: EventLink[]; policy_hit: Value<boolean>;
  policies: PolicyLink[]; expert_trade_evaluated: Value<boolean>; expert_trade: Value<boolean>;
  direction: Value<"LONG" | "SHORT">;
  prior_expectation: Value<string>; expectation_delta: Value<string>;
  timing: Timing; attempts: Page<ModelAttempt>;
  reasoning: { novelty: Resource<ContentRef>; policy: Resource<ContentRef>; expert_trade: Resource<ContentRef>; trade?: Resource<ContentRef> };
}
export interface Candidate {
  candidate_id: Id; dedupe_key: Id; originating_node: "W1_R3" | "W3";
  proposition: string; assertion_state: Assertion; subject_time: string | null;
  occurrence_date: Day | null; entities: string[];
  provisional_event_id: Id | null; created_at: Instant;
}
export interface CaseDetail {
  w3_state?: {watch: JsonObject | null; pins: JsonObject | null; receipt: JsonObject | null};
  policy_watch?: {followup: JsonObject[]; actions: JsonObject[]; receipt: JsonObject | null};
  policy_execution?: JsonObject | null;
  summary: CaseSummary;
  runtime_activation_id: Value<Id>;
  document1: Value<DocumentRef>; document2: Value<DocumentRef>;
  library: LibraryRef; policy_set: PolicySetRef; provisional_snapshot_version: Count;
  hot_path: Timing; w1: Resource<W1Detail>; w2: Resource<W2Detail>; w3: Resource<W3Detail>;
  messages: Page<MessageSummary>;
  failures: { failure_id: Id; stage: string; status: string; error: ApiError; occurred_at: Instant }[];
  candidate_count: Value<Count>; execution_count: Value<Count>;
  results: ResultKind[];
}
export interface ExecutionSummary {
  execution_id: Id; intent_id: Id; case_id: Id;
  environment: Value<"PAPER" | "LIVE">; profile_revision: Value<Id>;
  direction: "LONG" | "SHORT";
  intent_status: string; intake_status: "NOT_RECEIVED" | "EXECUTION_ACCEPTED" | "REJECTED" | "UNKNOWN";
  execution_state: ExecutionState; execution_reason_codes: string[];
  entry_result: "FILLED" | "PARTIAL_FILLED" | "FAILED" | "DIRECTION_DISABLED" | null;
  entry_reason: string | null;
  has_actual_fill: boolean;
  triggered_at: Value<Instant>; accepted_at: Value<Instant>; first_fill_at: Value<Instant>;
  filled_quantity: Value<DecimalString>; filled_notional_usd: Value<DecimalString>;
}
export interface OrderSummary {
  order_attempt_id: Id; execution_id: Id; leg: "ENTRY" | "EXIT";
  side: "BUY" | "SELL"; order_type: "LMT" | "MKT";
  quantity: DecimalString; limit_price: DecimalString | null;
  state: string; broker_status: string | null; sent_at: Value<Instant>; settled_at: Value<Instant>;
}
export interface Fill {
  fill_id: Id; execution_id: Id; order_attempt_id: Id; correction_revision: Count;
  leg: "ENTRY" | "EXIT"; side: "BUY" | "SELL";
  executed_at: Instant; quantity: DecimalString; price: DecimalString;
  commission_usd: Value<DecimalString>;
}
export interface ExecutionDetail { execution: ExecutionSummary; orders: Page<OrderSummary>; fills: Page<Fill> }
export interface StreamEnvelope<T> {
  event_id: Id;
  stream_cursor: string;
  view_id: Id;
  scope_key: string;
  sequence: string; // unsigned decimal integer, opaque ordering within a stream
  emitted_at: Instant;
  payload: T;
}
export interface MessageDelta {
  standard_message_id: Id; revision: Count;
  row_revision: Count;
  matches_scope: boolean;
  action: "UPSERT" | "REMOVE";
  row: MessageSummary | null;
}
export interface GraphDelta {
  previous_graph_revision: Count;
  graph_revision: Count;
  upsert_cases: CaseSummary[];
  remove_case_ids: Id[];
  replace_nodes: NodeCounts[];
  replace_edges: GraphEdge[];
}
export interface StreamReset {
  reason: "CURSOR_EXPIRED" | "SCOPE_MISMATCH" | "BASELINE_UNAVAILABLE" | "VIEW_EXPIRED";
  replacement_required: true;
}
export type AuditScope = "API" | "CODEX";
export interface UsageTotals {
  requests: Value<Count>; input_tokens: Metric; cached_input_tokens: Metric;
  output_tokens: Metric; total_tokens: Metric; cache_hit_ratio: Metric;
  noncached_input_cost_usd: Metric; cached_input_cost_usd: Metric;
  input_cost_usd: Metric; output_cost_usd: Metric; total_cost_usd: Metric;
  average_total_tokens_per_request: Value<DecimalString>;
  coverage: Coverage;
  unpriced_request_count: Value<Count>;
}
export interface AuditFilters {
  scope: AuditScope;
  nodes: Page<{ node_id: string; label: string }>;
  models: Page<{ provider: string; model_id: string }>;
}
export interface CostSummary { scope: AuditScope; pricing_version: string; usd_cny: "6.8"; totals: UsageTotals }
export interface TrendPoint { start_at: Instant; end_at: Instant; total_tokens: Value<DecimalString>; cost_usd: Value<DecimalString>; coverage: Coverage }
export interface CostTrend { scope: AuditScope; bucket: "SEMANTIC_DAY" | "SEMANTIC_WEEK" | "SEMANTIC_MONTH"; points: TrendPoint[] }
export interface ShareRow { key: string; label: string; value: DecimalString; ratio: DecimalString }
export interface CostBreakdown {
  scope: AuditScope; unit: "USD" | "TOKENS"; dimension: "NODE" | "MODEL";
  items: ShareRow[]; other: ShareRow | null; coverage: Coverage;
}
export interface CostNodeRow { node_id: string; label: string; models: { provider: string; model_id: string }[]; totals: UsageTotals }

/** Temporary response only for deferred exact keyword aggregates. */
export interface DeferredQueryTicket {
  query_id: string;
  state: 'QUEUED' | 'RUNNING';
  retry_after_seconds: number;
  expires_at: Instant;
  result_path: string;
}

/** One frozen report across the retained production invocation ledger. */
export interface CostReportTotals extends UsageTotals {
  observed_attempts: Count; usage_known_attempts: Count; usage_unknown_attempts: Count;
  priced_attempts: Count; unpriced_attempts: Count;
  reported_cost_attempts: Count; estimated_cost_attempts: Count;
  normalized_attempts: Count; cache_ratio_known_attempts: Count; unknown_model_price_attempts: Count;
  cache_write_input_tokens: Metric; cache_write_5m_input_tokens: Metric;
  cache_write_1h_input_tokens: Metric; cache_write_cost_usd: Metric;
  cost_native_cny: Value<DecimalString>; cost_native_usd: Value<DecimalString>;
}
/** Rows have no previous-period comparison; retain only their displayed evidence. */
export interface CostRowMetric {
  current: Value<DecimalString>; current_coverage: Coverage; provisional: boolean;
}
export interface CostReportRowTotals {
  observed_attempts: Count; usage_known_attempts: Count;
  reported_cost_attempts: Count; estimated_cost_attempts: Count; unpriced_attempts: Count;
  cache_ratio_known_attempts: Count;
  input_tokens: CostRowMetric; cached_input_tokens: CostRowMetric;
  output_tokens: CostRowMetric; cache_hit_ratio: CostRowMetric;
  input_cost_usd: CostRowMetric; output_cost_usd: CostRowMetric; total_cost_usd: CostRowMetric;
  cache_write_input_tokens: CostRowMetric; cache_write_5m_input_tokens: CostRowMetric;
  cache_write_1h_input_tokens: CostRowMetric; cache_write_cost_usd: CostRowMetric;
  cost_native_cny: Value<DecimalString>; cost_native_usd: Value<DecimalString>;
}
export interface CostReportRow {
  key: string; node: string; round: string; provider: string; model: string;
  totals: CostReportRowTotals;
}
export interface CostReport {
  scope: AuditScope; pricing_version: string; usd_cny: DecimalString; observed_through: Instant;
  totals: CostReportTotals; trend: CostTrend; breakdown: CostBreakdown;
  rows: CostReportRow[]; row_count: Count; offset: Count; limit: Count;
  filter_choices: { tickers: string[]; nodes: string[]; models: {provider: string; model_id: string}[] };
  source_coverage: Coverage; coverage: Coverage;
}
