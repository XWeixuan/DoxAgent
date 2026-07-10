import { useCallback, useEffect, useMemo, useState } from "react"
import { useParams } from "react-router-dom"
import { CircleAlertIcon, PlayIcon } from "lucide-react"
import { toast } from "sonner"
import {
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  Pie,
  PieChart,
  XAxis,
  YAxis,
} from "recharts"

import { Button } from "@/components/ui/button"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import {
  ChartContainer,
  ChartLegend,
  ChartLegendContent,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig,
} from "@/components/ui/chart"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { Progress } from "@/components/ui/progress"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"
import { dashboardApi } from "@/lib/dashboard-api"
import type {
  CostAudit,
  CostRecord,
  DashboardEvent,
  PageResult,
  Period,
  RevenueAudit,
  RevenueAuditDetail,
  RevenueBasis,
  RevenueTrend,
  TradeIntent,
} from "@/lib/dashboard-types"
import {
  auditStatusLabel,
  formatCurrency,
  formatDateTime,
  formatNumber,
  formatSignedPercent,
} from "@/lib/format"
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

const periodOptions: Array<{ value: Period; label: string }> = [
  { value: "today", label: "今日" },
  { value: "7d", label: "近 7 日" },
  { value: "30d", label: "近 30 日" },
]

const costLimit = 10
const revenueLimit = 20

const revenueBasisOptions: Array<{ value: RevenueBasis; label: string }> = [
  { value: "system_executable", label: "系统可执行收益" },
  { value: "message_bus", label: "Message Bus 收益" },
  { value: "ideal_signal", label: "理想信号收益" },
]

const revenueChartConfig = {
  pnl_usd: { label: "收益", color: "var(--chart-1)" },
  trade_intent_count: { label: "交易意图", color: "var(--chart-2)" },
} satisfies ChartConfig

const costChartConfig = {
  total_cost_usd: { label: "总成本", color: "var(--chart-1)" },
  total_tokens: { label: "Token", color: "var(--chart-2)" },
  cost_usd: { label: "成本", color: "var(--chart-3)" },
} satisfies ChartConfig

function periodMetricPrefix(period: Period) {
  if (period === "7d") {
    return "近 7 日"
  }
  if (period === "30d") {
    return "近 30 日"
  }
  return "今日"
}

function revenueBasisLabel(basis: RevenueBasis) {
  return revenueBasisOptions.find((item) => item.value === basis)?.label ?? basis
}

function decisionSourceLabel(source: string) {
  if (source === "w2_policy_direct") {
    return "W2 Policy 直接生成"
  }
  if (source === "o3_duty_expert") {
    return "O3 值班专家生成"
  }
  if (source === "o3_upstream_retained") {
    return "O3 异常保留上游意图"
  }
  return source
}

