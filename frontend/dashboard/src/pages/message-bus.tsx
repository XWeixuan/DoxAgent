import { useCallback, useEffect, useMemo, useState } from "react"
import type { FormEvent } from "react"
import { useParams } from "react-router-dom"
import {
  ChevronDownIcon,
  ExternalLinkIcon,
  ListIcon,
  RadioTowerIcon,
  SaveIcon,
  SearchIcon,
  SettingsIcon,
} from "lucide-react"
import { toast } from "sonner"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import {
  Field,
  FieldGroup,
  FieldLabel,
} from "@/components/ui/field"
import {
  InputGroup,
  InputGroupAddon,
  InputGroupButton,
  InputGroupInput,
  InputGroupTextarea,
} from "@/components/ui/input-group"
import { Switch } from "@/components/ui/switch"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { dashboardApi } from "@/lib/dashboard-api"
import type {
  DashboardEvent,
  MessageItem,
  MessageSourceConfig,
  PageResult,
} from "@/lib/dashboard-types"
import {
  formatDateTime,
  formatDuration,
  formatLatency,
  formatNumber,
  formatPercent,
  formatTime,
  interfaceTypeLabel,
  pollStatusLabel,
  processingStatusLabel,
  sourceTypeLabel,
} from "@/lib/format"
import { cn } from "@/lib/utils"
import { useDashboardEvents } from "@/hooks/use-dashboard-events"
import { useDashboardQuery } from "@/hooks/use-dashboard-query"
import {
  EmptyState,
  ErrorState,
  FilterSelect,
  KeyValueList,
  LoadMoreButton,
  LoadingGrid,
  MetricCell,
  MetricStrip,
  PageHeader,
  RefreshButton,
  Section,
  StatusBadge,
} from "@/components/dashboard/shared"

const messageLimit = 10
const parameterSchemaBySource: Record<
  string,
  Array<{ key: string; label: string; max: number; placeholder: string }>
> = {
  benzinga_news: [
    {
      key: "search_terms",
      label: "搜索词",
      max: 3,
      placeholder: "每行一个搜索词，最多 3 个",
    },
  ],
  finnhub_company_news: [],
  stocktwits_messages: [],
  tikhub_x_search: [
    {
      key: "search_terms",
      label: "搜索词",
      max: 3,
      placeholder: "每行一个 X 搜索词，最多 3 个",
    },
  ],
  tikhub_x_user_posts: [
    {
      key: "usernames",
      label: "用户名",
      max: 2,
      placeholder: "每行一个 X 用户名，最多 2 个",
    },
  ],
  newswire_rss: [
    {
      key: "rss_urls",
      label: "RSS 地址",
      max: 3,
      placeholder: "每行一个 RSS URL，最多 3 个",
    },
  ],
}

