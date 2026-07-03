import { useCallback, useEffect, useMemo, useState } from "react"
import type { FormEvent } from "react"
import { useNavigate } from "react-router-dom"
import {
  ActivityIcon,
  AlertCircleIcon,
  BanIcon,
  BoxesIcon,
  CircleDollarSignIcon,
  CpuIcon,
  MessageSquareTextIcon,
  PlayIcon,
  RotateCwIcon,
  RouteIcon,
  Trash2Icon,
} from "lucide-react"
import { toast } from "sonner"

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
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
} from "@/components/ui/input-group"
import { Progress } from "@/components/ui/progress"
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { dashboardApi } from "@/lib/dashboard-api"
import type { MonitorMode, PageResult, StartupProgress, StartupProgressStep, TickerCard } from "@/lib/dashboard-types"
import { cn } from "@/lib/utils"
import {
  formatCurrency,
  formatDateTime,
  formatNumber,
  healthStatusLabel,
  monitorModeLabel,
  runStatusLabel,
} from "@/lib/format"
import { useDashboardQuery } from "@/hooks/use-dashboard-query"
import {
  EmptyState,
  ErrorState,
  FilterSelect,
  LoadMoreButton,
  LoadingGrid,
  MetricCell,
  MetricStrip,
  PageHeader,
  RefreshButton,
  Section,
  StatusBadge,
} from "@/components/dashboard/shared"

const tickerLimit = 8

const statusOptions = [
  { value: "all", label: "全部状态" },
  { value: "running", label: "运行中" },
  { value: "initializing", label: "初始化中" },
  { value: "paused", label: "已暂停" },
  { value: "degraded", label: "异常降级" },
  { value: "stopped", label: "已停止" },
]

const healthOptions = [
  { value: "all", label: "全部健康度" },
  { value: "normal", label: "正常" },
  { value: "degraded", label: "降级" },
  { value: "blocked", label: "阻塞" },
  { value: "unknown", label: "无数据" },
]

const monitorModeOptions: Array<{ value: MonitorMode; label: string; disabled?: boolean }> = [
  { value: "message_monitoring", label: "消息监测" },
  { value: "paper_trading", label: "模拟交易" },
  { value: "broker_trading", label: "真实 Broker", disabled: true },
]

type InitializeMode = "reuse" | "force"

