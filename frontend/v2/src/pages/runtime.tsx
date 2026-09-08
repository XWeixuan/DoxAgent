import { LoadMore } from "@/components/load-more";
import { Network, X, ArrowUpRight } from "lucide-react";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useParams, useSearchParams } from "react-router-dom";
import type {
  Period,
  GraphBaseline,
  GraphDelta,
  GraphEdge,
  NodeId,
  CaseSummary,
  Response,
  Resource,
  Page,
} from "@contract";
import { useRuntime } from "@/core/runtime";
import { usePageContext, useRead, tickerPath, id } from "@/core/page-query";
import { usePages } from "@/core/paged-query";
import { useLive, useMinuteReads } from "@/core/live-query";
import { validateGraphRevision } from "@/core/updates";
import { queryString } from "@/core/api";
import { formatInstant, valueText, duration } from "@/core/format";
import { PageTitle, DetailPanel } from "@/components/page-kit";
import {
  PeriodPicker,
  MetricStrip,
  periods,
} from "@/components/business-metrics";
import { Module, Notice } from "@/components/state";
import { Button } from "@/components/ui/button";
import CaseDetails from "./runtime-case";
export const nodeNames: Record<NodeId, string> = {
  SOURCE: "接收消息",
  W1: "W1 · 新旧判定",
  W2: "W2 · Policy 匹配",
  W3: "W3 · 二轮研判",
  ARCHIVE: "归档",
  EVENT_DISCOVERY: "事件发现",
  BADCASE: "Badcase",
  TRADE_EXECUTION: "交易执行",
  FAILURE: "失败",
};
export default function RuntimePage() {
  const { ticker = "" } = useParams();
  const [p, setP] = useSearchParams();
  const period = (periods.find((x) => x[0] === p.get("period"))?.[0] ??
    "PREVIOUS_TRADING_DAY") as Period;
  const c = usePageContext(ticker, "RUNTIME", period);
  return (
    <>
      <PageTitle title={`${ticker} 运行状态`} refresh={c.refetch}>
        <PeriodPicker
          value={period}
          options={c.data?.data.period_options}
          onChange={(v) => {
            const n = new URLSearchParams(p);
            n.set("period", v);
            setP(n);
          }}
        />
      </PageTitle>
      {c.error && <Notice danger>{c.error.message}</Notice>}
      {c.data && (
        <RuntimeBody
          ticker={ticker}
          context={c.data}
          period={period}
          reset={() => void c.refetch()}
        />
      )}
    </>
  );
}
function RuntimeBody({
  ticker,
  context,
  period,
  reset,
}: {
  ticker: string;
  context: Response<import("@contract").ReadContext>;
  period: Period;
  reset: () => void;
}) {
  const { api, scope, query } = useRuntime();
  const view = context.data.view_id;
  const minuteKey = [scope, "runtime-minute-view", view];
  const minute = useQuery({
    queryKey: minuteKey,
    queryFn: async () => context,
    initialData: context,
  });
  const readView = minute.data.data.view_id;
  const [p, setP] = useSearchParams(),
    [node, setNode] = useState<NodeId>(),
    [selected, setSelected] = useState<{ id: string; view: string }>();
  const selectCase = (id: string) => setSelected({ id, view: readView });
  const metrics = useRead(
    "RuntimeMetrics",
    tickerPath(ticker) +
      "/runtime/metrics" +
      queryString({ view_id: readView }),
    readView,
  );
  const graphPath =
    tickerPath(ticker) +
    "/runtime/graph" +
    queryString({ view_id: view, limit: "20" });
  const graph = useRead("Graph", graphPath, view);
  const result = p.get("result") ?? undefined,
    source = p.get("source") ?? undefined;
  const cases = usePages<CaseSummary, "Cases">(
    "Cases",
    tickerPath(ticker) +
      "/runtime/cases" +
      queryString({
        view_id: readView,
        result,
        source_id: source,
        limit: "20",
      }),
    (r) => r.data,
    readView,
  );
  const detail = useRead(
    "Node",
    node
      ? tickerPath(ticker) +
          `/runtime/nodes/${id(node)}` +
          queryString({ view_id: readView, limit: "20" })
      : null,
    readView,
  );
  // A minute refresh updates only small current summaries. Graph and case bodies keep their own lifecycle.
  const minuteError = useMinuteReads(context, async () => {
    const next = await api.request(
      "ReadContext",
      "/read-context" +
        queryString({ ticker, page: "RUNTIME", period, refresh: "MINUTE" }),
    );
    const v = next.data.view_id;
    const metricPath =
      tickerPath(ticker) + "/runtime/metrics" + queryString({ view_id: v });
    const casePath =
      tickerPath(ticker) +
      "/runtime/cases" +
      queryString({ view_id: v, result, source_id: source, limit: "20" });
    const [m, c] = await Promise.all([
      api.request("RuntimeMetrics", metricPath, { view: v }),
      api.request("Cases", casePath, { view: v }),
    ]);
    if (node) {
      const path =
        tickerPath(ticker) +
        `/runtime/nodes/${id(node)}` +
        queryString({ view_id: v, limit: "20" });
      const n = await api.request("Node", path, { view: v });
      query.setQueryData([scope, "read", "Node", path, v], n);
    }
    query.setQueryData([scope, "read", "RuntimeMetrics", metricPath, v], m);
    query.setQueryData([scope, "pages", "Cases", casePath, v], {
      pages: [c],
      pageParams: [undefined],
    });
    query.setQueryData(minuteKey, next);
  });
  const streamError = useLive<GraphDelta>(
    tickerPath(ticker) +
      "/runtime/graph/events" +
      queryString({ view_id: view }),
    view,
    graph.data?.data.data?.stream_cursor,
    (d) => {
      query.setQueryData<Response<Resource<GraphBaseline>>>(
        [scope, "read", "Graph", graphPath, view],
        (old) => {
          const g = old?.data.data;
          if (!old || !g) return old;
          validateGraphRevision(g.graph_revision, d);
          const items = g.cases.items.filter(
            (c) => !d.remove_case_ids.includes(c.case_id),
          );
          for (const c of d.upsert_cases) {
            const at = items.findIndex((x) => x.case_id === c.case_id);
            if (at >= 0) items[at] = c;
            else items.unshift(c);
          }
          return {
            ...old,
            data: {
              ...old.data,
              data: {
                ...g,
                graph_revision: d.graph_revision,
                nodes: g.nodes.map(
                  (n) =>
                    d.replace_nodes.find((x) => x.node_id === n.node_id) ?? n,
                ),
                edges: d.replace_edges,
                cases: { ...g.cases, items: items.slice(0, g.cases.limit) },
              },
            },
          };
        },
      );
    },
    reset,
  );
  const set = (key: string, v: string) => {
    const n = new URLSearchParams(p);
    if (v) n.set(key, v);
    else n.delete(key);
    setP(n);
  };
  return (
    <>
      <Module query={metrics} label="运行指标">
        {(m) => (
          <MetricStrip
            period={period}
            items={[
              { label: "周期处理", metric: m.processed_cases },
              { label: "一轮判定延时 · s", metric: m.hot_path_mean_seconds },
              { label: "二轮研判延时 · s", metric: m.w3_mean_seconds },
              {
                label: "NEW / OLD",
                metric: m.new_cases,
                secondary: m.old_cases,
              },
              {
                label: "Policy 命中 / 比例",
                metric: m.hit_cases,
                secondary: m.hit_case_ratio,
              },
              { label: "二轮研判", metric: m.w3_cases },
              { label: "事件发现", metric: m.new_fact_candidates },
              { label: "交易执行", metric: m.executed_cases },
            ]}
          />
        )}
      </Module>
      {streamError && <Notice danger>{streamError}</Notice>}
      {minuteError && <Notice danger>{minuteError}</Notice>}
      <div className={"runtime-flow-layout" + (node ? " has-node" : "")}>
        <Module query={graph} label="运行链路图">
          {(g) => (
            <FlowGraph
              graph={g}
              selected={node}
              pathEdges={detail.data?.data.data?.path_edges ?? []}
              select={setNode}
            />
          )}
        </Module>
        {node && (
          <section className="node-detail" id="runtime-node-detail">
            <header>
              <h2>
                <Network aria-hidden="true" />
                {nodeNames[node]}
                <span>节点详情</span>
              </h2>
              <Button variant="outline" onClick={() => setNode(undefined)}>
                关闭
              </Button>
            </header>
            <Module query={detail} label="节点详情">
              {(d) => (
                <>
                  <div className="node-statistics">
                    <span>
                      处理 <strong>{d.summary.case_count}</strong>
                    </span>
                    <span>
                      失败 <strong>{d.summary.failed_case_count}</strong>
                    </span>
                    <span>
                      平均延时{" "}
                      <strong>
                        {valueText(
                          d.summary.average_seconds,
                          (v) => v.toFixed(2) + "s",
                        )}
                      </strong>
                    </span>
                    {d.summary.low_confidence_case_count !== null && (
                      <span>
                        低置信度{" "}
                        <strong>{d.summary.low_confidence_case_count}</strong>
                      </span>
                    )}
                    <span className="node-latest">
                      最近处理{" "}
                      {valueText(d.summary.latest_processed_at, formatInstant)}
                    </span>
                    {d.summary.result_counts.map((r) => (
                      <span key={r.result}>
                        {nodeNames[r.result]} {r.case_count}
                      </span>
                    ))}
                  </div>
                  <NodeCases
                    key={`${readView}:${node}`}
                    ticker={ticker}
                    node={node}
                    view={readView}
                    first={d.recent_cases}
                    select={selectCase}
                  />
                </>
              )}
            </Module>
          </section>
        )}
      </div>
      <section className="case-list">
        <div className="filter-bar">
          <h2>最近处理记录</h2>
          <div className="business-filters">
            <label>
              结果
              <select
                value={result ?? ""}
                onChange={(e) => set("result", e.target.value)}
              >
                <option value="">全部结果</option>
                {(
                  [
                    "ARCHIVE",
                    "EVENT_DISCOVERY",
                    "BADCASE",
                    "TRADE_EXECUTION",
                    "FAILURE",
                  ] as const
                ).map((r) => (
                  <option key={r} value={r}>
                    {nodeNames[r]}
                  </option>
                ))}
              </select>
            </label>
            <label>
              来源
              <select
                value={source ?? ""}
                onChange={(e) => set("source", e.target.value)}
              >
                <option value="">全部消息源</option>
                {Array.from(
                  new Map(
                    (cases.data?.data.data?.items ?? []).map((c) => [
                      c.source.source_id,
                      c.source.name,
                    ]),
                  ),
                ).map(([v, l]) => (
                  <option key={v} value={v}>
                    {l}
                  </option>
                ))}
              </select>
            </label>
          </div>
        </div>
        <Module query={cases} label="最近处理记录">
          {(d) => <CaseRows rows={d.items} select={selectCase} />}
        </Module>
        {cases.hasNextPage && (
          <LoadMore
            variant="outline"
            onClick={() => void cases.fetchNextPage()}
          >
            更早记录
          </LoadMore>
        )}
      </section>
      {selected && (
        <DetailPanel
          label="研判明细"
          className="case-drawer"
          close={() => setSelected(undefined)}
        >
          <header>
            <h2>研判明细</h2>
            <Button variant="outline" onClick={() => setSelected(undefined)}>
              关闭
            </Button>
          </header>
          <CaseDetails
            ticker={ticker}
            caseId={selected.id}
            view={selected.view}
          />
        </DetailPanel>
      )}
    </>
  );
}
const positions: Record<NodeId, [number, number]> = {
  SOURCE: [18, 172],
  W1: [234, 78],
  W2: [234, 256],
  W3: [464, 172],
  ARCHIVE: [702, 24],
  EVENT_DISCOVERY: [702, 98],
  BADCASE: [702, 172],
  TRADE_EXECUTION: [702, 246],
  FAILURE: [702, 320],
};
const nodeColors: Record<NodeId, string> = {
  SOURCE: "#68616f",
  W1: "#42668b",
  W2: "#42668b",
  W3: "#a14066",
  ARCHIVE: "#68616f",
  EVENT_DISCOVERY: "#42668b",
  BADCASE: "#94631b",
  TRADE_EXECUTION: "#317357",
  FAILURE: "#b13a35",
};
function FlowGraph({
  graph,
  selected,
  pathEdges,
  select,
}: {
  graph: GraphBaseline;
  selected?: NodeId;
  pathEdges: GraphEdge[];
  select: (n?: NodeId) => void;
}) {
  const keep = new Set<NodeId>(selected ? [selected] : []);
  const paths = new Map(pathEdges.map((e) => [e.edge_id, e]));
  pathEdges.forEach((e) => {
    keep.add(e.from);
    keep.add(e.to);
  });
  return (
    <section className="flow-panel">
      <header className="flow-heading">
        <h2>
          <Network aria-hidden="true" />
          运行链路
        </h2>
        {selected && (
          <div>
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label="恢复完整链路"
              onClick={() => select(undefined)}
            >
              <X />
            </Button>
          </div>
        )}
      </header>
      <svg
        className="runtime-graph"
        viewBox="0 0 880 400"
        role="group"
        aria-label="Runtime Case 流向"
        onClick={() => select(undefined)}
      >
        <defs>
          <marker
            id="flow-arrow-v2"
            viewBox="0 0 10 10"
            refX="9"
            refY="5"
            markerWidth="5"
            markerHeight="5"
            orient="auto"
          >
            <path d="M0 0L10 5L0 10z" fill="context-stroke" />
          </marker>
        </defs>
        <g className="stage-labels">
          <text x="18" y="16">
            接收
          </text>
          <text x="234" y="16">
            一轮并行判定
          </text>
          <text x="464" y="16">
            二轮研判
          </text>
          <text x="702" y="16">
            最终结果
          </text>
        </g>
        {graph.edges.map((e) => {
          const [ax, ay] = positions[e.from],
            [bx, by] = positions[e.to];
          const sx = ax + 156,
            sy = ay + 27,
            ty = by + 27;
          const bypass = bx === 702 && e.from !== "W3";
          let d: string, lx: number, ly: number;
          if (bypass) {
            const upper = by < 172;
            const lane = upper ? 38 : 389;
            const junction = sx + 22,
              arrival = 666;
            d = `M${sx} ${sy} H${junction - 8} Q${junction} ${sy} ${junction} ${sy + (upper ? -8 : 8)} V${lane + (upper ? 8 : -8)} Q${junction} ${lane} ${junction + 8} ${lane} H${arrival - 8} Q${arrival} ${lane} ${arrival} ${lane + (upper ? 8 : -8)} V${ty + (upper ? -8 : 8)} Q${arrival} ${ty} ${arrival + 8} ${ty} H${bx}`;
            const peers = graph.edges.filter(
              (edge) =>
                positions[edge.to][0] === 702 &&
                edge.from !== "W3" &&
                positions[edge.to][1] < 172 === upper,
            );
            lx =
              430 +
              ((peers.findIndex((edge) => edge.edge_id === e.edge_id) + 0.5) *
                180) /
                peers.length;
            ly = lane;
          } else {
            const bend = (bx - sx) / 2;
            d = `M${sx} ${sy} C${sx + bend} ${sy},${bx - bend} ${ty},${bx} ${ty}`;
            lx = (sx + bx) / 2;
            ly = (sy + ty) / 2;
          }
          const count =
            selected && paths.has(e.edge_id)
              ? paths.get(e.edge_id)!.case_count
              : e.case_count;
          const visible = !selected || paths.has(e.edge_id);
          return (
            <g key={e.edge_id} opacity={visible ? 1 : 0} pointerEvents="none">
              <path
                d={d}
                fill="none"
                stroke={selected ? nodeColors[selected] : "#ad98aa"}
                strokeWidth={1.5 + Math.min(1.5, Math.log2(count + 1) / 4)}
                markerEnd="url(#flow-arrow-v2)"
              />
              <g className="edge-counter" transform={`translate(${lx},${ly})`}>
                <rect x="-16" y="-11" width="32" height="22" rx="7" />
                <text textAnchor="middle" y="4">
                  {count}
                </text>
              </g>
            </g>
          );
        })}
        {graph.nodes.map((n) => {
          const [x, y] = positions[n.node_id];
          const color = nodeColors[n.node_id];
          return (
            <g
              key={n.node_id}
              className="graph-node"
              role="button"
              tabIndex={0}
              aria-label={nodeNames[n.node_id]}
              aria-pressed={selected === n.node_id}
              transform={`translate(${x},${y})`}
              opacity={!selected || keep.has(n.node_id) ? 1 : 0.25}
              style={{ "--node-color": color } as React.CSSProperties}
              onClick={(e) => {
                e.stopPropagation();
                select(n.node_id);
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  select(n.node_id);
                }
              }}
            >
              <rect className="node-surface" width="156" height="54" rx="7" />
              <rect
                className="node-accent"
                width="3"
                height="30"
                y="12"
                rx="1.5"
              />
              <text className="node-title" x="15" y="21">
                {nodeNames[n.node_id]}
              </text>
              <text className="node-count" x="15" y="45">
                {n.case_count}
              </text>
              <text className="node-open" x="132" y="43">
                ↗
              </text>
            </g>
          );
        })}
      </svg>
    </section>
  );
}
function NodeCases({
  ticker,
  node,
  view,
  first,
  select,
}: {
  ticker: string;
  node: NodeId;
  view: string;
  first: Page<CaseSummary>;
  select: (id: string) => void;
}) {
  const [more, setMore] = useState(false);
  const q = usePages<CaseSummary, "Cases">(
    "Cases",
    more
      ? tickerPath(ticker) +
          `/runtime/nodes/${id(node)}/cases` +
          queryString({ view_id: view, limit: "20" })
      : null,
    (r) => r.data,
    view,
    first,
  );
  return (
    <>
      <CaseRows
        compact
        rows={q.data?.data.data?.items ?? first.items}
        select={select}
      />
      {(q.hasNextPage || (!more && first.has_more)) && (
        <LoadMore
          variant="outline"
          disabled={q.isFetchingNextPage}
          onClick={() => (more ? void q.fetchNextPage() : setMore(true))}
        >
          更多节点记录
        </LoadMore>
      )}
      {q.error && <Notice danger>{q.error.message}</Notice>}
    </>
  );
}
function CaseState({ row: c }: { row: CaseSummary }) {
  return (
    <span className="case-state">
      {(
        {
          COMPLETED: "已完成",
          SUCCEEDED: "已完成",
          FAILED: "失败",
          RUNNING: "处理中",
          CREATED: "待处理",
          PENDING_RETRY: "等待重试",
          RETRY_PENDING: "等待重试",
          ADJUDICATED: "已判定",
          PENDING_W3: "等待二轮研判",
          UNAVAILABLE: "不可用",
        } as Record<string, string>
      )[c.status] ?? c.status}
    </span>
  );
}
function CaseResults({
  row: c,
  showState = true,
}: {
  row: CaseSummary;
  showState?: boolean;
}) {
  return (
    <div className="case-results">
      {!c.results.length && (
        <span className="case-source">
          {c.result_settled ? "结果未记录" : "尚未形成结果"}
        </span>
      )}
      {c.results.map((r) => (
        <span className={"domain-tag result-" + r} key={r}>
          {nodeNames[r]}
        </span>
      ))}
      {showState && <CaseState row={c} />}
    </div>
  );
}
export function CaseRows({
  rows,
  select,
  compact = false,
}: {
  rows: CaseSummary[];
  select: (id: string) => void;
  compact?: boolean;
}) {
  if (!rows.length) return <Notice>当前范围没有处理记录</Notice>;
  if (compact)
    return (
      <div className="node-case-rows">
        {rows.map((c) => (
          <button key={c.case_id} onClick={() => select(c.case_id)}>
            <strong>{valueText(c.title)}</strong>
            <div className="node-case-meta">
              <span>{c.source.name}</span>
              <span>{valueText(c.duration_seconds, duration)}</span>
            </div>
            <CaseResults row={c} />
            <div className="node-case-times">
              <span>接收 {formatInstant(c.received_at)}</span>
              <span>完成 {valueText(c.completed_at, formatInstant)}</span>
            </div>
          </button>
        ))}
      </div>
    );
  return (
    <div className="case-table-wrap">
      <table className="case-table">
        <colgroup>
          <col className="case-title-col" />
          <col className="case-result-col" />
          <col className="case-status-col" />
          <col />
          <col />
          <col className="case-time-col" />
          <col className="case-action-col" />
        </colgroup>
        <thead>
          <tr>
            <th scope="col">消息 / 来源</th>
            <th scope="col">最终结果</th>
            <th scope="col">状态</th>
            <th scope="col">接收时间</th>
            <th scope="col">完成时间</th>
            <th scope="col">耗时</th>
            <th scope="col">
              <span className="sr-only">详情</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((c) => (
            <tr key={c.case_id} onClick={() => select(c.case_id)}>
              <td>
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    select(c.case_id);
                  }}
                  className="case-title-button"
                >
                  {valueText(c.title)}
                </button>
                <span className="case-source">{c.source.name}</span>
              </td>
              <td>
                <CaseResults row={c} showState={false} />
              </td>
              <td>
                <CaseState row={c} />
              </td>
              <td>{formatInstant(c.received_at)}</td>
              <td>{valueText(c.completed_at, formatInstant)}</td>
              <td className="numeric">
                {valueText(c.duration_seconds, duration)}
              </td>
              <td>
                <Button
                  variant="ghost"
                  size="icon-sm"
                  aria-label={"查看研判明细：" + valueText(c.title)}
                  onClick={(e) => {
                    e.stopPropagation();
                    select(c.case_id);
                  }}
                >
                  <ArrowUpRight />
                </Button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
