import type {
  ApiErrorPayload,
  ApiResponse,
  CostAudit,
  CostRecord,
  DashboardEvent,
  DocumentType,
  DocumentVersion,
  DocumentVersionDetail,
  DocumentsCurrent,
  JsonObject,
  KnownEvent,
  MessageBusConfig,
  MessageBusOverview,
  MessageItem,
  MonitorMode,
  OperationResult,
  OverviewState,
  PageResult,
  Period,
  Policy,
  RevenueAudit,
  RuntimeExecution,
  RuntimeGraph,
  RuntimeNodeDetail,
  RuntimeOverview,
  TickerCard,
  TickerDetail,
} from "@/lib/dashboard-types"

const DEFAULT_API_BASE_URL = "http://127.0.0.1:8780/api/dashboard/v1"

export const dashboardApiBaseUrl = (
  import.meta.env.VITE_DASHBOARD_API_BASE_URL || DEFAULT_API_BASE_URL
).replace(/\/$/, "")

const dashboardAuthToken = import.meta.env.VITE_DASHBOARD_AUTH_TOKEN as string | undefined
const localTokenKey = "doxagent_dashboard_auth_token"

export function getDashboardAuthToken() {
  if (dashboardAuthToken) {
    return dashboardAuthToken
  }
  if (typeof window === "undefined") {
    return undefined
  }
  return window.localStorage.getItem(localTokenKey) || undefined
}

export function setDashboardAuthToken(token: string) {
  window.localStorage.setItem(localTokenKey, token)
}

export function clearDashboardAuthToken() {
  window.localStorage.removeItem(localTokenKey)
}

export class DashboardApiError extends Error {
  code: string
  retryable: boolean
  details: JsonObject
  requestId: string | null
  status: number

  constructor(payload: ApiErrorPayload, status: number) {
    super(payload.error.message)
    this.name = "DashboardApiError"
    this.code = payload.error.code
    this.retryable = payload.error.retryable
    this.details = payload.error.details ?? {}
    this.requestId = payload.request_id
    this.status = status
  }
}

export type QueryParams = Record<
  string,
  string | number | boolean | null | undefined
>

export function queryString(params: QueryParams = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") {
      search.set(key, String(value))
    }
  }
  const value = search.toString()
  return value ? `?${value}` : ""
}

async function parseError(response: Response) {
  const fallback: ApiErrorPayload = {
    error: {
      code: "INTERNAL_ERROR",
      message: `Dashboard API 请求失败：${response.status}`,
      retryable: response.status >= 500,
      details: {},
    },
    request_id: "",
  }

  try {
    const payload = (await response.json()) as ApiErrorPayload
    if (payload.error?.code && payload.error.message) {
      return payload
    }
  } catch {
    return fallback
  }
  return fallback
}

export async function dashboardRequest<T>(
  path: string,
  init: Omit<RequestInit, "body"> & { body?: BodyInit | JsonObject | null } = {}
) {
  const headers = new Headers(init.headers)
  headers.set("accept", "application/json")
  const token = getDashboardAuthToken()
  if (token) {
    headers.set("authorization", `Bearer ${token}`)
  }

  let body = init.body
  if (body && !(body instanceof FormData) && typeof body !== "string") {
    headers.set("content-type", "application/json")
    body = JSON.stringify(body)
  }

  const response = await fetch(`${dashboardApiBaseUrl}${path}`, {
    ...init,
    headers,
    body,
  })

  if (!response.ok) {
    throw new DashboardApiError(await parseError(response), response.status)
  }

  const payload = (await response.json()) as ApiResponse<T>
  return payload.data
}