export function MessageBusPage() {
  const ticker = useParams().ticker?.toUpperCase() ?? "MU"
  const [view, setView] = useState<"stream" | "config">("stream")
  const [sourceFilter, setSourceFilter] = useState("all")
  const [statusFilter, setStatusFilter] = useState("all")
  const [query, setQuery] = useState("")
  const [appliedQuery, setAppliedQuery] = useState("")
  const [messagePage, setMessagePage] = useState<PageResult<MessageItem> | null>(null)
  const [loadingMore, setLoadingMore] = useState(false)
  const [mutatingSource, setMutatingSource] = useState<string | null>(null)

  const overviewLoader = useCallback(() => dashboardApi.messageBusOverview(ticker), [ticker])
  const configLoader = useCallback(() => dashboardApi.messageBusConfig(ticker), [ticker])
  const messagesLoader = useCallback(
    () =>
      dashboardApi.messages(ticker, {
        limit: messageLimit,
        source_id: sourceFilter === "all" ? undefined : sourceFilter,
        processing_status: statusFilter === "all" ? undefined : statusFilter,
        q: appliedQuery || undefined,
        sort: "-collected_at",
      }),
    [appliedQuery, sourceFilter, statusFilter, ticker]
  )

  const overview = useDashboardQuery(overviewLoader, { intervalMs: 8000 })
  const config = useDashboardQuery(configLoader, { intervalMs: 60000 })
  const messages = useDashboardQuery(messagesLoader, { intervalMs: 15000 })
  const reloadOverview = overview.reload
  const reloadMessages = messages.reload

  useEffect(() => {
    setSourceFilter("all")
    setStatusFilter("all")
    setQuery("")
    setAppliedQuery("")
  }, [ticker])

  useEffect(() => {
    if (messages.data) {
      setMessagePage(messages.data)
    }
  }, [messages.data])

  const handleEvent = useCallback(
    (event: DashboardEvent) => {
      if (
        event.event_type === "message_bus.message.created" ||
        event.event_type === "message_bus.poll.failed"
      ) {
        void reloadMessages()
        void reloadOverview()
      }
    },
    [reloadMessages, reloadOverview]
  )

  const events = useDashboardEvents({
    ticker,
    eventTypes: ["message_bus.message.created", "message_bus.poll.failed"],
    onEvent: handleEvent,
  })

  const sourceOptions = useMemo(() => {
    const options = [{ value: "all", label: "全部来源" }]
    for (const source of config.data?.sources ?? []) {
      options.push({ value: source.source_id, label: source.display_name })
    }
    return options
  }, [config.data])

  const statusOptions = useMemo(() => {
    const values = new Set<string>()
    for (const item of messagePage?.items ?? []) {
      values.add(item.processing_status)
    }
    return [
      { value: "all", label: "全部状态" },
      ...Array.from(values).map((value) => ({ value, label: processingStatusLabel(value) })),
    ]
  }, [messagePage])
  const averageChannelLatency = useMemo(() => {
    const values =
      config.data?.sources
        .map((source) => source.poll_state.last_latency_ms)
        .filter((value): value is number => typeof value === "number") ?? []
    if (!values.length) {
      return null
    }
    return Math.round(values.reduce((total, value) => total + value, 0) / values.length)
  }, [config.data])

  const search = (event: FormEvent) => {
    event.preventDefault()
    setAppliedQuery(query.trim())
  }

  const loadMore = async () => {
    if (!messagePage?.page.next_cursor) {
      return
    }
    setLoadingMore(true)
    try {
      const next = await dashboardApi.messages(ticker, {
        limit: messageLimit,
        cursor: messagePage.page.next_cursor,
        source_id: sourceFilter === "all" ? undefined : sourceFilter,
        processing_status: statusFilter === "all" ? undefined : statusFilter,
        q: appliedQuery || undefined,
        sort: "-collected_at",
      })
      setMessagePage({
        items: [...messagePage.items, ...next.items],
        page: next.page,
      })
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error))
    } finally {
      setLoadingMore(false)
    }
  }

  const updateSourceEnabled = async (sourceId: string, enabled: boolean) => {
    setMutatingSource(sourceId)
    try {
      await dashboardApi.patchMessageSource(ticker, sourceId, {
        enabled,
        reason: "Dashboard 前端切换 source",
      })
      toast.success("消息源配置已更新。")
      await config.reload()
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error))
    } finally {
      setMutatingSource(null)
    }
  }

  const updateSourceParameters = async (sourceId: string, parameters: Record<string, string[]>) => {
    setMutatingSource(sourceId)
    try {
      await dashboardApi.patchMessageSource(ticker, sourceId, {
        ...parameters,
        reason: "Dashboard 前端更新 source 参数",
      })
      toast.success("消息源参数已更新。")
      await config.reload()
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error))
    } finally {
      setMutatingSource(null)
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title={`${ticker} 消息总线`}
        eyebrow="Message Bus Control Plane"
        description="查看采集、标准化、去重、正文补全、相关性过滤与事件流状态。"
        lastUpdatedAt={messages.lastUpdatedAt}
        actions={
          <>
            <StatusBadge status={events.state} label={`SSE：${events.state}`} />
            <ToggleGroup
              type="single"
              value={view}
              onValueChange={(value) => value && setView(value as "stream" | "config")}
            >
              <ToggleGroupItem value="stream">
                <ListIcon data-icon="inline-start" />
                消息流
              </ToggleGroupItem>
              <ToggleGroupItem value="config">
                <SettingsIcon data-icon="inline-start" />
                配置
              </ToggleGroupItem>
            </ToggleGroup>
            <RefreshButton
              refreshing={overview.isRefreshing || messages.isRefreshing || config.isRefreshing}
              onClick={() => {
                void overview.reload()
                void messages.reload()
                void config.reload()
              }}
            />
          </>
        }
      />

      {events.error ? <ErrorState title="SSE 连接异常" message={events.error} /> : null}
      {[overview.error, messages.error, config.error]
        .filter(Boolean)
        .map((message) => (
          <ErrorState key={message} message={message ?? ""} />
        ))}

      {overview.data ? (
        <MetricStrip>
          <MetricCell title="启动时长" value={formatDuration(overview.data.uptime_seconds)} status="normal" />
          <MetricCell title="今日原始消息" value={formatNumber(overview.data.today_raw_message_count)} status="normal" />
          <MetricCell title="事件流数量" value={formatNumber(overview.data.today_event_count)} status="normal" />
          <MetricCell
            title="正文补全成功率"
            value={formatPercent(overview.data.media_enrichment_success_rate)}
            status={
              overview.data.media_enrichment_success_rate &&
              overview.data.media_enrichment_success_rate >= 0.7
                ? "normal"
                : "degraded"
            }
          />
          <MetricCell
            title="健康 channel"
            value={`${overview.data.healthy_channel_count}/${overview.data.total_channel_count}`}
            status={
              overview.data.healthy_channel_count === overview.data.total_channel_count
                ? "normal"
                : "degraded"
            }
          />
          <MetricCell
            title="平均轮询延迟"
            value={formatLatency(averageChannelLatency)}
            status={averageChannelLatency && averageChannelLatency > 30000 ? "degraded" : "normal"}
          />
        </MetricStrip>
      ) : overview.isLoading ? (
        <LoadingGrid rows={5} />
      ) : null}

      {view === "stream" ? (
        <div className="message-console">
          <Section
            title="Live Message Stream"
            description="新消息通过 SSE 触发刷新，列表按抓取时间倒序分页加载。"
            actions={
              <form className="message-stream-toolbar" onSubmit={search}>
                <FilterSelect
                  value={sourceFilter}
                  placeholder="来源"
                  options={sourceOptions}
                  onChange={setSourceFilter}
                  className="sm:w-auto"
                />
                <FilterSelect
                  value={statusFilter}
                  placeholder="处理状态"
                  options={statusOptions}
                  onChange={setStatusFilter}
                  className="sm:w-auto"
                />
                <FieldGroup>
                  <Field>
                    <FieldLabel htmlFor="message-query" className="sr-only">
                      关键词搜索
                    </FieldLabel>
                    <InputGroup>
                      <InputGroupInput
                        id="message-query"
                        value={query}
                        onChange={(event) => setQuery(event.target.value)}
                        placeholder="标题、摘要或正文关键词"
                      />
                      <InputGroupAddon align="inline-end">
                        <InputGroupButton type="submit" variant="outline">
                          <SearchIcon data-icon="inline-start" />
                          搜索
                        </InputGroupButton>
                      </InputGroupAddon>
                    </InputGroup>
                    {appliedQuery ? (
                      <div className="text-xs text-muted-foreground">
                        当前关键词：{appliedQuery}
                      </div>
                    ) : null}
                  </Field>
                </FieldGroup>
              </form>
            }
          >
            <div className="message-stream-board biome-scrollbar">
              {messages.isLoading && !messagePage ? (
                <LoadingGrid rows={4} />
              ) : messagePage && messagePage.items.length > 0 ? (
                <div className="flex flex-col gap-3">
                  {messagePage.items.map((message) => (
                    <MessageCard key={message.message_id} message={message} />
                  ))}
                  <LoadMoreButton
                    hasMore={messagePage.page.has_more}
                    loading={loadingMore}
                    onClick={() => void loadMore()}
                  />
                </div>
              ) : (
                <EmptyState title="暂无消息" description="当前筛选条件下没有消息。" />
              )}
            </div>
          </Section>

          <MessageBusSidePanel
            sources={config.data?.sources ?? []}
            loading={config.isLoading}
          />
        </div>
      ) : (
        <Section title="消息渠道配置" description="当前 ticker 的消息源配置状态。">
          {config.isLoading && !config.data ? (
            <LoadingGrid rows={3} />
          ) : config.data && config.data.sources.length > 0 ? (
            <div className="flex flex-col gap-3">
              {config.data.sources.map((source) => (
                <SourceConfigCard
                  key={source.source_id}
                  source={source}
                  mutatingSource={mutatingSource}
                  onToggle={updateSourceEnabled}
                  onSaveParameters={updateSourceParameters}
                />
              ))}
            </div>
          ) : (
            <EmptyState title="暂无消息源配置" />
          )}
        </Section>
      )}
    </div>
  )
}

