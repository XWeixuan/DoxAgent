import { useCallback, useEffect, useMemo, useState } from "react"
import { useParams } from "react-router-dom"
import { PlayIcon } from "lucide-react"
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
import { dashboardApi } from "@/lib/dashboard-api"
import type {
  CostAudit,
  CostRecord,
  DashboardEvent,
  PageResult,
  Period,
  RevenueAudit,
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

export function AuditPage() {
  const ticker = useParams().ticker?.toUpperCase() ?? "MU"
  const [view, setView] = useState<"revenue" | "cost">("revenue")
  const [period, setPeriod] = useState<Period>("today")
  const [groupBy, setGroupBy] = useState<"node" | "model">("node")
  const [nodeFilter, setNodeFilter] = useState("all")
  const [modelFilter, setModelFilter] = useState("all")
  const [statusFilter, setStatusFilter] = useState("all")
  const [costPage, setCostPage] = useState<PageResult<CostRecord> | null>(null)
  const [loadingMore, setLoadingMore] = useState(false)
  const [selectedTrade, setSelectedTrade] = useState<TradeIntent | null>(null)

  const revenueLoader = useCallback(() => dashboardApi.revenueAudit(ticker, period), [period, ticker])
  const costLoader = useCallback(() => dashboardApi.costAudit(ticker, period, groupBy), [groupBy, period, ticker])
  const costDetailsLoader = useCallback(
    () =>
      dashboardApi.costDetails(ticker, {
        limit: costLimit,
        node: nodeFilter === "all" ? undefined : nodeFilter,
        model: modelFilter === "all" ? undefined : modelFilter,
        status: statusFilter === "all" ? undefined : statusFilter,
      }),
    [modelFilter, nodeFilter, statusFilter, ticker]
  )

  const revenue = useDashboardQuery(revenueLoader, { intervalMs: 60000 })
  const cost = useDashboardQuery(costLoader, { intervalMs: 60000 })
  const costDetails = useDashboardQuery(costDetailsLoader, { intervalMs: 60000 })
  const reloadRevenue = revenue.reload
  const reloadCost = cost.reload
  const reloadCostDetails = costDetails.reload

  useEffect(() => {
    setSelectedTrade(null)
  }, [period, ticker])

  useEffect(() => {
    if (costDetails.data) {
      setCostPage(costDetails.data)
    }
  }, [costDetails.data])

  const handleEvent = useCallback(
    (event: DashboardEvent) => {
      if (event.event_type === "audit.revenue.status_changed") {
        void reloadRevenue()
      }
      if (event.event_type === "audit.cost.status_changed") {
        void reloadCost()
        void reloadCostDetails()
      }
    },
    [reloadCost, reloadCostDetails, reloadRevenue]
  )

  const events = useDashboardEvents({
    ticker,
    eventTypes: ["audit.revenue.status_changed", "audit.cost.status_changed"],
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
    try {
      await dashboardApi.runRevenueAudit(ticker)
      toast.success("收益审计已触发。")
      await revenue.reload()
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error))
    }
  }

  const loadMoreCost = async () => {
    if (!costPage?.page.next_cursor) {
      return
    }
    setLoadingMore(true)
    try {
      const next = await dashboardApi.costDetails(ticker, {
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
              refreshing={revenue.isRefreshing || cost.isRefreshing || costDetails.isRefreshing}
              onClick={() => {
                void revenue.reload()
                void cost.reload()
                void costDetails.reload()
              }}
            />
          </>
        }
      />

      {events.error ? <ErrorState title="SSE 连接异常" message={events.error} /> : null}
      {[revenue.error, cost.error, costDetails.error]
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
        <RevenueAuditView
          loading={revenue.isLoading}
          period={period}
          selectedTrade={selectedTrade}
          onSelectTrade={setSelectedTrade}
          onRunRevenueAudit={() => void runRevenueAudit()}
          revenue={revenue.data}
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
  period,
  loading,
  selectedTrade,
  onSelectTrade,
  onRunRevenueAudit,
}: {
  revenue: RevenueAudit | null
  period: Period
  loading: boolean
  selectedTrade: TradeIntent | null
  onSelectTrade: (trade: TradeIntent | null) => void
  onRunRevenueAudit: () => void
}) {
  if (loading && !revenue) {
    return <LoadingGrid rows={6} />
  }
  if (!revenue) {
    return <EmptyState title="暂无收益审计" />
  }
  const labelPrefix = periodMetricPrefix(period)
  return (
    <div className="flex flex-col gap-6">
      <MetricStrip>
        <MetricCell title={`${labelPrefix}交易意图`} value={formatNumber(revenue.kpis.today_trade_intent_count)} status="normal" />
        <MetricCell title={`${labelPrefix}已审计交易`} value={formatNumber(revenue.kpis.audited_trade_count)} status="normal" />
        <MetricCell
          title={`${labelPrefix}收益`}
          value={formatCurrency(revenue.kpis.today_pnl_usd)}
          status={revenue.kpis.today_pnl_usd !== null && revenue.kpis.today_pnl_usd >= 0 ? "normal" : "failed"}
        />
        <MetricCell
          title={`${labelPrefix}收益率`}
          value={formatSignedPercent(revenue.kpis.today_return_pct)}
          status={revenue.kpis.today_return_pct !== null && revenue.kpis.today_return_pct >= 0 ? "normal" : "failed"}
        />
        <MetricCell
          title={`${labelPrefix}胜率`}
          value={revenue.kpis.win_rate === null ? "暂无数据" : `${(revenue.kpis.win_rate * 100).toFixed(0)}%`}
          status="normal"
        />
        <MetricCell title="审计状态" value={auditStatusLabel(revenue.status)} status={revenue.status} />
      </MetricStrip>

      <Section
        title="收益趋势"
        description={`退出策略：${revenue.exit_rule}`}
        actions={
          <Button variant="outline" onClick={onRunRevenueAudit}>
            <PlayIcon data-icon="inline-start" />
            手动补跑
          </Button>
        }
      >
        {revenue.trend.length > 0 ? (
          <Card>
            <CardContent className="pt-6">
              <ChartContainer config={revenueChartConfig} className="h-72 w-full">
                <LineChart data={revenue.trend} accessibilityLayer>
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
        {revenue.trade_intents.length > 0 ? (
          <Card>
            <CardContent className="overflow-x-auto p-0 biome-scrollbar">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>时间</TableHead>
                    <TableHead>Ticker</TableHead>
                    <TableHead>触发消息</TableHead>
                    <TableHead>Policy</TableHead>
                    <TableHead>动作</TableHead>
                    <TableHead>买入价</TableHead>
                    <TableHead>卖出价</TableHead>
                    <TableHead>滑点</TableHead>
                    <TableHead>收益</TableHead>
                    <TableHead>状态</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {revenue.trade_intents.map((trade) => (
                    <TableRow
                      key={trade.record_id}
                      className="cursor-pointer"
                      onClick={() => onSelectTrade(trade)}
                    >
                      <TableCell>{formatDateTime(trade.time)}</TableCell>
                      <TableCell>{trade.ticker}</TableCell>
                      <TableCell>{trade.trigger_message_id || "暂无数据"}</TableCell>
                      <TableCell>{trade.trigger_policy_id || "暂无数据"}</TableCell>
                      <TableCell>{trade.action}</TableCell>
                      <TableCell>{formatCurrency(trade.estimated_entry_price)}</TableCell>
                      <TableCell>{formatCurrency(trade.exit_price)}</TableCell>
                      <TableCell>{trade.slippage_pct === null ? "暂无数据" : `${trade.slippage_pct}%`}</TableCell>
                      <TableCell>{formatCurrency(trade.pnl_usd)}</TableCell>
                      <TableCell>
                        <StatusBadge status={trade.status} label={trade.status} />
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        ) : (
          <EmptyState title="暂无交易意图" />
        )}
        {selectedTrade ? (
          <Card>
            <CardHeader>
              <CardTitle className="text-lg font-light">交易意图详情：{selectedTrade.record_id}</CardTitle>
              <CardDescription>{formatDateTime(selectedTrade.time)}</CardDescription>
            </CardHeader>
            <CardContent>
              <KeyValueList
                items={[
                  { label: "触发 Policy", value: selectedTrade.trigger_policy_id || "未知 policy" },
                  { label: "触发消息", value: selectedTrade.trigger_message_id || "未知消息" },
                  { label: "动作", value: selectedTrade.action },
                  { label: "纸面收益", value: formatCurrency(selectedTrade.pnl_usd) },
                ]}
              />
            </CardContent>
          </Card>
        ) : null}
      </Section>
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
