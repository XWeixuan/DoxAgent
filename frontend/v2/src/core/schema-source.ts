import type * as W from "@contract";
export type WireMessageStream = W.StreamEnvelope<W.MessageDelta>;
export type WireGraphStream = W.StreamEnvelope<W.GraphDelta>;
export type WireAuthConfig = W.Response<W.AuthConfig>;
export type WirePrincipal = W.Response<W.Principal>;
export type WireCapabilities = W.Response<W.Capabilities>;
export type WireReadContext = W.Response<W.ReadContext>;
export type WireStatus = W.Response<W.Resource<W.OverviewStatus>>;
export type WireMetrics = W.Response<W.Resource<W.OverviewMetrics>>;
export type WireTickers = W.Response<W.Resource<W.Page<W.TickerOverview>>>;
export type WireNavigation = W.Response<W.Resource<W.Page<W.TickerNavigation>>>;
export type WireTicker = W.Response<W.Resource<W.TickerState>>;
export type WireInitialization = W.Response<
  W.Resource<W.InitializationProgress>
>;
export type WireOperation = W.Response<W.Operation>;
export type WireContent = W.Response<W.Resource<W.ContentChunk>>;
export type WireCitations = W.Response<W.Resource<W.Page<W.Citation>>>;
export type WireResearch = W.Response<W.Resource<W.ResearchSummary>>;
export type WireRuns = W.Response<W.Resource<W.Page<W.RunSummary>>>;
export type WireFutureNodes = W.Response<W.Resource<W.Page<W.FutureNodeRow>>>;
export type WireExpectations = W.Response<W.Resource<W.ExpectationsSummary>>;
export type WireShells = W.Response<W.Resource<W.Page<W.ShellSummary>>>;
export type WireShellContent = W.Response<W.Resource<W.ShellContent>>;
export type WireUnit = W.Response<W.Resource<W.ExpectationUnit>>;
export type WirePolicyContext = W.Response<W.Resource<W.PolicyContext>>;
export type WirePolicyShells = W.Response<
  W.Resource<
    W.Page<Pick<W.ShellSummary, "shell_id" | "ordinal" | "core_question">>
  >
>;
export type WirePolicyMetrics = W.Response<W.Resource<W.PolicyMetrics>>;
export type WirePolicies = W.Response<W.Resource<W.Page<W.PolicySummary>>>;
export type WirePolicy = W.Response<W.Resource<W.PolicyDetail>>;
export type WireChanges = W.Response<W.Resource<W.Page<W.ChangeEvent>>>;
export type WireLibrary = W.Response<W.Resource<W.LibraryRef>>;
export type WireEventMetrics = W.Response<W.Resource<W.EventMetrics>>;
export type WireEvents = W.Response<W.Resource<W.Page<W.EventSummary>>>;
export type WireEvent = W.Response<W.Resource<W.EventDetail>>;
export type WireFacts = W.Response<W.Resource<W.Page<W.FactRow>>>;
export type WireDeltaDays = W.Response<W.Resource<W.Page<W.DeltaDay>>>;
export type WireDelta = W.Response<W.Resource<W.ReferenceDelta>>;
export type WireBusStatus = W.Response<W.Resource<W.BusStatus>>;
export type WireBusMetrics = W.Response<W.Resource<W.BusMetrics>>;
export type WireSources = W.Response<W.Resource<W.Page<W.SourceStatus>>>;
export type WireMessages = W.Response<W.Resource<W.MessageBaseline>>;
export type WireMessage = W.Response<W.Resource<W.MessageSummary>>;
export type WireBinding = W.Response<W.Resource<W.BindingConfig>>;
export type WireAvailableSources = W.Response<
  W.Resource<W.Page<W.AvailableSource>>
>;
export type WireAvailableSource = W.Response<
  W.Resource<W.AvailableSourceDetail>
>;
export type WireRuntimeMetrics = W.Response<W.Resource<W.RuntimeMetrics>>;
export type WireGraph = W.Response<W.Resource<W.GraphBaseline>>;
export type WireNode = W.Response<W.Resource<W.NodeDetail>>;
export type WireCases = W.Response<W.Resource<W.Page<W.CaseSummary>>>;
export type WireCase = W.Response<W.Resource<W.CaseDetail>>;
export type WireAttempts = W.Response<W.Resource<W.Page<W.ModelAttempt>>>;
export type WireCaseMessages = W.Response<W.Resource<W.Page<W.MessageSummary>>>;
export type WireCandidates = W.Response<W.Resource<W.Page<W.Candidate>>>;
export type WireExecutions = W.Response<W.Resource<W.Page<W.ExecutionSummary>>>;
export type WireExecution = W.Response<W.Resource<W.ExecutionDetail>>;
export type WireOrders = W.Response<W.Resource<W.Page<W.OrderSummary>>>;
export type WireFills = W.Response<W.Resource<W.Page<W.Fill>>>;
export type WireCostFilters = W.Response<W.Resource<W.AuditFilters>>;
export type WireCostSummary = W.Response<W.Resource<W.CostSummary>>;
export type WireCostTrend = W.Response<W.Resource<W.CostTrend>>;
export type WireCostBreakdown = W.Response<W.Resource<W.CostBreakdown>>;
export type WireCostNodes = W.Response<W.Resource<W.Page<W.CostNodeRow>>>;
export type WireBindingWrite = W.Response<W.BindingConfig>;
export type WireBindingReceipt = W.Response<W.MutationReceipt>;