function MessageBusSidePanel({
  sources,
  loading,
}: {
  sources: MessageSourceConfig[]
  loading: boolean
}) {
  return (
    <aside className="flex flex-col gap-4">
      <Section title="Channel Health" description="消息源轮询状态。">
        {loading && sources.length === 0 ? (
          <LoadingGrid rows={2} />
        ) : sources.length > 0 ? (
          <div className="source-health-list">
            {sources.map((source) => (
              <SourceHealthRow key={source.source_id} source={source} />
            ))}
          </div>
        ) : (
          <EmptyState title="暂无 channel" />
        )}
      </Section>
    </aside>
  )
}

function SourceHealthRow({ source }: { source: MessageSourceConfig }) {
  const status = sourceConfigStatus(source)
  const tone = sourceStatusTone(status)
  const newCount = source.poll_state.last_poll_new_message_count ?? 0
  return (
    <div className="source-health-row">
      <div className={cn("source-health-ring", `source-health-${tone}`)} aria-hidden="true" />
      <div className="min-w-0">
        <div className="flex items-center gap-1.5">
          <span className={cn("source-health-dot", `source-health-${tone}`)} />
          <div className="truncate text-sm font-semibold">{source.display_name}</div>
        </div>
        <div className="mt-1 truncate text-xs text-muted-foreground">
          {source.binding.ticker} | {interfaceTypeLabel(source.interface_type)} |{" "}
          {sourceTypeLabel(source.source_type)} | 延迟 {formatLatency(source.poll_state.last_latency_ms)}
        </div>
      </div>
      <div className="ml-auto flex shrink-0 items-center gap-2">
        <Badge variant="outline" className="rounded-full px-2 py-0.5 font-medium">
          +{newCount}
        </Badge>
        <Badge
          variant="outline"
          className={cn("rounded-full px-2 py-0.5 font-mono font-semibold", `source-health-${tone}`)}
        >
          {formatTime(source.poll_state.last_success_at)}
        </Badge>
      </div>
      {source.poll_state.last_error_message ? (
        <p className="col-span-3 rounded-[4px] bg-destructive/10 p-2 text-xs text-destructive">
          {source.poll_state.last_error_message}
        </p>
      ) : null}
    </div>
  )
}