export function OverviewPage() {
  const navigate = useNavigate()
  const [status, setStatus] = useState("all")
  const [health, setHealth] = useState("all")
  const [tickerInput, setTickerInput] = useState("")
  const [monitorMode, setMonitorMode] = useState<MonitorMode>("message_monitoring")
  const [initializeMode, setInitializeMode] = useState<InitializeMode>("reuse")
  const [pendingTicker, setPendingTicker] = useState<string | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<TickerCard | null>(null)
  const [tickerPage, setTickerPage] = useState<PageResult<TickerCard> | null>(null)
  const [loadingMore, setLoadingMore] = useState(false)

  const tickerParams = useMemo(
    () => ({
      limit: tickerLimit,
      status: status === "all" ? undefined : status,
      health: health === "all" ? undefined : health,
      sort: "ticker",
    }),
    [health, status]
  )

  const overviewLoader = useCallback(() => dashboardApi.overview(), [])
  const tickersLoader = useCallback(() => dashboardApi.tickers(tickerParams), [tickerParams])
  const overview = useDashboardQuery(overviewLoader, { intervalMs: 7000 })
  const tickers = useDashboardQuery(tickersLoader, { intervalMs: 7000 })

  useEffect(() => {
    if (tickers.data) {
      setTickerPage(tickers.data)
    }
  }, [tickers.data])

  const reloadAll = useCallback(async () => {
    await Promise.all([overview.reload(), tickers.reload()])
  }, [overview, tickers])

  const submitTicker = async (event: FormEvent) => {
    event.preventDefault()
    const ticker = tickerInput.trim().toUpperCase()
    if (!ticker) {
      toast.error("请输入 ticker。")
      return
    }
    setPendingTicker(ticker)
    try {
      await dashboardApi.startTicker(ticker, {
        forceInitialize: initializeMode === "force",
        monitorMode,
      })
      toast.success(`${ticker} 已启动${monitorModeLabel(monitorMode)}。`)
      setTickerInput("")
      await reloadAll()
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error))
    } finally {
      setPendingTicker(null)
    }
  }

  const operateTicker = async (
    ticker: string,
    operation: "pause" | "restart" | "delete"
  ) => {
    setPendingTicker(ticker)
    try {
      if (operation === "pause") {
        await dashboardApi.pauseTicker(ticker)
        toast.success(`${ticker} 已暂停。`)
      }
      if (operation === "restart") {
        await dashboardApi.restartTicker(ticker)
        toast.success(`${ticker} 已重启。`)
      }
      if (operation === "delete") {
        await dashboardApi.deleteTicker(ticker)
        toast.success(`${ticker} 已删除监控配置。`)
      }
      await reloadAll()
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error))
    } finally {
      setPendingTicker(null)
      setDeleteTarget(null)
    }
  }

  const changeMonitorMode = async (item: TickerCard, value: MonitorMode) => {
    if (value === "broker_trading") {
      toast.error("真实 Broker 本阶段暂未开放。")
      return
    }
    if ((item.monitor_mode ?? "message_monitoring") === value) {
      return
    }
    setPendingTicker(item.ticker)
    try {
      await dashboardApi.setMonitorMode(item.ticker, value)
      toast.success(`${item.ticker} 已切换为${monitorModeLabel(value)}。`)
      await reloadAll()
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error))
    } finally {
      setPendingTicker(null)
    }
  }

  const loadMore = async () => {
    if (!tickerPage?.page.next_cursor) {
      return
    }
    setLoadingMore(true)
    try {
      const next = await dashboardApi.tickers({
        ...tickerParams,
        cursor: tickerPage.page.next_cursor,
      })
      setTickerPage({
        items: [...tickerPage.items, ...next.items],
        page: next.page,
      })
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error))
    } finally {
      setLoadingMore(false)
    }
  }

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        title="Overview"
        lastUpdatedAt={overview.lastUpdatedAt}
        actions={
          <RefreshButton
            refreshing={overview.isRefreshing || tickers.isRefreshing}
            onClick={() => void reloadAll()}
          />
        }
      />

      {overview.error ? (
        <ErrorState message={overview.error} onRetry={() => void overview.reload()} />
      ) : null}

      {overview.isLoading && !overview.data ? (
        <LoadingGrid rows={8} />
      ) : overview.data ? (
        <MetricStrip>
          <MetricCell
            title="容器状态"
            value={healthStatusLabel(overview.data.system.container_status)}
            icon={<BoxesIcon />}
          />
          <MetricCell
            title="Message Bus"
            value={healthStatusLabel(overview.data.system.message_bus_status)}
            icon={<MessageSquareTextIcon />}
          />
          <MetricCell
            title="Dashboard API"
            value={healthStatusLabel(overview.data.system.dashboard_api_status)}
            icon={<CpuIcon />}
          />
          <MetricCell
            title="运行中 ticker"
            value={formatNumber(overview.data.kpis.running_ticker_count)}
            icon={<ActivityIcon />}
          />
          <MetricCell
            title="今日消息"
            value={formatNumber(overview.data.kpis.today_message_count)}
            icon={<MessageSquareTextIcon />}
          />
          <MetricCell
            title="今日 DTC"
            value={formatNumber(overview.data.kpis.today_dtc_count)}
            icon={<RouteIcon />}
          />
          <MetricCell
            title="今日 token 成本"
            value={formatCurrency(overview.data.kpis.today_token_cost_usd)}
            icon={<CircleDollarSignIcon />}
          />
          <MetricCell
            title="异常数量"
            value={formatNumber(overview.data.kpis.exception_count)}
            icon={<AlertCircleIcon />}
          />
        </MetricStrip>
      ) : null}

      <div className="grid gap-4 lg:grid-cols-[360px_minmax(0,1fr)]">
        <Section title="启动新标的监控">
          <Card>
            <form onSubmit={submitTicker}>
              <CardHeader>
                <CardTitle className="text-lg font-light">启动监测与自动交易</CardTitle>
              </CardHeader>
              <CardContent>
                <FieldGroup>
                  <Field>
                    <FieldLabel htmlFor="ticker-input">Ticker</FieldLabel>
                    <InputGroup>
                      <InputGroupInput
                        id="ticker-input"
                        value={tickerInput}
                        onChange={(event) => setTickerInput(event.target.value.toUpperCase())}
                        placeholder="例如 MU"
                      />
                      <InputGroupAddon align="inline-end">
                        <InputGroupButton
                          type="submit"
                          disabled={pendingTicker !== null}
                          variant="default"
                        >
                          <PlayIcon data-icon="inline-start" />
                          启动
                        </InputGroupButton>
                      </InputGroupAddon>
                    </InputGroup>
                  </Field>

                  <Field>
                    <FieldLabel>监测模式</FieldLabel>
                    <ToggleGroup
                      type="single"
                      value={monitorMode}
                      onValueChange={(value) => value && setMonitorMode(value as MonitorMode)}
                    >
                      <ToggleGroupItem value="message_monitoring">消息监测</ToggleGroupItem>
                      <ToggleGroupItem value="paper_trading">模拟交易</ToggleGroupItem>
                      <ToggleGroupItem value="broker_trading" disabled>
                        真实 Broker
                      </ToggleGroupItem>
                    </ToggleGroup>
                  </Field>

                  <Field>
                    <FieldLabel>文档初始化</FieldLabel>
                    <ToggleGroup
                      type="single"
                      value={initializeMode}
                      onValueChange={(value) => value && setInitializeMode(value as InitializeMode)}
                    >
                      <ToggleGroupItem value="reuse">复用当前文档</ToggleGroupItem>
                      <ToggleGroupItem value="force">强制初始化</ToggleGroupItem>
                    </ToggleGroup>
                  </Field>
                </FieldGroup>
              </CardContent>
            </form>
          </Card>
        </Section>

        <Section
          title="标的监控列表"
          actions={
            <>
              <FilterSelect
                value={status}
                placeholder="运行状态"
                options={statusOptions}
                onChange={setStatus}
              />
              <FilterSelect
                value={health}
                placeholder="健康状态"
                options={healthOptions}
                onChange={setHealth}
              />
            </>
          }
        >
          {tickers.error ? (
            <ErrorState message={tickers.error} onRetry={() => void tickers.reload()} />
          ) : null}
          {tickers.isLoading && !tickerPage ? (
            <LoadingGrid rows={4} />
          ) : tickerPage && tickerPage.items.length > 0 ? (
            <div className="workbench-section overflow-hidden">
              <div className="grid grid-cols-[0.68fr_150px_100px_1fr_1fr_0.66fr_0.72fr_180px] gap-3 border-b px-4 py-3 text-xs font-medium text-muted-foreground max-xl:hidden">
                <span>Ticker</span>
                <span>监测模式</span>
                <span>状态</span>
                <span>最近消息</span>
                <span>Worker 处理</span>
                <span>今日 DTC</span>
                <span>今日成本</span>
                <span>操作</span>
              </div>
              <div className="divide-y">
                {tickerPage.items.map((item) => (
                  <div
                    key={item.ticker}
                    role="button"
                    tabIndex={0}
                    className="grid w-full cursor-pointer grid-cols-1 gap-3 px-4 py-4 text-left transition-colors hover:bg-accent/45 xl:grid-cols-[0.68fr_150px_100px_1fr_1fr_0.66fr_0.72fr_180px]"
                    onClick={() => navigate(`/ticker/${item.ticker}/research`)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" || event.key === " ") {
                        navigate(`/ticker/${item.ticker}/research`)
                      }
                    }}
                  >
                    <div className="min-w-0">
                      <div className="text-2xl font-light">{item.ticker}</div>
                    </div>
                    <div
                      className="flex items-center justify-between gap-3 xl:block"
                      onClick={(event) => event.stopPropagation()}
                      onKeyDown={(event) => event.stopPropagation()}
                    >
                      <span className="text-xs text-muted-foreground xl:hidden">监测模式</span>
                      <Select
                        value={(item.monitor_mode ?? "message_monitoring") as MonitorMode}
                        disabled={pendingTicker === item.ticker}
                        onValueChange={(value) => void changeMonitorMode(item, value as MonitorMode)}
                      >
                        <SelectTrigger className="h-8 w-[148px]">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectGroup>
                            {monitorModeOptions.map((option) => (
                              <SelectItem
                                key={option.value}
                                value={option.value}
                                disabled={option.disabled}
                              >
                                {option.label}
                              </SelectItem>
                            ))}
                          </SelectGroup>
                        </SelectContent>
                      </Select>
                    </div>
                    <div className="flex items-center justify-between gap-3 xl:block">
                      <span className="text-xs text-muted-foreground xl:hidden">状态</span>
                      <div className="min-w-0">
                        <StatusBadge
                          status={item.status}
                          label={item.status_label || runStatusLabel(item.status)}
                        />
                      </div>
                    </div>
                    <TickerCell label="最近消息" value={formatDateTime(item.last_message_at)} />
                    <TickerCell label="Worker 处理" value={formatDateTime(item.last_worker_processed_at)} />
                    <TickerCell label="今日 DTC" value={formatNumber(item.today_dtc_count)} />
                    <TickerCell label="今日成本" value={formatCurrency(item.today_cost_usd)} />
                    <div className="flex flex-wrap items-center gap-2" onClick={(event) => event.stopPropagation()}>
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={pendingTicker === item.ticker}
                        onClick={() => void operateTicker(item.ticker, "pause")}
                      >
                        <BanIcon data-icon="inline-start" />
                        暂停
                      </Button>
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={pendingTicker === item.ticker}
                        onClick={() => void operateTicker(item.ticker, "restart")}
                      >
                        <RotateCwIcon data-icon="inline-start" />
                        重启
                      </Button>
                      <Button
                        variant="destructive"
                        size="icon-sm"
                        disabled={pendingTicker === item.ticker}
                        onClick={() => setDeleteTarget(item)}
                      >
                        <Trash2Icon />
                        <span className="sr-only">删除</span>
                      </Button>
                    </div>
                    {item.last_error ? (
                      <p className="rounded-[4px] bg-destructive/10 p-2 text-sm text-destructive xl:col-span-8">
                        {item.last_error}
                      </p>
                    ) : null}
                    {item.startup_progress ? (
                      <StartupProgressCard
                        progress={item.startup_progress}
                        disabled={pendingTicker === item.ticker}
                        onRetry={() => void operateTicker(item.ticker, "restart")}
                      />
                    ) : null}
                  </div>
                ))}
              </div>
              <LoadMoreButton
                hasMore={tickerPage.page.has_more}
                loading={loadingMore}
                onClick={() => void loadMore()}
              />
            </div>
          ) : (
            <EmptyState
              title="暂无 ticker"
              description="当前筛选条件下没有监控中的标的。"
            />
          )}
        </Section>
      </div>

      <AlertDialog open={!!deleteTarget} onOpenChange={(open) => !open && setDeleteTarget(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>确认删除监控配置</AlertDialogTitle>
            <AlertDialogDescription>
              将停止 {deleteTarget?.ticker} 的 mock 监控配置；历史记录默认保留。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                if (deleteTarget) {
                  void operateTicker(deleteTarget.ticker, "delete")
                }
              }}
            >
              确认删除
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