export function AuditPage() {
  const ticker = useParams().ticker?.toUpperCase() ?? "MU"
  const [view, setView] = useState<"revenue" | "cost">("revenue")
  const [period, setPeriod] = useState<Period>("today")
  const [revenueBasis, setRevenueBasis] = useState<RevenueBasis>("system_executable")
  const [groupBy, setGroupBy] = useState<"node" | "model">("node")
  const [nodeFilter, setNodeFilter] = useState("all")
  const [modelFilter, setModelFilter] = useState("all")
  const [statusFilter, setStatusFilter] = useState("all")
  const [costPage, setCostPage] = useState<PageResult<CostRecord> | null>(null)
  const [revenuePage, setRevenuePage] = useState<PageResult<TradeIntent> | null>(null)
  const [loadingMore, setLoadingMore] = useState(false)
  const [loadingMoreRevenue, setLoadingMoreRevenue] = useState(false)
  const [runningRevenueAudit, setRunningRevenueAudit] = useState(false)
  const [selectedTrade, setSelectedTrade] = useState<TradeIntent | null>(null)
  const [selectedTradeDetail, setSelectedTradeDetail] = useState<RevenueAuditDetail | null>(null)
  const [loadingTradeDetail, setLoadingTradeDetail] = useState(false)

  const revenueLoader = useCallback(
    () => dashboardApi.revenueAudit(ticker, period, revenueBasis),
    [period, revenueBasis, ticker]
  )
  const revenueTrendLoader = useCallback(
    () => dashboardApi.revenueTrend(ticker, period, revenueBasis),
    [period, revenueBasis, ticker]
  )
  const revenueRecordsLoader = useCallback(
    () =>
      dashboardApi.revenueRecords(ticker, {
        period,
        basis: revenueBasis,
        limit: revenueLimit,
      }),
    [period, revenueBasis, ticker]
  )
  const costLoader = useCallback(() => dashboardApi.costAudit(ticker, period, groupBy), [groupBy, period, ticker])
  const costDetailsLoader = useCallback(
    () =>
      dashboardApi.costDetails(ticker, {
        period,
        limit: costLimit,
        node: nodeFilter === "all" ? undefined : nodeFilter,
        model: modelFilter === "all" ? undefined : modelFilter,
        status: statusFilter === "all" ? undefined : statusFilter,
      }),
    [modelFilter, nodeFilter, period, statusFilter, ticker]
  )

  const revenue = useDashboardQuery(revenueLoader)
  const revenueTrend = useDashboardQuery(revenueTrendLoader)
  const revenueRecords = useDashboardQuery(revenueRecordsLoader)
  const cost = useDashboardQuery(costLoader)
  const costDetails = useDashboardQuery(costDetailsLoader)
  const reloadRevenue = revenue.reload
  const reloadRevenueTrend = revenueTrend.reload
  const reloadRevenueRecords = revenueRecords.reload

  useEffect(() => {
    setSelectedTrade(null)
    setSelectedTradeDetail(null)
  }, [period, revenueBasis, ticker])

  useEffect(() => {
    if (revenueRecords.data) {
      setRevenuePage(revenueRecords.data)
    }
  }, [revenueRecords.data])

  useEffect(() => {
    if (costDetails.data) {
      setCostPage(costDetails.data)
    }
  }, [costDetails.data])

  const handleEvent = useCallback(
    (event: DashboardEvent) => {
      if (event.event_type === "audit.revenue.status_changed") {
        void reloadRevenue()
        void reloadRevenueTrend()
        void reloadRevenueRecords()
      }
    },
    [reloadRevenue, reloadRevenueRecords, reloadRevenueTrend]
  )

  const events = useDashboardEvents({
    ticker,
    eventTypes: ["audit.revenue.status_changed"],
    onEvent: handleEvent,
  })

  const nodeOptions = useMemo(() => {
    const values = new Set<string>()
    for (const item of costPage?.items ?? []) {
      values.add(item.node)
    }
    for (const item of cost.data?.breakdown.by_node ?? []) {
      values.add(item.key)
    }
    return [{ value: "all", label: "全部节点" }, ...Array.from(values).map((value) => ({ value, label: value }))]
  }, [cost.data, costPage])

  const modelOptions = useMemo(() => {
    const values = new Set<string>()
    for (const item of costPage?.items ?? []) {
      values.add(item.model)
    }
    for (const item of cost.data?.breakdown.by_model ?? []) {
      values.add(item.key)
    }
    return [{ value: "all", label: "全部模型" }, ...Array.from(values).map((value) => ({ value, label: value }))]
  }, [cost.data, costPage])

  const statusOptions = useMemo(() => {
    const values = new Set<string>()
    for (const item of costPage?.items ?? []) {
      values.add(item.status)
    }
    return [{ value: "all", label: "全部状态" }, ...Array.from(values).map((value) => ({ value, label: value }))]
  }, [costPage])

  const runRevenueAudit = async () => {
    setRunningRevenueAudit(true)
    try {
      const run = await dashboardApi.runRevenueAudit(ticker)
      toast.success(`收益审计已完成：${run.status}`)
      await Promise.all([
        revenue.reload(),
        revenueTrend.reload(),
        revenueRecords.reload(),
      ])
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error))
    } finally {
      setRunningRevenueAudit(false)
    }
  }

  const selectTrade = async (trade: TradeIntent) => {
    setSelectedTrade(trade)
    setSelectedTradeDetail(null)
    setLoadingTradeDetail(true)
    try {
      setSelectedTradeDetail(await dashboardApi.revenueRecordDetail(ticker, trade.record_id))
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error))
    } finally {
      setLoadingTradeDetail(false)
    }
  }

  const loadMoreRevenue = async () => {
    if (!revenuePage?.page.next_cursor) {
      return
    }
    setLoadingMoreRevenue(true)
    try {
      const next = await dashboardApi.revenueRecords(ticker, {
        period,
        basis: revenueBasis,
        limit: revenueLimit,
        cursor: revenuePage.page.next_cursor,
      })
      setRevenuePage({
        items: [...revenuePage.items, ...next.items],
        page: next.page,
      })
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error))
    } finally {
      setLoadingMoreRevenue(false)
    }
  }

  const loadMoreCost = async () => {
    if (!costPage?.page.next_cursor) {
      return
    }
    setLoadingMore(true)
    try {
      const next = await dashboardApi.costDetails(ticker, {
        period,
        limit: costLimit,
        cursor: costPage.page.next_cursor,
        node: nodeFilter === "all" ? undefined : nodeFilter,
        model: modelFilter === "all" ? undefined : modelFilter,
        status: statusFilter === "all" ? undefined : statusFilter,
      })
      setCostPage({
        items: [...costPage.items, ...next.items],
        page: next.page,
      })
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error))
    } finally {
      setLoadingMore(false)
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title={`${ticker} 收益 / 成本审计`}
        eyebrow="Revenue & Cost Audit"
        description="回答交易意图是否有效，以及运行成本是否可控。"
        lastUpdatedAt={view === "revenue" ? revenue.lastUpdatedAt : cost.lastUpdatedAt}
        actions={
          <>
            <StatusBadge status={events.state} label={`SSE：${events.state}`} />
            <ToggleGroup
              type="single"
              value={view}
              onValueChange={(value) => value && setView(value as "revenue" | "cost")}
            >
              <ToggleGroupItem value="revenue">收益审计</ToggleGroupItem>
              <ToggleGroupItem value="cost">成本审计</ToggleGroupItem>
            </ToggleGroup>
            <RefreshButton
              refreshing={
                revenue.isRefreshing ||
                revenueTrend.isRefreshing ||
                revenueRecords.isRefreshing ||
                cost.isRefreshing ||
                costDetails.isRefreshing
              }
              onClick={() => {
                void revenue.reload()
                void revenueTrend.reload()
                void revenueRecords.reload()
                void cost.reload()
                void costDetails.reload()
              }}
            />
          </>
        }
      />

      {events.error ? <ErrorState title="SSE 连接异常" message={events.error} /> : null}
      {[revenue.error, revenueTrend.error, revenueRecords.error, cost.error, costDetails.error]
        .filter(Boolean)
        .map((message) => (
          <ErrorState key={message} message={message ?? ""} />
        ))}

      <ToggleGroup
        type="single"
        value={period}
        onValueChange={(value) => value && setPeriod(value as Period)}
      >
        {periodOptions.map((item) => (
          <ToggleGroupItem key={item.value} value={item.value}>
            {item.label}
          </ToggleGroupItem>
        ))}
      </ToggleGroup>

      {view === "revenue" ? (
        <ToggleGroup
          type="single"
          value={revenueBasis}
          onValueChange={(value) => value && setRevenueBasis(value as RevenueBasis)}
        >
          {revenueBasisOptions.map((item) => (
            <ToggleGroupItem key={item.value} value={item.value}>
              {item.label}
            </ToggleGroupItem>
          ))}
        </ToggleGroup>
      ) : null}

      {view === "revenue" ? (
        <RevenueAuditView
          loading={revenue.isLoading || revenueTrend.isLoading || revenueRecords.isLoading}
          period={period}
          basis={revenueBasis}
          selectedTrade={selectedTrade}
          selectedTradeDetail={selectedTradeDetail}
          loadingTradeDetail={loadingTradeDetail}
          onSelectTrade={(trade) => void selectTrade(trade)}
          onCloseTrade={() => {
            setSelectedTrade(null)
            setSelectedTradeDetail(null)
          }}
          onRunRevenueAudit={() => void runRevenueAudit()}
          runningRevenueAudit={runningRevenueAudit}
          revenue={revenue.data}
          trend={revenueTrend.data}
          revenuePage={revenuePage}
          loadingMore={loadingMoreRevenue}
          onLoadMore={() => void loadMoreRevenue()}
        />
      ) : (
        <CostAuditView
          loading={cost.isLoading || costDetails.isLoading}
          period={period}
          groupBy={groupBy}
          setGroupBy={setGroupBy}
          nodeFilter={nodeFilter}
          setNodeFilter={setNodeFilter}
          modelFilter={modelFilter}
          setModelFilter={setModelFilter}
          statusFilter={statusFilter}
          setStatusFilter={setStatusFilter}
          nodeOptions={nodeOptions}
          modelOptions={modelOptions}
          statusOptions={statusOptions}
          cost={cost.data}
          costPage={costPage}
          loadingMore={loadingMore}
          onLoadMore={() => void loadMoreCost()}
        />
      )}
    </div>
  )
}