function sourceStatusTone(status: string | null | undefined) {
  if (status === "succeeded" || status === "normal" || status === "completed") {
    return "green"
  }
  if (status === "failed" || status === "error") {
    return "yellow"
  }
  if (status === "blocked") {
    return "red"
  }
  return "gray"
}

function sourceConfigStatus(source: MessageSourceConfig) {
  if (!source.enabled || !source.binding.enabled || source.poll_state.status === "disabled") {
    return "disabled"
  }
  if (source.poll_state.status === "succeeded") {
    return "normal"
  }
  if (source.poll_state.status === "failed") {
    return "error"
  }
  return source.poll_state.status || "unknown"
}

function SourceConfigCard({
  source,
  mutatingSource,
  onToggle,
  onSaveParameters,
}: {
  source: MessageSourceConfig
  mutatingSource: string | null
  onToggle: (sourceId: string, enabled: boolean) => void
  onSaveParameters: (sourceId: string, parameters: Record<string, string[]>) => void
}) {
  const [parameterView, setParameterView] = useState<"form" | "json">("form")
  const parameterFields = parameterSchemaBySource[source.source_id] ?? []
  const [draft, setDraft] = useState<Record<string, string>>(() =>
    createParameterDraft(source)
  )

  useEffect(() => {
    setDraft(createParameterDraft(source))
  }, [source])

  const saveParameters = () => {
    const payload: Record<string, string[]> = {}
    for (const field of parameterFields) {
      payload[field.key] = splitParameterInput(draft[field.key] ?? "").slice(0, field.max)
    }
    onSaveParameters(source.source_id, payload)
  }

  const status = sourceConfigStatus(source)

  return (
    <Card>
      <CardHeader>
        <div className="flex items-start justify-between gap-3">
          <div>
            <CardTitle className="text-lg font-light">{source.display_name}</CardTitle>
            <CardDescription>
              {source.source_id} · {sourceTypeLabel(source.source_type)} ·{" "}
              {interfaceTypeLabel(source.interface_type)}
            </CardDescription>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <ToggleGroup
              type="single"
              value={parameterView}
              onValueChange={(value) => value && setParameterView(value as "form" | "json")}
            >
              <ToggleGroupItem value="form">表单</ToggleGroupItem>
              <ToggleGroupItem value="json">JSON</ToggleGroupItem>
            </ToggleGroup>
            <StatusBadge
              status={status}
              label={pollStatusLabel(status)}
              className="px-2.5 py-1 text-sm"
            />
          </div>
        </div>
      </CardHeader>
      <CardContent className="grid gap-4 lg:grid-cols-[240px_minmax(0,1fr)]">
        <div className="flex flex-col gap-3">
          <div className="flex items-center justify-between rounded-[4px] border bg-white/55 p-3">
            <span className="text-sm font-medium">启用</span>
            <Switch
              checked={source.enabled}
              disabled={
                mutatingSource === source.source_id ||
                !source.agent_mutable_fields.includes("enabled")
              }
              onCheckedChange={(checked) => onToggle(source.source_id, checked)}
            />
          </div>
          <KeyValueList
            items={[
              { label: "Binding", value: source.binding.binding_id },
              { label: "轮询间隔", value: `${source.poll_interval_seconds}s` },
              { label: "最近成功", value: formatDateTime(source.poll_state.last_success_at) },
              { label: "上次延迟", value: formatLatency(source.poll_state.last_latency_ms) },
            ]}
          />
        </div>
        <div>
          <div className="mb-2 flex items-center gap-2 text-sm font-medium">
            <RadioTowerIcon data-icon="inline-start" />
            参数配置
          </div>
          {parameterView === "json" ? (
            <pre className="max-h-56 overflow-auto whitespace-pre-wrap break-words rounded-[4px] border bg-white/60 p-3 text-xs biome-scrollbar">
              {JSON.stringify(source.binding.parameters, null, 2)}
            </pre>
          ) : (
            <div className="flex flex-col gap-3">
              {parameterFields.length > 0 ? (
                <FieldGroup>
                  {parameterFields.map((field) => (
                    <Field key={field.key}>
                      <FieldLabel htmlFor={`${source.source_id}-${field.key}`}>
                        {field.label}
                      </FieldLabel>
                      <InputGroup>
                        <InputGroupTextarea
                          id={`${source.source_id}-${field.key}`}
                          value={draft[field.key] ?? ""}
                          onChange={(event) =>
                            setDraft((current) => ({
                              ...current,
                              [field.key]: event.target.value,
                            }))
                          }
                          placeholder={field.placeholder}
                          rows={3}
                        />
                      </InputGroup>
                      <div className="text-xs text-muted-foreground">
                        对应参数：{field.key}，最多 {field.max} 项。
                      </div>
                    </Field>
                  ))}
                </FieldGroup>
              ) : (
                <div className="rounded-[4px] border bg-white/60 p-3 text-sm text-muted-foreground">
                  该 channel 当前只需要 ticker binding，不接收额外注入参数。
                </div>
              )}
              <div className="flex justify-end">
                <Button
                  variant="outline"
                  size="sm"
                  disabled={mutatingSource === source.source_id || parameterFields.length === 0}
                  onClick={saveParameters}
                >
                  <SaveIcon data-icon="inline-start" />
                  保存参数
                </Button>
              </div>
            </div>
          )}
          {source.poll_state.last_error_message ? (
            <p className="mt-3 rounded-[4px] bg-destructive/10 p-2 text-sm text-destructive">
              {source.poll_state.last_error_message}
            </p>
          ) : null}
        </div>
      </CardContent>
    </Card>
  )
}