export const dashboardApi = {
  overview: (params?: QueryParams) =>
    dashboardRequest<OverviewState>(`/overview${queryString(params)}`),
  tickers: (params?: QueryParams) =>
    dashboardRequest<PageResult<TickerCard>>(`/tickers${queryString(params)}`),
  ticker: (ticker: string) =>
    dashboardRequest<TickerDetail>(`/tickers/${encodeURIComponent(ticker)}`),
  startTicker: (
    ticker: string,
    options: { forceInitialize?: boolean; monitorMode?: MonitorMode } = {}
  ) =>
    dashboardRequest<OperationResult>("/tickers", {
      method: "POST",
      body: {
        ticker,
        force_initialize: options.forceInitialize ?? false,
        monitor_mode: options.monitorMode ?? "message_monitoring",
        reason: "Dashboard 前端手动启动",
      },
    }),
  pauseTicker: (ticker: string) =>
    dashboardRequest<OperationResult>(
      `/tickers/${encodeURIComponent(ticker)}/pause`,
      {
        method: "POST",
        body: { reason: "Dashboard 前端手动暂停" },
      }
    ),
  restartTicker: (ticker: string) =>
    dashboardRequest<OperationResult>(
      `/tickers/${encodeURIComponent(ticker)}/restart`,
      {
        method: "POST",
        body: { keep_bindings: true, reason: "Dashboard 前端手动重启" },
      }
    ),
  deleteTicker: (ticker: string) =>
    dashboardRequest<OperationResult>(
      `/tickers/${encodeURIComponent(ticker)}?delete_history=false`,
      {
        method: "DELETE",
        body: { reason: "Dashboard 前端手动删除" },
      }
    ),
  documentsCurrent: (ticker: string, types?: DocumentType[]) =>
    dashboardRequest<DocumentsCurrent>(
      `/tickers/${encodeURIComponent(ticker)}/documents/current${queryString({
        types: types?.join(","),
      })}`
    ),
  documentVersions: (
    ticker: string,
    documentType: DocumentType,
    params?: QueryParams
  ) =>
    dashboardRequest<PageResult<DocumentVersion>>(
      `/tickers/${encodeURIComponent(ticker)}/documents/${documentType}/versions${queryString(
        params
      )}`
    ),
  documentVersionDetail: (
    ticker: string,
    documentType: DocumentType,
    versionId: string
  ) =>
    dashboardRequest<DocumentVersionDetail>(
      `/tickers/${encodeURIComponent(
        ticker
      )}/documents/${documentType}/versions/${encodeURIComponent(versionId)}`
    ),
  knownEvents: (ticker: string, params?: QueryParams) =>
    dashboardRequest<PageResult<KnownEvent>>(
      `/tickers/${encodeURIComponent(ticker)}/known-events${queryString(params)}`
    ),
  policies: (ticker: string, params?: QueryParams) =>
    dashboardRequest<PageResult<Policy>>(
      `/tickers/${encodeURIComponent(ticker)}/policies${queryString(params)}`
    ),
  messageBusOverview: (ticker: string) =>
    dashboardRequest<MessageBusOverview>(
      `/tickers/${encodeURIComponent(ticker)}/message-bus/overview`
    ),
  messages: (ticker: string, params?: QueryParams) =>
    dashboardRequest<PageResult<MessageItem>>(
      `/tickers/${encodeURIComponent(ticker)}/message-bus/messages${queryString(
        params
      )}`
    ),
  messageBusConfig: (ticker: string) =>
    dashboardRequest<MessageBusConfig>(
      `/tickers/${encodeURIComponent(ticker)}/message-bus/config`
    ),
  patchMessageSource: (
    ticker: string,
    sourceId: string,
    payload: JsonObject
  ) =>
    dashboardRequest<MessageBusConfig & { source_id: string }>(
      `/tickers/${encodeURIComponent(
        ticker
      )}/message-bus/config/${encodeURIComponent(sourceId)}`,
      { method: "PATCH", body: payload }
    ),
  deleteMessageSource: (ticker: string, sourceId: string) =>
    dashboardRequest<{ ticker: string; source_id: string; removed: boolean }>(
      `/tickers/${encodeURIComponent(
        ticker
      )}/message-bus/config/${encodeURIComponent(sourceId)}`,
      { method: "DELETE" }
    ),
  runtimeOverview: (ticker: string) =>
    dashboardRequest<RuntimeOverview>(
      `/tickers/${encodeURIComponent(ticker)}/runtime/overview`
    ),
  runtimeGraph: (ticker: string) =>
    dashboardRequest<RuntimeGraph>(
      `/tickers/${encodeURIComponent(ticker)}/runtime/graph`
    ),
  runtimeNode: (ticker: string, nodeId: string, params?: QueryParams) =>
    dashboardRequest<RuntimeNodeDetail>(
      `/tickers/${encodeURIComponent(
        ticker
      )}/runtime/nodes/${encodeURIComponent(nodeId)}${queryString(params)}`
    ),
  runtimeExecutions: (ticker: string, params?: QueryParams) =>
    dashboardRequest<PageResult<RuntimeExecution>>(
      `/tickers/${encodeURIComponent(ticker)}/runtime/executions${queryString(
        params
      )}`
    ),
  revenueAudit: (ticker: string, period: Period) =>
    dashboardRequest<RevenueAudit>(
      `/tickers/${encodeURIComponent(ticker)}/audit/revenue${queryString({
        period,
      })}`
    ),
  runRevenueAudit: (ticker: string, date?: string) =>
    dashboardRequest<{ audit_run_id: string; ticker: string; date: string; status: string }>(
      `/tickers/${encodeURIComponent(ticker)}/audit/revenue/run`,
      {
        method: "POST",
        body: {
          date,
          force: false,
          reason: "Dashboard 前端手动补跑",
        },
      }
    ),
  costAudit: (ticker: string, period: Period, groupBy: string) =>
    dashboardRequest<CostAudit>(
      `/tickers/${encodeURIComponent(ticker)}/audit/cost${queryString({
        period,
        group_by: groupBy,
      })}`
    ),
  costDetails: (ticker: string, params?: QueryParams) =>
    dashboardRequest<PageResult<CostRecord>>(
      `/tickers/${encodeURIComponent(ticker)}/audit/cost/details${queryString(
        params
      )}`
    ),
}