function RevenueAuditView({
  revenue,
  trend,
  revenuePage,
  period,
  basis,
  loading,
  selectedTrade,
  selectedTradeDetail,
  loadingTradeDetail,
  onSelectTrade,
  onCloseTrade,
  onRunRevenueAudit,
  runningRevenueAudit,
  loadingMore,
  onLoadMore,
}: {
  revenue: RevenueAudit | null
  trend: RevenueTrend | null
  revenuePage: PageResult<TradeIntent> | null
  period: Period
  basis: RevenueBasis
  loading: boolean
  selectedTrade: TradeIntent | null
  selectedTradeDetail: RevenueAuditDetail | null
  loadingTradeDetail: boolean
  onSelectTrade: (trade: TradeIntent) => void
  onCloseTrade: () => void
  onRunRevenueAudit: () => void
  runningRevenueAudit: boolean
  loadingMore: boolean
  onLoadMore: () => void
}) {
  if (loading && (!revenue || !trend || !revenuePage)) {
    return <LoadingGrid rows={6} />
  }
  if (!revenue || !trend || !revenuePage) {
    return <EmptyState title="暂无收益审计" />
  }
  const labelPrefix = periodMetricPrefix(period)
  const coveragePercent =
    revenue.coverage_rate === null ? null : revenue.coverage_rate * 100
  const losses = [
    { label: "抓取损耗", value: revenue.latency_losses.capture_loss },
    { label: "决策损耗", value: revenue.latency_losses.decision_loss },
    { label: "总延迟损耗", value: revenue.latency_losses.total_latency_loss },
  ]
  return (
    <div className="flex flex-col gap-6">
      <MetricStrip>
        <MetricCell
          title={`${labelPrefix}交易意图`}
          value={formatNumber(revenue.trade_intent_count)}
          status="normal"
        />
        <MetricCell
          title={`${labelPrefix}可审计交易`}
          value={formatNumber(revenue.auditable_trade_count)}
          status="normal"
        />
        <MetricCell
          title={`${labelPrefix}已审计交易`}
          value={formatNumber(revenue.audited_trade_count)}
          status="normal"
        />
        <MetricCell
          title="审计覆盖率"
          value={coveragePercent === null ? "暂无数据" : `${coveragePercent.toFixed(0)}%`}
          status={coveragePercent !== null && coveragePercent < 100 ? "degraded" : "normal"}
        />
        <MetricCell
          title={`${labelPrefix}模拟 PnL`}
          value={formatCurrency(revenue.simulated_pnl_usd)}
          status={
            revenue.simulated_pnl_usd !== null && revenue.simulated_pnl_usd >= 0
              ? "normal"
              : "failed"
          }
        />
        <MetricCell
          title={`${labelPrefix}模拟收益率`}
          value={formatSignedPercent(revenue.simulated_return_pct)}
          status={
            revenue.simulated_return_pct !== null && revenue.simulated_return_pct >= 0
              ? "normal"
              : "failed"
          }
        />
        <MetricCell
          title={`${labelPrefix}胜率`}
          value={
            revenue.win_rate === null ? "暂无数据" : `${(revenue.win_rate * 100).toFixed(0)}%`
          }
          status="normal"
        />
        <MetricCell title="审计状态" value={auditStatusLabel(revenue.status)} status={revenue.status} />
      </MetricStrip>

      {coveragePercent !== null && coveragePercent < 100 ? (
        <Alert>
          <CircleAlertIcon />
          <AlertTitle>收益数据尚不完整</AlertTitle>
          <AlertDescription>
            当前口径覆盖率为 {coveragePercent.toFixed(0)}%。收益、胜率和延迟损耗只基于已完成审计的交易。
            <Progress value={coveragePercent} className="mt-3 max-w-md" />
          </AlertDescription>
        </Alert>
      ) : null}

      <Section
        title="时间延迟损耗"
        description="只比较同一交易意图中两个口径均已审计的匹配样本，避免不同覆盖率造成假损耗。"
      >
        <div className="grid gap-4 md:grid-cols-3">
          {losses.map((loss) => (
            <Card key={loss.label}>
              <CardHeader>
                <CardTitle>{loss.label}</CardTitle>
                <CardDescription>
                  匹配交易 {formatNumber(loss.value.matched_trade_count)} 条
                </CardDescription>
              </CardHeader>
              <CardContent className="flex flex-col gap-1">
                <div>{formatCurrency(loss.value.pnl_usd)}</div>
                <div className="text-muted-foreground">
                  {formatSignedPercent(loss.value.return_pct_points)}
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      </Section>

      <Section
        title={`${revenueBasisLabel(basis)}趋势`}
        description={`退出策略：${revenue.exit_rule}；方法版本：${revenue.method_version}`}
        actions={
          <Button variant="outline" onClick={onRunRevenueAudit} disabled={runningRevenueAudit}>
            <PlayIcon data-icon="inline-start" />
            {runningRevenueAudit ? "正在计算" : "手动补跑"}
          </Button>
        }
      >
        {trend.items.length > 0 ? (
          <Card>
            <CardHeader>
              <CardTitle>每日模拟 PnL 与交易意图数</CardTitle>
              <CardDescription>不完整日期会在明细覆盖率中保留，不做价格估算。</CardDescription>
            </CardHeader>
            <CardContent className="pt-6">
              <ChartContainer config={revenueChartConfig} className="h-72 w-full">
                <LineChart data={trend.items} accessibilityLayer>
                  <CartesianGrid vertical={false} />
                  <XAxis dataKey="date" tickLine={false} axisLine={false} />
                  <YAxis yAxisId="left" tickLine={false} axisLine={false} />
                  <YAxis yAxisId="right" orientation="right" tickLine={false} axisLine={false} />
                  <ChartTooltip content={<ChartTooltipContent />} />
                  <ChartLegend content={<ChartLegendContent />} />
                  <Line yAxisId="left" type="monotone" dataKey="pnl_usd" stroke="var(--color-pnl_usd)" strokeWidth={2} dot />
                  <Line yAxisId="right" type="monotone" dataKey="trade_intent_count" stroke="var(--color-trade_intent_count)" strokeWidth={2} dot />
                </LineChart>
              </ChartContainer>
            </CardContent>
          </Card>
        ) : (
          <EmptyState title="暂无收益趋势" />
        )}
      </Section>

      <Section title="交易意图列表" description="点击记录查看触发原因、相关消息和 agent 输出摘要。">
        {revenuePage.items.length > 0 ? (
          <Card>
            <CardContent className="overflow-x-auto p-0 biome-scrollbar">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>时间</TableHead>
                    <TableHead>Ticker</TableHead>
                    <TableHead>触发消息</TableHead>
                    <TableHead>Policy</TableHead>
                    <TableHead>决策来源</TableHead>
                    <TableHead>动作</TableHead>
                    <TableHead>收益口径</TableHead>
                    <TableHead>入场价</TableHead>
                    <TableHead>退出价</TableHead>
                    <TableHead>滑点</TableHead>
                    <TableHead>收益率</TableHead>
                    <TableHead>PnL</TableHead>
                    <TableHead>状态</TableHead>
                    <TableHead>详情</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {revenuePage.items.map((trade) => (
                    <TableRow key={trade.record_id}>
                      <TableCell>{formatDateTime(trade.time)}</TableCell>
                      <TableCell>{trade.ticker}</TableCell>
                      <TableCell className="max-w-48 truncate">
                        {trade.trigger_message_id || "暂无数据"}
                      </TableCell>
                      <TableCell>{trade.trigger_policy_id || "暂无数据"}</TableCell>
                      <TableCell>{decisionSourceLabel(trade.decision_source)}</TableCell>
                      <TableCell>{trade.action}</TableCell>
                      <TableCell>{revenueBasisLabel(trade.basis)}</TableCell>
                      <TableCell>{formatCurrency(trade.estimated_entry_price)}</TableCell>
                      <TableCell>{formatCurrency(trade.exit_price)}</TableCell>
                      <TableCell>{trade.slippage_bps} bps / 边</TableCell>
                      <TableCell>{formatSignedPercent(trade.return_pct)}</TableCell>
                      <TableCell>{formatCurrency(trade.pnl_usd)}</TableCell>
                      <TableCell>
                        <StatusBadge status={trade.status} label={trade.status} />
                      </TableCell>
                      <TableCell>
                        <Button variant="ghost" size="sm" onClick={() => onSelectTrade(trade)}>
                          查看
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
            <LoadMoreButton
              hasMore={revenuePage.page.has_more}
              loading={loadingMore}
              onClick={onLoadMore}
            />
          </Card>
        ) : (
          <EmptyState title="暂无交易意图" />
        )}
      </Section>

      <Sheet open={selectedTrade !== null} onOpenChange={(open) => !open && onCloseTrade()}>
        <SheetContent className="overflow-y-auto sm:max-w-2xl">
          <SheetHeader>
            <SheetTitle>交易意图详情：{selectedTrade?.record_id}</SheetTitle>
            <SheetDescription>
              {selectedTrade ? formatDateTime(selectedTrade.time) : "收益审计详情"}
            </SheetDescription>
          </SheetHeader>
          {loadingTradeDetail ? <LoadingGrid rows={3} /> : null}
          {selectedTradeDetail ? (
            <div className="flex flex-col gap-6 px-4 pb-6">
              <KeyValueList
                items={[
                  { label: "决策来源", value: decisionSourceLabel(selectedTradeDetail.decision_source) },
                  { label: "触发 Policy", value: selectedTradeDetail.trigger_policy || "未知 policy" },
                  { label: "关联消息", value: selectedTradeDetail.source_message_id },
                  { label: "消息发布时间", value: formatDateTime(selectedTradeDetail.published_at) },
                  { label: "Message Bus 入队", value: formatDateTime(selectedTradeDetail.message_bus_event_time) },
                  { label: "Runtime 开始", value: formatDateTime(selectedTradeDetail.runtime_started_at) },
                  { label: "意图生成", value: formatDateTime(selectedTradeDetail.intent_generated_at) },
                  { label: "触发原因", value: selectedTradeDetail.trigger_reason || "暂无摘要" },
                  { label: "消息摘要", value: selectedTradeDetail.message_summary || "暂无摘要" },
                  { label: "Agent 摘要", value: selectedTradeDetail.agent_summary || "暂无摘要" },
                ]}
              />
              <Card>
                <CardHeader>
                  <CardTitle>三种收益口径</CardTitle>
                  <CardDescription>每种口径独立保留时间缺失或行情失败原因。</CardDescription>
                </CardHeader>
                <CardContent className="overflow-x-auto p-0">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>口径</TableHead>
                        <TableHead>时间锚点</TableHead>
                        <TableHead>入场</TableHead>
                        <TableHead>退出</TableHead>
                        <TableHead>收益率</TableHead>
                        <TableHead>PnL</TableHead>
                        <TableHead>行情源</TableHead>
                        <TableHead>状态</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {selectedTradeDetail.results.map((result) => (
                        <TableRow key={result.result_id}>
                          <TableCell>{revenueBasisLabel(result.basis)}</TableCell>
                          <TableCell>{formatDateTime(result.anchor_time)}</TableCell>
                          <TableCell>{formatCurrency(result.simulated_entry_price)}</TableCell>
                          <TableCell>{formatCurrency(result.simulated_exit_price)}</TableCell>
                          <TableCell>{formatSignedPercent(result.simulated_return_pct)}</TableCell>
                          <TableCell>{formatCurrency(result.simulated_pnl_usd)}</TableCell>
                          <TableCell>{result.data_source || "暂无数据"}</TableCell>
                          <TableCell>
                            <StatusBadge status={result.status} label={result.status} />
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </CardContent>
              </Card>
              {selectedTradeDetail.results.some((result) => result.failure_reason) ? (
                <Alert>
                  <CircleAlertIcon />
                  <AlertTitle>未完成口径</AlertTitle>
                  <AlertDescription>
                    {selectedTradeDetail.results
                      .filter((result) => result.failure_reason)
                      .map((result) => `${revenueBasisLabel(result.basis)}：${result.failure_reason}`)
                      .join("；")}
                  </AlertDescription>
                </Alert>
              ) : null}
            </div>
          ) : null}
        </SheetContent>
      </Sheet>
    </div>
  )
}

function CostAuditView({
  cost,
  period,
  costPage,
  loading,
  groupBy,
  setGroupBy,
  nodeFilter,
  setNodeFilter,
  modelFilter,
  setModelFilter,
  statusFilter,
  setStatusFilter,
  nodeOptions,
  modelOptions,
  statusOptions,
  loadingMore,
  onLoadMore,
}: {
  cost: CostAudit | null
  period: Period
  costPage: PageResult<CostRecord> | null
  loading: boolean
  groupBy: "node" | "model"
  setGroupBy: (value: "node" | "model") => void
  nodeFilter: string
  setNodeFilter: (value: string) => void
  modelFilter: string
  setModelFilter: (value: string) => void
  statusFilter: string
  setStatusFilter: (value: string) => void
  nodeOptions: Array<{ value: string; label: string }>
  modelOptions: Array<{ value: string; label: string }>
  statusOptions: Array<{ value: string; label: string }>
  loadingMore: boolean
  onLoadMore: () => void
}) {
  if (loading && !cost) {
    return <LoadingGrid rows={6} />
  }
  if (!cost) {
    return <EmptyState title="暂无成本审计" />
  }

  const labelPrefix = periodMetricPrefix(period)
  const breakdown = groupBy === "node" ? cost.breakdown.by_node : cost.breakdown.by_model
  const pieColors = ["var(--chart-1)", "var(--chart-2)", "var(--chart-3)", "var(--chart-4)"]

  return (
    <div className="flex flex-col gap-6">
      <MetricStrip>
        <MetricCell
          title="Audit status"
          value={<StatusBadge status={cost.status} label={auditStatusLabel(cost.status)} />}
          status={cost.status}
        />
        <MetricCell title={`${labelPrefix}token 总量`} value={formatNumber(cost.kpis.today_total_tokens)} status="normal" />
        <MetricCell title={`${labelPrefix}总成本`} value={formatCurrency(cost.kpis.today_total_cost_usd)} status="normal" />
        <MetricCell title={`${labelPrefix}Input tokens`} value={formatNumber(cost.kpis.today_input_tokens)} status="normal" />
        <MetricCell title={`${labelPrefix}Output tokens`} value={formatNumber(cost.kpis.today_output_tokens)} status="normal" />
        <MetricCell title={`${labelPrefix}成本最高节点`} value={cost.kpis.highest_cost_node || "暂无数据"} status="degraded" />
        <MetricCell
          title={`${labelPrefix}重试成本`}
          value={formatCurrency(cost.kpis.retry_cost_usd)}
          status={cost.kpis.retry_cost_usd !== null && cost.kpis.retry_cost_usd > 0 ? "degraded" : "normal"}
        />
      </MetricStrip>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,3fr)_minmax(260px,1fr)]">
        <Section title="成本趋势" description={`${labelPrefix}总成本与 token 使用量趋势。`}>
          {cost.trend.length > 0 ? (
            <Card>
              <CardContent className="pt-6">
                <ChartContainer config={costChartConfig} className="h-72 w-full">
                  <LineChart data={cost.trend} accessibilityLayer>
                    <CartesianGrid vertical={false} />
                    <XAxis dataKey="date" tickLine={false} axisLine={false} />
                    <YAxis yAxisId="left" tickLine={false} axisLine={false} />
                    <YAxis yAxisId="right" orientation="right" tickLine={false} axisLine={false} />
                    <ChartTooltip content={<ChartTooltipContent />} />
                    <ChartLegend content={<ChartLegendContent />} />
                    <Line yAxisId="left" type="monotone" dataKey="total_cost_usd" stroke="var(--color-total_cost_usd)" strokeWidth={2} dot />
                    <Line yAxisId="right" type="monotone" dataKey="total_tokens" stroke="var(--color-total_tokens)" strokeWidth={2} dot />
                  </LineChart>
                </ChartContainer>
              </CardContent>
            </Card>
          ) : (
            <EmptyState title="暂无成本趋势" />
          )}
        </Section>

        <Section
          title="成本占比"
          actions={
            <ToggleGroup
              type="single"
              value={groupBy}
              onValueChange={(value) => value && setGroupBy(value as "node" | "model")}
            >
              <ToggleGroupItem value="node">按节点</ToggleGroupItem>
              <ToggleGroupItem value="model">按模型</ToggleGroupItem>
            </ToggleGroup>
          }
        >
          {breakdown.length > 0 ? (
            <Card>
              <CardHeader>
                <CardTitle className="text-lg font-light">
                  {groupBy === "node" ? "节点占比" : "模型占比"}
                </CardTitle>
                <CardDescription>{labelPrefix}cost_usd 聚合。</CardDescription>
              </CardHeader>
              <CardContent>
                <ChartContainer config={costChartConfig} className="h-64 w-full">
                  <PieChart accessibilityLayer>
                    <ChartTooltip content={<ChartTooltipContent hideLabel />} />
                    <Pie data={breakdown} dataKey="cost_usd" nameKey="label" innerRadius={42} outerRadius={82}>
                      {breakdown.map((item, index) => (
                        <Cell key={item.key} fill={pieColors[index % pieColors.length]} />
                      ))}
                    </Pie>
                  </PieChart>
                </ChartContainer>
              </CardContent>
            </Card>
          ) : (
            <EmptyState title="暂无成本占比" />
          )}
        </Section>
      </div>

      <Section
        title="成本明细"
        description="当前 ticker 已由路由限定；下方支持节点、模型和状态筛选。"
        actions={
          <>
            <FilterSelect value={nodeFilter} placeholder="节点" options={nodeOptions} onChange={setNodeFilter} />
            <FilterSelect value={modelFilter} placeholder="模型" options={modelOptions} onChange={setModelFilter} />
            <FilterSelect value={statusFilter} placeholder="状态" options={statusOptions} onChange={setStatusFilter} />
          </>
        }
      >
        {costPage && costPage.items.length > 0 ? (
          <Card>
            <CardContent className="overflow-x-auto p-0 biome-scrollbar">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>时间</TableHead>
                    <TableHead>Ticker</TableHead>
                    <TableHead>节点</TableHead>
                    <TableHead>模型</TableHead>
                    <TableHead>Input</TableHead>
                    <TableHead>Output</TableHead>
                    <TableHead>成本</TableHead>
                    <TableHead>重试</TableHead>
                    <TableHead>状态</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {costPage.items.map((record) => (
                    <TableRow key={record.cost_record_id}>
                      <TableCell>{formatDateTime(record.time)}</TableCell>
                      <TableCell>{record.ticker}</TableCell>
                      <TableCell>{record.node}</TableCell>
                      <TableCell>{record.model}</TableCell>
                      <TableCell>{formatNumber(record.input_tokens)}</TableCell>
                      <TableCell>{formatNumber(record.output_tokens)}</TableCell>
                      <TableCell>{formatCurrency(record.cost_usd)}</TableCell>
                      <TableCell>{record.is_retry ? "是" : "否"}</TableCell>
                      <TableCell>
                        <StatusBadge status={record.status} label={record.status} />
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
            <LoadMoreButton
              hasMore={costPage.page.has_more}
              loading={loadingMore}
              onClick={onLoadMore}
            />
          </Card>
        ) : (
          <EmptyState title="暂无成本明细" />
        )}
      </Section>
    </div>
  )
}