function createParameterDraft(source: MessageSourceConfig) {
  const draft: Record<string, string> = {}
  for (const field of parameterSchemaBySource[source.source_id] ?? []) {
    const rawValue = source.binding.parameters[field.key]
    draft[field.key] = Array.isArray(rawValue) ? rawValue.map(String).join("\n") : ""
  }
  return draft
}

function splitParameterInput(value: string) {
  const seen = new Set<string>()
  const items: string[] = []
  for (const part of value.split(/[\n,]/)) {
    const cleaned = part.trim()
    if (!cleaned) {
      continue
    }
    const key = cleaned.toLowerCase()
    if (seen.has(key)) {
      continue
    }
    seen.add(key)
    items.push(cleaned)
  }
  return items
}

function MessageCard({ message }: { message: MessageItem }) {
  const [open, setOpen] = useState(false)
  const toggleOpen = () => setOpen((value) => !value)
  return (
    <Card
      role="button"
      tabIndex={0}
      className="cursor-pointer"
      onClick={toggleOpen}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault()
          toggleOpen()
        }
      }}
    >
      <CardHeader>
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="mb-2 flex flex-wrap items-center gap-2">
              <SourceBadge sourceId={message.source_id} label={message.source_label} />
              <StatusBadge
                status={message.processing_status}
                label={processingStatusLabel(message.processing_status)}
              />
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <CardTitle className="text-xl font-medium">{message.title}</CardTitle>
            </div>
          </div>
          <div className="flex shrink-0 flex-col items-end gap-2">
            <div className="flex items-start gap-3">
              <CardDescription className="mt-1 flex items-center gap-2 text-right text-xs leading-5">
                <span>抓取：{formatDateTime(message.collected_at)}</span>
                <span className="h-3 w-px bg-border" aria-hidden="true" />
                <span>发布：{formatDateTime(message.published_at)}</span>
            </CardDescription>
              <Button
                variant="ghost"
                size="icon-sm"
                onClick={(event) => {
                  event.stopPropagation()
                  toggleOpen()
                }}
              >
                <ChevronDownIcon className={cn("transition-transform", open && "rotate-180")} />
                <span className="sr-only">{open ? "收起" : "展开"}</span>
              </Button>
            </div>
            {message.url ? (
              <Button
                variant="outline"
                size="sm"
                asChild
                onClick={(event) => event.stopPropagation()}
              >
                <a href={message.url} target="_blank" rel="noreferrer">
                  <ExternalLinkIcon data-icon="inline-start" />
                  原始信源
                </a>
              </Button>
            ) : null}
          </div>
        </div>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <p className="border-t pt-3 text-sm font-normal leading-6 text-foreground">
          {message.summary || "暂无摘要"}
        </p>
        {open ? (
          <p className="border-t pt-3 font-mono text-xs leading-6 text-muted-foreground">
            {message.body || "暂无数据"}
          </p>
        ) : null}
      </CardContent>
    </Card>
  )
}

function SourceBadge({ sourceId, label }: { sourceId: string; label: string }) {
  return (
    <Badge
      variant="outline"
      className={cn("source-badge rounded-[4px] font-semibold", sourceToneClass(sourceId))}
    >
      {label}
    </Badge>
  )
}

function sourceToneClass(sourceId: string) {
  if (sourceId.includes("newswire")) {
    return "source-tone-blue"
  }
  if (sourceId.includes("stocktwits")) {
    return "source-tone-green"
  }
  if (sourceId.includes("tikhub") || sourceId.includes("x_")) {
    return "source-tone-red"
  }
  if (sourceId.includes("benzinga")) {
    return "source-tone-yellow"
  }
  return "source-tone-gray"
}
