import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useParams } from "react-router-dom"
import {
  Clock3Icon,
  Maximize2Icon,
  RotateCcwIcon,
} from "lucide-react"
import { toast } from "sonner"

import {
  Button,
} from "@/components/ui/button"
import {
  Card,
  CardContent,
} from "@/components/ui/card"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { dashboardApi } from "@/lib/dashboard-api"
import type {
  DashboardEvent,
  PageResult,
  RuntimeEdge,
  RuntimeExecution,
  RuntimeGraph,
  RuntimeNode,
  RuntimeNodeDetail,
} from "@/lib/dashboard-types"
import { formatDateTime, formatLatency, formatNumber, routeLabel, sourceTypeLabel } from "@/lib/format"
import { useDashboardEvents } from "@/hooks/use-dashboard-events"
import { useDashboardQuery } from "@/hooks/use-dashboard-query"
import {
  EmptyState,
  ErrorState,
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

const executionLimit = 10
const stageWidth = 1192
const stageHeight = 940
const laneWidth = 220
const laneGap = 80
const lanePaddingX = 36
const laneHeaderHeight = 86
const nodeWidth = 176
const nodeHeight = 118
const nodeGap = 28

const runtimeLanes = [
  {
    id: "entry",
    title: "入口 / 任务池",
    subtitle: "Message Bus",
    nodeIds: ["message_bus"],
  },
  {
    id: "first_pass",
    title: "一轮判定",
    subtitle: "W1 + W2 联合判定",
    nodeIds: ["w1", "w2", "route_engine"],
  },
  {
    id: "second_review",
    title: "二轮研判",
    subtitle: "O3 专家复核",
    nodeIds: ["o3"],
  },
  {
    id: "result",
    title: "结果沉淀",
    subtitle: "持久化落点",
    nodeIds: [
      "trading_records",
      "exception_queue",
      "objection",
      "known_event_patch",
      "archive",
      "ingest_queue",
    ],
  },
]

const canonicalRuntimeNodeIds = new Set(runtimeLanes.flatMap((lane) => lane.nodeIds))

const runtimeNodeDefaults: Record<string, Pick<RuntimeNode, "label" | "status">> = {
  message_bus: { label: "Message Bus / 任务池", status: "normal" },
  w1: { label: "W1 新旧判定", status: "normal" },
  w2: { label: "W2 Policy 判定", status: "normal" },
  route_engine: { label: "联合路由", status: "normal" },
  o3: { label: "O3 值班专家", status: "normal" },
  trading_records: { label: "交易记录", status: "normal" },
  exception_queue: { label: "异常队列", status: "normal" },
  objection: { label: "发起 Objection", status: "normal" },
  known_event_patch: { label: "增补 Known Event", status: "normal" },
  archive: { label: "归档池 Archive", status: "normal" },
  ingest_queue: { label: "待入库队列 Ingest Queue", status: "normal" },
}

const nodeYById: Record<string, number> = {
  message_bus: 250,
  w1: 128,
  w2: 310,
  route_engine: 510,
  o3: 280,
  trading_records: 116,
  exception_queue: 264,
  objection: 412,
  known_event_patch: 560,
  archive: 708,
  ingest_queue: 856,
}

export function RuntimePage() {
  const ticker = useParams().ticker?.toUpperCase() ?? "MU"
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null)
  const [nodeDetail, setNodeDetail] = useState<RuntimeNodeDetail | null>(null)
  const [nodeError, setNodeError] = useState<string | null>(null)
  const [nodeLoading, setNodeLoading] = useState(false)
  const [executionPage, setExecutionPage] = useState<PageResult<RuntimeExecution> | null>(null)
  const [loadingMore, setLoadingMore] = useState(false)

  const overviewLoader = useCallback(() => dashboardApi.runtimeOverview(ticker), [ticker])
  const graphLoader = useCallback(() => dashboardApi.runtimeGraph(ticker), [ticker])
  const executionsLoader = useCallback(
    () => dashboardApi.runtimeExecutions(ticker, { limit: executionLimit }),
    [ticker]
  )

  const overview = useDashboardQuery(overviewLoader, { intervalMs: 8000 })
  const graph = useDashboardQuery(graphLoader, { intervalMs: 8000 })
  const executions = useDashboardQuery(executionsLoader, { intervalMs: 15000 })
  const normalizedGraph = useMemo(
    () => (graph.data ? normalizeRuntimeGraph(graph.data) : null),
    [graph.data]
  )
  const graphNodeLookup = useMemo(() => {
    return new Map((normalizedGraph?.nodes ?? []).map((node) => [node.node_id, node]))
  }, [normalizedGraph])
  const reloadOverview = overview.reload
  const reloadGraph = graph.reload
  const reloadExecutions = executions.reload

  useEffect(() => {
    setSelectedNodeId(null)
    setNodeDetail(null)
    setNodeError(null)
  }, [ticker])

  useEffect(() => {
    if (executions.data) {
      setExecutionPage(executions.data)
    }
  }, [executions.data])

  const loadNode = useCallback(
    async (nodeId: string, options: { silent?: boolean } = {}) => {
      const showLoading = !options.silent
      setSelectedNodeId(nodeId)
      if (showLoading) {
        setNodeLoading(true)
      }
      setNodeError(null)
      try {
        const detail = await dashboardApi.runtimeNode(ticker, nodeId, { limit: 10 })
        setNodeDetail(detail)
      } catch (error) {
        const fallbackNode = graphNodeLookup.get(nodeId)
        if (fallbackNode) {
          setNodeDetail({
            node: {
              node_id: fallbackNode.node_id,
              label: fallbackNode.label,
              status: fallbackNode.status,
              last_processed_at: null,
              today_count: fallbackNode.in_count,
              today_failed_count: fallbackNode.failed_count,
              avg_latency_ms: null,
              last_error: null,
            },
            recent_records: [],
          })
          setNodeError(null)
        } else {
          setNodeError(error instanceof Error ? error.message : String(error))
        }
      } finally {
        if (showLoading) {
          setNodeLoading(false)
        }
      }
    },
    [graphNodeLookup, ticker]
  )
  const selectedDetailNodeId = nodeDetail?.node.node_id

  const handleEvent = useCallback(
    (event: DashboardEvent) => {
      if (
        event.event_type === "runtime.execution.updated" ||
        event.event_type === "runtime.execution.failed"
      ) {
        void reloadOverview()
        void reloadGraph()
        void reloadExecutions()
        if (selectedNodeId) {
          void loadNode(selectedNodeId, { silent: selectedDetailNodeId === selectedNodeId })
        }
      }
    },
    [loadNode, reloadExecutions, reloadGraph, reloadOverview, selectedDetailNodeId, selectedNodeId]
  )

  const events = useDashboardEvents({
    ticker,
    eventTypes: ["runtime.execution.updated", "runtime.execution.failed"],
    onEvent: handleEvent,
  })

  const loadMoreExecutions = async () => {
    if (!executionPage?.page.next_cursor) {
      return
    }
    setLoadingMore(true)
    try {
      const next = await dashboardApi.runtimeExecutions(ticker, {
        limit: executionLimit,
        cursor: executionPage.page.next_cursor,
      })
      setExecutionPage({
        items: [...executionPage.items, ...next.items],
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
        title={`${ticker} 运行状态`}
        eyebrow="Persistent Runtime"
        description="展示 Message Bus 到 W1 / W2 / O3 / 交易记录等节点的持久化执行链路。"
        lastUpdatedAt={graph.lastUpdatedAt}
        actions={
          <>
            <StatusBadge status={events.state} label={`SSE：${events.state}`} />
            <RefreshButton
              refreshing={overview.isRefreshing || graph.isRefreshing || executions.isRefreshing}
              onClick={() => {
                void overview.reload()
                void graph.reload()
                void executions.reload()
              }}
            />
          </>
        }
      />

      {events.error ? <ErrorState title="SSE 连接异常" message={events.error} /> : null}
      {[overview.error, graph.error, executions.error]
        .filter(Boolean)
        .map((message) => (
          <ErrorState key={message} message={message ?? ""} />
        ))}

      {overview.data ? (
        <MetricStrip>
          <MetricCell
            title="当前队列消息"
            value={formatNumber(overview.data.queue_message_count)}
            status={overview.data.queue_message_count > 0 ? "degraded" : "normal"}
          />
          <MetricCell
            title="W1 今日处理"
            value={formatNumber(overview.data.w1_today_count)}
            description={`平均 ${formatLatency(overview.data.w1_avg_latency_ms)}`}
            status="normal"
          />
          <MetricCell
            title="W2 今日处理"
            value={formatNumber(overview.data.w2_today_count)}
            description={`平均 ${formatLatency(overview.data.w2_avg_latency_ms)}`}
            status="normal"
          />
          <MetricCell
            title="O3 今日处理"
            value={formatNumber(overview.data.o3_today_count)}
            description={`平均 ${formatLatency(overview.data.o3_avg_latency_ms)}`}
            status="normal"
          />
          <MetricCell title="DTC 今日数量" value={formatNumber(overview.data.dtc_today_count)} status="normal" />
          <MetricCell title="EBA 今日数量" value={formatNumber(overview.data.eba_today_count)} status="normal" />
          <MetricCell
            title="失败任务"
            value={formatNumber(overview.data.failed_task_count)}
            status={overview.data.failed_task_count > 0 ? "failed" : "normal"}
          />
          <MetricCell
            title="平均处理延迟"
            value={formatLatency(overview.data.avg_processing_latency_ms)}
            status="normal"
          />
        </MetricStrip>
      ) : overview.isLoading ? (
        <LoadingGrid rows={8} />
      ) : null}

      <Section
        title="运行链路图"
        description="点击节点查看关键输入、输出、状态和最近错误；选中节点后高亮对应上游消息流向。"
      >
        {graph.isLoading && !graph.data ? (
          <LoadingGrid rows={4} />
        ) : normalizedGraph && normalizedGraph.nodes.length > 0 ? (
          <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
            <RuntimeFlowMap
              graph={normalizedGraph}
              selectedNodeId={selectedNodeId}
              onSelectNode={(nodeId) => void loadNode(nodeId)}
            />
            <NodeDetailPanel
              detail={nodeDetail}
              selectedNodeId={selectedNodeId}
              loading={nodeLoading}
              error={nodeError}
            />
          </div>
        ) : (
          <EmptyState title="暂无运行链路" />
        )}
      </Section>

      <Section title="最近处理记录" description="按执行时间倒序展示 runtime execution 摘要。">
        {executions.isLoading && !executionPage ? (
          <LoadingGrid rows={3} />
        ) : executionPage && executionPage.items.length > 0 ? (
          <Card>
            <CardContent className="overflow-x-auto p-0 biome-scrollbar">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-[128px]">时间</TableHead>
                    <TableHead className="min-w-[320px]">消息标题</TableHead>
                    <TableHead className="w-[96px]">来源</TableHead>
                    <TableHead className="w-[132px]">最终路由</TableHead>
                    <TableHead className="w-[104px]">状态</TableHead>
                    <TableHead className="w-[140px]">异常</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {executionPage.items.map((item) => (
                    <TableRow key={item.execution_id}>
                      <TableCell>{formatDateTime(item.created_at)}</TableCell>
                      <TableCell>
                        <div className="font-medium">
                          {item.message_title || item.source_message_id || item.execution_id}
                        </div>
                        <div className="mt-1 font-mono text-xs text-muted-foreground">
                          {item.execution_id}
                        </div>
                      </TableCell>
                      <TableCell>{sourceTypeLabel(item.source_type)}</TableCell>
                      <TableCell>{routeLabel(item.final_route)}</TableCell>
                      <TableCell>
                        <StatusBadge status={item.status} label={item.status} />
                      </TableCell>
                      <TableCell>{item.exception_types.join(", ") || "无"}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
            <LoadMoreButton
              hasMore={executionPage.page.has_more}
              loading={loadingMore}
              onClick={() => void loadMoreExecutions()}
            />
          </Card>
        ) : (
          <EmptyState title="暂无处理记录" />
        )}
      </Section>
    </div>
  )
}

function RuntimeFlowMap({
  graph,
  selectedNodeId,
  onSelectNode,
}: {
  graph: RuntimeGraph
  selectedNodeId: string | null
  onSelectNode: (nodeId: string) => void
}) {
  const layout = useMemo(() => buildLayout(graph.nodes), [graph.nodes])
  const highlightedEdgeIds = useMemo(
    () => (selectedNodeId ? collectUpstreamEdgeIds(graph.edges, selectedNodeId) : new Set<string>()),
    [graph.edges, selectedNodeId]
  )
  const hasHighlightedEdges = highlightedEdgeIds.size > 0
  const dragRef = useRef<{ x: number; y: number; viewX: number; viewY: number } | null>(null)
  const [view, setView] = useState({ x: -12, y: -10, scale: 0.78 })

  const resetView = () => setView({ x: 0, y: 0, scale: 1 })
  const fitView = () => setView({ x: -12, y: -10, scale: 0.78 })

  return (
    <div className="runtime-map">
      <div className="runtime-map-controls">
        <Button variant="outline" size="sm" className="runtime-map-control" onClick={fitView}>
          <Maximize2Icon data-icon="inline-start" />
          Fit View
        </Button>
        <Button variant="outline" size="sm" className="runtime-map-control" onClick={resetView}>
          <RotateCcwIcon data-icon="inline-start" />
          Reset View
        </Button>
      </div>
      <div
        className="runtime-pan-surface"
        onWheel={(event) => {
          event.preventDefault()
          const delta = event.deltaY > 0 ? -0.08 : 0.08
          setView((current) => ({
            ...current,
            scale: clamp(current.scale + delta, 0.62, 1.36),
          }))
        }}
        onPointerDown={(event) => {
          if ((event.target as HTMLElement).closest(".runtime-node, .runtime-map-control")) {
            return
          }
          event.currentTarget.setPointerCapture(event.pointerId)
          dragRef.current = {
            x: event.clientX,
            y: event.clientY,
            viewX: view.x,
            viewY: view.y,
          }
        }}
        onPointerMove={(event) => {
          const drag = dragRef.current
          if (!drag) {
            return
          }
          setView((current) => ({
            ...current,
            x: drag.viewX + event.clientX - drag.x,
            y: drag.viewY + event.clientY - drag.y,
          }))
        }}
        onPointerUp={(event) => {
          if (!dragRef.current) {
            return
          }
          dragRef.current = null
          event.currentTarget.releasePointerCapture(event.pointerId)
        }}
        onPointerCancel={() => {
          dragRef.current = null
        }}
      >
        <div
          className="runtime-stage"
          style={{
            width: stageWidth,
            height: stageHeight,
            transform: `translate(${view.x}px, ${view.y}px) scale(${view.scale})`,
          }}
        >
          {runtimeLanes.map((lane, index) => (
            <StageLabel key={lane.id} lane={lane} index={index} />
          ))}

        <svg
          className="pointer-events-none absolute inset-0"
          width={stageWidth}
          height={stageHeight}
          viewBox={`0 0 ${stageWidth} ${stageHeight}`}
          aria-hidden="true"
        >
          <defs>
            <marker
              id="runtime-arrow"
              viewBox="0 0 10 10"
              refX="8"
              refY="5"
              markerWidth="6"
              markerHeight="6"
              orient="auto-start-reverse"
            >
              <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--primary)" opacity="0.56" />
            </marker>
            <marker
              id="runtime-arrow-highlight"
              viewBox="0 0 10 10"
              refX="8"
              refY="5"
              markerWidth="6"
              markerHeight="6"
              orient="auto-start-reverse"
            >
              <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--primary)" opacity="0.9" />
            </marker>
          </defs>
          {graph.edges.map((edge, index) => {
            const highlighted = highlightedEdgeIds.has(edge.edge_id)
            return (
              <RuntimeEdgePath
                key={edge.edge_id}
                edge={edge}
                index={index}
                layout={layout}
                highlighted={highlighted}
                dimmed={hasHighlightedEdges && !highlighted}
              />
            )
          })}
        </svg>

        {graph.nodes.map((node) => {
          const point = layout[node.node_id]
          return (
            <RuntimeNodeCard
              key={node.node_id}
              node={node}
              selected={selectedNodeId === node.node_id}
              x={point.x}
              y={point.y}
              onClick={() => onSelectNode(node.node_id)}
            />
          )
        })}
        </div>
      </div>
    </div>
  )
}

function StageLabel({
  lane,
  index,
}: {
  lane: (typeof runtimeLanes)[number]
  index: number
}) {
  const left = lanePaddingX + index * (laneWidth + laneGap)
  return (
    <div className="runtime-lane" style={{ left, width: laneWidth }}>
      <div className="runtime-lane-title">{lane.title}</div>
      <div className="runtime-lane-subtitle">{lane.subtitle}</div>
    </div>
  )
}

function RuntimeEdgePath({
  edge,
  index,
  layout,
  highlighted,
  dimmed,
}: {
  edge: RuntimeEdge
  index: number
  layout: Record<string, { x: number; y: number }>
  highlighted: boolean
  dimmed: boolean
}) {
  const from = layout[edge.from]
  const to = layout[edge.to]
  if (!from || !to || edge.count <= 0) {
    return null
  }
  const edgeRoute = runtimeEdgeRoute(edge, from, to, index)
  const strokeWidth = highlighted ? 2.6 : 1.7
  const haloWidth = highlighted ? 7 : 4.8
  const haloOpacity = dimmed ? "0.03" : highlighted ? "0.2" : "0.1"
  const strokeOpacity = dimmed ? "0.14" : highlighted ? "0.84" : "0.48"
  const label = formatNumber(edge.count)
  const pillWidth = Math.max(28, label.length * 7 + 18)

  return (
    <g
      data-dimmed={dimmed ? "true" : "false"}
      data-edge-id={edge.edge_id}
      data-highlighted={highlighted ? "true" : "false"}
    >
      <path
        d={edgeRoute.d}
        fill="none"
        stroke="var(--primary)"
        strokeOpacity={haloOpacity}
        strokeWidth={haloWidth}
      />
      <path
        d={edgeRoute.d}
        fill="none"
        stroke="var(--primary)"
        strokeOpacity={strokeOpacity}
        strokeWidth={strokeWidth}
        markerEnd={highlighted ? "url(#runtime-arrow-highlight)" : "url(#runtime-arrow)"}
      />
      <g
        opacity={dimmed ? "0.42" : "1"}
        transform={`translate(${edgeRoute.labelX - pillWidth / 2}, ${edgeRoute.labelY - 9})`}
      >
        <rect
          width={pillWidth}
          height="18"
          rx="9"
          fill="rgb(255 255 255 / 0.92)"
          stroke="rgb(161 64 102 / 0.26)"
        />
        <text
          x={pillWidth / 2}
          y="12.5"
          fontSize="10.5"
          fill="var(--foreground)"
          fontWeight="650"
          textAnchor="middle"
        >
          {label}
        </text>
      </g>
    </g>
  )
}

function runtimeEdgeRoute(
  edge: RuntimeEdge,
  from: { x: number; y: number },
  to: { x: number; y: number },
  index: number
) {
  const fromLane = laneIndexForNode(edge.from)
  const toLane = laneIndexForNode(edge.to)
  const fanOffset = ((index % 5) - 2) * 7
  if (fromLane === toLane) {
    const sideX = from.x + nodeWidth + 16
    const startY = from.y + nodeHeight / 2 + fanOffset
    const endY = to.y + nodeHeight / 2 - fanOffset
    return {
      d: `M ${from.x + nodeWidth} ${startY} L ${sideX} ${startY} C ${sideX} ${startY}, ${sideX} ${endY}, ${sideX} ${endY} L ${to.x + nodeWidth} ${endY}`,
      labelX: sideX,
      labelY: (startY + endY) / 2,
    }
  }

  const startX = from.x + nodeWidth
  const startY = from.y + nodeHeight / 2 + fanOffset
  const endX = to.x
  const endY = to.y + nodeHeight / 2 - fanOffset
  const laneGapX =
    lanePaddingX +
    (fromLane + 1) * laneWidth +
    fromLane * laneGap +
    laneGap / 2
  return {
    d: `M ${startX} ${startY} C ${laneGapX} ${startY}, ${laneGapX} ${startY}, ${laneGapX} ${startY} L ${laneGapX} ${endY} C ${laneGapX} ${endY}, ${laneGapX} ${endY}, ${endX} ${endY}`,
    labelX: laneGapX,
    labelY: startY + (endY - startY) * 0.52,
  }
}

function RuntimeNodeCard({
  node,
  selected,
  x,
  y,
  onClick,
}: {
  node: RuntimeNode
  selected?: boolean
  x: number
  y: number
  onClick: () => void
}) {
  return (
    <button
      type="button"
      className="runtime-node p-3 text-left"
      data-node-id={node.node_id}
      data-selected={selected}
      style={{ left: x, top: y }}
      onClick={onClick}
    >
      <div className="mb-2 flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <div className="text-sm font-medium leading-5">{node.label}</div>
        </div>
      </div>
      <div className="runtime-node-counts">
        <NodeCount label="入" value={node.in_count} />
        <NodeCount label="出" value={node.out_count} />
        <NodeCount label="错" value={node.failed_count} />
      </div>
      <div className="mt-2">
        <StatusBadge status={node.status} />
      </div>
    </button>
  )
}

function NodeCount({ label, value }: { label: string; value: number }) {
  return (
    <span className="runtime-node-count">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-medium">{formatNumber(value)}</span>
    </span>
  )
}

function NodeDetailPanel({
  detail,
  selectedNodeId,
  loading,
  error,
}: {
  detail: RuntimeNodeDetail | null
  selectedNodeId: string | null
  loading: boolean
  error: string | null
}) {
  return (
    <aside className="glass-slab p-4 xl:sticky xl:top-24">
      <div className="mb-4 flex items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="text-xs font-medium uppercase tracking-[0.2em] text-primary">
            Node Detail
          </div>
          <div className="mt-2 flex items-center gap-2">
            <h3 className="truncate text-lg font-light">
              {detail?.node.label ?? selectedNodeId ?? "选择节点"}
            </h3>
            {detail ? <StatusBadge status={detail.node.status} /> : null}
          </div>
        </div>
      </div>
      {loading ? (
        <LoadingGrid rows={2} />
      ) : error ? (
        <ErrorState message={error} />
      ) : detail ? (
        <div className="runtime-node-detail-scroll biome-scrollbar flex flex-col gap-4">
          <KeyValueList
            items={[
              { label: "最近处理", value: formatDateTime(detail.node.last_processed_at) },
              { label: "今日处理", value: formatNumber(detail.node.today_count) },
              { label: "今日失败", value: formatNumber(detail.node.today_failed_count) },
              { label: "平均延迟", value: formatLatency(detail.node.avg_latency_ms) },
            ]}
          />
          {detail.node.last_error ? (
            <p className="rounded-[4px] bg-destructive/10 p-2 text-sm text-destructive">
              {detail.node.last_error}
            </p>
          ) : null}
          <div className="flex flex-col gap-3">
            <h4 className="flex items-center gap-2 text-sm font-medium">
              <Clock3Icon data-icon="inline-start" />
              最近节点记录
            </h4>
            {detail.recent_records.length > 0 ? (
              detail.recent_records.map((record) => (
                <div key={record.execution_id} className="rounded-[4px] border bg-white/60 p-3">
                  <div className="font-mono text-xs">{record.execution_id}</div>
                  <div className="mt-1 text-xs text-muted-foreground">
                    {formatDateTime(record.created_at)} · {formatLatency(record.duration_ms)}
                  </div>
                  <div className="mt-3 grid gap-3 text-sm">
                    <div>
                      <div className="font-medium">输入</div>
                      <p className="mt-1 leading-6 text-muted-foreground">
                        {record.input_summary || "暂无数据"}
                      </p>
                    </div>
                    <div>
                      <div className="font-medium">输出</div>
                      <p className="mt-1 leading-6 text-muted-foreground">
                        {record.output_summary || "暂无数据"}
                      </p>
                    </div>
                  </div>
                </div>
              ))
            ) : (
              <EmptyState title="暂无节点记录" />
            )}
          </div>
        </div>
      ) : (
        <EmptyState title="选择节点" description="点击链路图中的任一节点查看详情。" />
      )}
    </aside>
  )
}

function buildLayout(nodes: RuntimeNode[]) {
  const layout: Record<string, { x: number; y: number }> = {}
  const used = new Set<string>()
  for (const [laneIndex, lane] of runtimeLanes.entries()) {
    const laneNodes = lane.nodeIds
      .map((nodeId) => nodes.find((node) => node.node_id === nodeId))
      .filter((node): node is RuntimeNode => Boolean(node))
    laneNodes.forEach((node, nodeIndex) => {
      used.add(node.node_id)
      layout[node.node_id] = {
        x: lanePaddingX + laneIndex * (laneWidth + laneGap) + (laneWidth - nodeWidth) / 2,
        y: nodeYById[node.node_id] ?? laneHeaderHeight + 28 + nodeIndex * (nodeHeight + nodeGap),
      }
    })
  }
  const resultLaneIndex = runtimeLanes.length - 1
  const resultLaneCount = runtimeLanes[resultLaneIndex].nodeIds.length
  nodes
    .filter((node) => !used.has(node.node_id))
    .forEach((node, index) => {
      layout[node.node_id] = {
        x:
          lanePaddingX +
          resultLaneIndex * (laneWidth + laneGap) +
          (laneWidth - nodeWidth) / 2,
        y: laneHeaderHeight + 28 + (resultLaneCount + index) * (nodeHeight + nodeGap),
      }
    })
  return layout
}

function normalizeRuntimeGraph(graph: RuntimeGraph): RuntimeGraph {
  const sourceNodes = new Map(graph.nodes.map((node) => [node.node_id, node]))
  const nodes = new Map<string, RuntimeNode>()

  for (const node of graph.nodes) {
    if (canonicalRuntimeNodeIds.has(node.node_id)) {
      nodes.set(node.node_id, { ...node, label: runtimeNodeDefaults[node.node_id]?.label ?? node.label })
    }
  }

  const legacyDelegate = sourceNodes.get("o1_a2")
  const legacyIgnored = sourceNodes.get("ignored")
  const hasRuntimeData = graph.nodes.length > 0

  if (hasRuntimeData) {
    for (const nodeId of canonicalRuntimeNodeIds) {
      ensureRuntimeNode(nodes, nodeId)
    }
  }

  if (legacyDelegate) {
    mergeRuntimeNode(nodes, "objection", {
      in_count: Math.max(1, legacyDelegate.in_count - Math.max(0, legacyDelegate.out_count)),
      status: legacyDelegate.status,
    })
    mergeRuntimeNode(nodes, "known_event_patch", {
      in_count: Math.max(0, legacyDelegate.out_count),
      status: legacyDelegate.status,
    })
  }

  if (legacyIgnored) {
    mergeRuntimeNode(nodes, "archive", {
      in_count: legacyIgnored.in_count,
      status: legacyIgnored.status,
    })
  }

  const messageBus = nodes.get("message_bus")
  const w1 = nodes.get("w1")
  const w2 = nodes.get("w2")
  const routeEngine = nodes.get("route_engine")
  const o3 = nodes.get("o3")
  if (routeEngine && (w1 || w2 || o3)) {
    const jointOut = Math.max(w1?.out_count ?? 0, w2?.out_count ?? 0, o3?.in_count ?? 0)
    routeEngine.in_count = Math.max(routeEngine.in_count, jointOut)
    routeEngine.out_count = Math.max(routeEngine.out_count, jointOut)
  }
  if (messageBus) {
    if (w1) {
      w1.in_count = Math.max(w1.in_count, messageBus.out_count)
    }
    if (w2) {
      w2.in_count = Math.max(w2.in_count, messageBus.out_count)
    }
  }

  const edges = new Map<string, RuntimeEdge>()
  for (const edge of graph.edges) {
    if (isAllowedRuntimeEdge(edge) && nodes.has(edge.from) && nodes.has(edge.to) && edge.count > 0) {
      edges.set(edge.edge_id, { ...edge })
    }
  }

  addRuntimeEdge(edges, nodes, "message_bus_to_w1", "message_bus", "w1", "W1 novelty 输入", nodes.get("w1")?.in_count)
  addRuntimeEdge(edges, nodes, "message_bus_to_w2", "message_bus", "w2", "W2 policy 输入", nodes.get("w2")?.in_count)
  addRuntimeEdge(edges, nodes, "w1_to_route_engine", "w1", "route_engine", "novelty label", nodes.get("w1")?.out_count)
  addRuntimeEdge(edges, nodes, "w2_to_route_engine", "w2", "route_engine", "policy type", nodes.get("w2")?.out_count)
  addRuntimeEdge(edges, nodes, "route_engine_to_o3", "route_engine", "o3", "专家复核", nodes.get("o3")?.in_count)
  addRuntimeEdge(edges, nodes, "route_engine_to_archive", "route_engine", "archive", "归档", nodes.get("archive")?.in_count)
  addRuntimeEdge(edges, nodes, "route_engine_to_ingest_queue", "route_engine", "ingest_queue", "待入库", nodes.get("ingest_queue")?.in_count)
  addRuntimeEdge(edges, nodes, "o3_to_trading", "o3", "trading_records", "交易记录", Math.min(nodes.get("o3")?.out_count ?? 0, nodes.get("trading_records")?.in_count ?? 0))
  addRuntimeEdge(edges, nodes, "o3_to_exception_queue", "o3", "exception_queue", "异常", nodes.get("exception_queue")?.in_count)
  addRuntimeEdge(edges, nodes, "o3_to_objection", "o3", "objection", "Objection", nodes.get("objection")?.in_count)
  addRuntimeEdge(edges, nodes, "o3_to_known_event_patch", "o3", "known_event_patch", "Known Event", nodes.get("known_event_patch")?.in_count)

  return {
    nodes: Array.from(nodes.values()).sort((a, b) => runtimeNodeOrder(a.node_id) - runtimeNodeOrder(b.node_id)),
    edges: Array.from(edges.values()),
  }
}

function ensureRuntimeNode(nodes: Map<string, RuntimeNode>, nodeId: string) {
  if (nodes.has(nodeId)) {
    return
  }
  const defaults = runtimeNodeDefaults[nodeId]
  if (!defaults) {
    return
  }
  nodes.set(nodeId, {
    node_id: nodeId,
    label: defaults.label,
    status: defaults.status,
    in_count: 0,
    out_count: 0,
    failed_count: 0,
  })
}

function mergeRuntimeNode(
  nodes: Map<string, RuntimeNode>,
  nodeId: string,
  patch: Partial<Pick<RuntimeNode, "in_count" | "out_count" | "failed_count" | "status">>
) {
  ensureRuntimeNode(nodes, nodeId)
  const node = nodes.get(nodeId)
  if (!node) {
    return
  }
  node.in_count = Math.max(node.in_count, patch.in_count ?? 0)
  node.out_count = Math.max(node.out_count, patch.out_count ?? 0)
  node.failed_count = Math.max(node.failed_count, patch.failed_count ?? 0)
  if (patch.status && patch.status !== "unknown") {
    node.status = patch.status
  }
}

function isAllowedRuntimeEdge(edge: RuntimeEdge) {
  return new Set([
    "message_bus_to_w1",
    "message_bus_to_w2",
    "w1_to_route_engine",
    "w2_to_route_engine",
    "route_engine_to_trading",
    "route_engine_to_o3",
    "route_engine_to_archive",
    "route_engine_to_ingest_queue",
    "o3_to_trading",
    "o3_to_exception_queue",
    "o3_to_objection",
    "o3_to_known_event_patch",
    "o3_to_ingest_queue",
  ]).has(edge.edge_id)
}

function addRuntimeEdge(
  edges: Map<string, RuntimeEdge>,
  nodes: Map<string, RuntimeNode>,
  edgeId: string,
  from: string,
  to: string,
  label: string,
  count: number | undefined
) {
  if (edges.has(edgeId) || !nodes.has(from) || !nodes.has(to) || !count || count <= 0) {
    return
  }
  edges.set(edgeId, { edge_id: edgeId, from, to, label, count })
}

function runtimeNodeOrder(nodeId: string) {
  for (const [laneIndex, lane] of runtimeLanes.entries()) {
    const nodeIndex = lane.nodeIds.indexOf(nodeId)
    if (nodeIndex >= 0) {
      return laneIndex * 100 + nodeIndex
    }
  }
  return 999
}

function laneIndexForNode(nodeId: string) {
  const laneIndex = runtimeLanes.findIndex((lane) => lane.nodeIds.includes(nodeId))
  return laneIndex >= 0 ? laneIndex : runtimeLanes.length - 1
}

function collectUpstreamEdgeIds(edges: RuntimeEdge[], targetNodeId: string) {
  const incoming = new Map<string, RuntimeEdge[]>()
  for (const edge of edges) {
    if (edge.count <= 0) {
      continue
    }
    const list = incoming.get(edge.to) ?? []
    list.push(edge)
    incoming.set(edge.to, list)
  }

  const selected = new Set<string>()
  const seenNodes = new Set<string>([targetNodeId])
  const stack = [targetNodeId]
  while (stack.length > 0) {
    const nodeId = stack.pop()
    if (!nodeId) {
      continue
    }
    for (const edge of incoming.get(nodeId) ?? []) {
      selected.add(edge.edge_id)
      if (!seenNodes.has(edge.from)) {
        seenNodes.add(edge.from)
        stack.push(edge.from)
      }
    }
  }
  return selected
}

function clamp(value: number, min: number, max: number) {
  return Math.min(Math.max(value, min), max)
}
