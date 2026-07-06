import type {
  ActionType,
  AuditStatus,
  HealthStatus,
  JsonValue,
  RunStatus,
  StatusColor,
} from "@/lib/dashboard-types"

const emptyText = "暂无数据"

export function formatDateTime(value: string | null | undefined) {
  if (!value) {
    return emptyText
  }
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return value
  }
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date)
}

export function formatDate(value: string | null | undefined) {
  if (!value) {
    return emptyText
  }
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return value
  }
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
  }).format(date)
}

export function formatTime(value: string | null | undefined) {
  if (!value) {
    return emptyText
  }
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return value
  }
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
  }).format(date)
}

export function formatCurrency(value: number | null | undefined) {
  if (value === null || value === undefined) {
    return emptyText
  }
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 4,
  }).format(value)
}

export function formatNumber(value: number | null | undefined) {
  if (value === null || value === undefined) {
    return emptyText
  }
  return new Intl.NumberFormat("zh-CN").format(value)
}

export function formatPercent(value: number | null | undefined) {
  if (value === null || value === undefined) {
    return emptyText
  }
  return `${(value * 100).toFixed(1)}%`
}

export function formatSignedPercent(value: number | null | undefined) {
  if (value === null || value === undefined) {
    return emptyText
  }
  return `${value > 0 ? "+" : ""}${value.toFixed(2)}%`
}

export function formatDuration(seconds: number | null | undefined) {
  if (seconds === null || seconds === undefined) {
    return emptyText
  }
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  if (hours > 0) {
    return `${hours} 小时 ${minutes} 分钟`
  }
  return `${minutes} 分钟`
}

export function formatLatency(ms: number | null | undefined) {
  if (ms === null || ms === undefined) {
    return emptyText
  }
  if (ms >= 1000) {
    return `${(ms / 1000).toFixed(1)} 秒`
  }
  return `${ms} ms`
}

export function renderJsonValue(value: JsonValue | undefined): string {
  if (value === null || value === undefined || value === "") {
    return emptyText
  }
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return String(value)
  }
  return JSON.stringify(value, null, 2)
}

export function runStatusLabel(status: RunStatus | string) {
  const labels: Record<string, string> = {
    initializing: "初始化中",
    running: "运行中",
    paused: "已暂停",
    stopped: "已停止",
    degraded: "异常降级",
    blocked: "已阻塞",
  }
  return labels[status] ?? status
}

export function healthStatusLabel(status: HealthStatus | string) {
  const labels: Record<string, string> = {
    normal: "正常",
    degraded: "降级",
    blocked: "阻塞",
    unknown: "无数据",
  }
  return labels[status] ?? status
}

export function sessionPhaseLabel(phase: string | null | undefined) {
  if (!phase) {
    return emptyText
  }
  const labels: Record<string, string> = {
    pre_market_digest: "盘前摘要",
    formal_monitoring: "正式监控",
    off_hours_low_frequency: "盘后低频",
  }
  return labels[phase] ?? phase
}

export function monitorModeLabel(mode: string | null | undefined) {
  if (!mode) {
    return emptyText
  }
  const labels: Record<string, string> = {
    message_monitoring: "消息监测",
    paper_trading: "模拟交易",
    broker_trading: "真实 Broker",
  }
  return labels[mode] ?? mode
}

export function actionTypeLabel(type: ActionType | string) {
  const labels: Record<string, string> = {
    DTC: "DTC",
    EBA: "EBA",
    NULL: "NULL",
    Irrelevant: "Irrelevant",
  }
  return labels[type] ?? type
}

export function auditStatusLabel(status: AuditStatus | string) {
  const labels: Record<string, string> = {
    not_started: "未开始",
    calculating: "计算中",
    completed: "完成",
    failed: "失败",
    missing: "暂无数据",
    partial: "部分可用",
  }
  return labels[status] ?? status
}

export function processingStatusLabel(status: string | null | undefined) {
  if (!status) {
    return emptyText
  }
  const labels: Record<string, string> = {
    succeeded: "成功",
    failed: "失败",
    pending: "待处理",
    processing: "处理中",
    skipped: "已跳过",
    completed: "完成",
  }
  return labels[status] ?? status
}

export function pollStatusLabel(status: string | null | undefined) {
  if (!status) {
    return emptyText
  }
  const labels: Record<string, string> = {
    normal: "正常",
    error: "错误",
    unknown: "未轮询",
    succeeded: "正常",
    failed: "错误",
    disabled: "停用",
    never_polled: "未轮询",
  }
  return labels[status] ?? status
}

export function sourceTypeLabel(type: string | null | undefined) {
  if (!type) {
    return emptyText
  }
  const labels: Record<string, string> = {
    media: "媒体",
    social: "社交",
  }
  return labels[type] ?? type
}

export function interfaceTypeLabel(type: string | null | undefined) {
  if (!type) {
    return emptyText
  }
  const labels: Record<string, string> = {
    by_ticker: "按 ticker",
    by_parameter: "按参数",
  }
  return labels[type] ?? type
}

export function routeLabel(route: string | null | undefined) {
  if (!route) {
    return emptyText
  }
  const labels: Record<string, string> = {
    trading_record: "交易记录",
    failed_with_exception: "异常",
    objection: "发起 Objection",
    objection_note: "Objection 备注",
    archive: "归档池",
    ingest_queue: "待入库队列",
    o3: "O3 研判",
    a2: "A2 复核",
  }
  return labels[route] ?? route
}

export function statusTone(status: string | null | undefined): StatusColor {
  if (!status) {
    return "gray"
  }
  if (["normal", "running", "completed", "succeeded", "audited", "available"].includes(status)) {
    return "green"
  }
  if (
    [
      "initializing",
      "calculating",
      "running_task",
      "processing",
      "connecting",
      "open",
      "initializing_documents",
      "collecting_dataset",
      "replaying",
      "draining_runtime",
    ].includes(status)
  ) {
    return "blue"
  }
  if (
    [
      "degraded",
      "paused",
      "partial",
      "retried",
      "pending",
      "pending_audit",
      "historical",
      "queued",
      "cancelled",
    ].includes(status)
  ) {
    return "yellow"
  }
  if (["blocked", "failed", "failed_with_exception", "error", "closed"].includes(status)) {
    return "red"
  }
  return "gray"
}