export interface EventStreamOptions {
  ticker?: string
  eventTypes?: string[]
  lastEventId?: string
  signal?: AbortSignal
  onEvent: (event: DashboardEvent) => void
  onError?: (error: Error) => void
}

export async function connectDashboardEvents(options: EventStreamOptions) {
  const headers = new Headers({ accept: "text/event-stream" })
  const token = getDashboardAuthToken()
  if (token) {
    headers.set("authorization", `Bearer ${token}`)
  }

  const path = `/events${queryString({
    ticker: options.ticker,
    event_types: options.eventTypes?.join(","),
    last_event_id: options.lastEventId,
  })}`

  try {
    const response = await fetch(`${dashboardApiBaseUrl}${path}`, {
      headers,
      signal: options.signal,
    })
    if (!response.ok) {
      throw new DashboardApiError(await parseError(response), response.status)
    }
    if (!response.body) {
      throw new Error("当前浏览器不支持 SSE streaming response。")
    }
    await readSseStream(response.body, options.onEvent, options.signal)
  } catch (error) {
    if (options.signal?.aborted) {
      return
    }
    options.onError?.(error instanceof Error ? error : new Error(String(error)))
  }
}

export async function readSseStream(
  body: ReadableStream<Uint8Array>,
  onEvent: (event: DashboardEvent) => void,
  signal?: AbortSignal
) {
  const reader = body.getReader()
  const decoder = new TextDecoder()
  let buffer = ""

  while (!signal?.aborted) {
    const { value, done } = await reader.read()
    if (done) {
      break
    }
    buffer += decoder.decode(value, { stream: true })
    const blocks = buffer.split(/\n\n|\r\n\r\n/)
    buffer = blocks.pop() ?? ""
    for (const block of blocks) {
      const parsed = parseSseBlock(block)
      if (parsed) {
        onEvent(parsed)
      }
    }
  }
}

export function parseSseBlock(block: string): DashboardEvent | null {
  const dataLines: string[] = []
  for (const line of block.split(/\r?\n/)) {
    if (line.startsWith("data:")) {
      dataLines.push(line.slice(5).trimStart())
    }
  }
  if (!dataLines.length) {
    return null
  }
  try {
    return JSON.parse(dataLines.join("\n")) as DashboardEvent
  } catch {
    return null
  }
}