function StartupProgressCard({
  progress,
  disabled,
  onRetry,
}: {
  progress: StartupProgress
  disabled: boolean
  onRetry: () => void
}) {
  const blocked = progress.status === "blocked"
  return (
    <div
      className="rounded-[6px] border bg-background/80 p-3 xl:col-span-8"
      onClick={(event) => event.stopPropagation()}
      onKeyDown={(event) => event.stopPropagation()}
    >
      <div className="mb-3 flex items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-2">
          <div className="text-sm font-medium">启动进度</div>
          <Badge variant={blocked ? "destructive" : "secondary"}>
            {progress.status_label || (blocked ? "阻塞" : "启动中")}
          </Badge>
        </div>
        {blocked && progress.retryable ? (
          <Button
            variant="outline"
            size="icon-sm"
            disabled={disabled}
            onClick={onRetry}
            aria-label="重试启动"
          >
            <RotateCwIcon />
          </Button>
        ) : null}
      </div>
      <div className="grid gap-2 md:grid-cols-5">
        {progress.steps.map((step) => (
          <StartupStep key={step.step_id} step={step} />
        ))}
      </div>
      {progress.message ? (
        <p className="mt-3 rounded-[4px] bg-muted p-2 text-xs text-muted-foreground">
          {progress.message}
        </p>
      ) : null}
    </div>
  )
}

function StartupStep({ step }: { step: StartupProgressStep }) {
  const blocked = step.status === "blocked"
  const running = step.status === "running"
  const completed = step.status === "completed"
  const value = Math.min(100, Math.max(0, Number(step.progress) || 0))
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <div
        className={cn(
          "truncate text-xs",
          completed || running ? "text-foreground" : "text-muted-foreground",
          blocked ? "text-destructive" : null
        )}
      >
        {step.label}
      </div>
      <Progress value={value} className="h-1.5" />
    </div>
  )
}

function TickerCell({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-3 xl:block">
      <span className="text-xs text-muted-foreground xl:hidden">{label}</span>
      <span className="text-sm">{value}</span>
    </div>
  )
}
