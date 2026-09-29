import { createRoot } from "react-dom/client";
import { useState } from "react";
import type { CaseSummary, GraphBaseline, NodeId } from "@contract";
import { CaseRows, FlowGraph } from "../../src/pages/runtime";
import "../../src/styles.css";
import "../../src/business.css";
import "../../src/refinement.css";

const v = <T,>(value: T) => ({
  state: "AVAILABLE" as const,
  value,
  reason: null,
});
const rows = Array.from({ length: 3 }, (_, i): CaseSummary => ({
  case_id: `case-${i}`,
  revision: i + 1,
  semantic_day: "2026-09-29",
  runtime_mode: "REALTIME",
  first_round_shape: "PARALLEL",
  status: "COMPLETED",
  technical_status: "OK",
  title: v(`Micron supply update ${i}`),
  source: {
    source_id: "news",
    binding_id: "MU:news",
    name: "TrendForce",
    kind: "api",
  },
  received_at: "2026-09-29T10:00:00Z",
  completed_at: v("2026-09-29T10:05:00Z"),
  duration_seconds: v(300),
  initial_route: "TRADE",
  resolved_route: "TRADE",
  w3_status: "RESOLVED",
  final_novelty: v("NEW"),
  final_policy_hit: v(true),
  results:
    i === 0
      ? ["EVENT_DISCOVERY", "TRADE_INTENT", "TRADE_EXECUTION"]
      : ["TRADE_INTENT", "TRADE_NOT_EXECUTED"],
  trade: {
    intent_count: 1,
    state: i === 0 ? "EXECUTED" : "NOT_EXECUTED",
    reason_codes: [],
  },
  result_settled: true,
  trade_disposition: "EXECUTION_ACCEPTED",
  stream_item_id: `stream-${i}`,
  member_count: 1,
}));
const nodes = [
  "SOURCE",
  "W1",
  "W2",
  "W3",
  "ARCHIVE",
  "EVENT_DISCOVERY",
  "BADCASE",
  "TRADE_INTENT",
  "FAILURE",
  "TRADE_EXECUTION",
  "TRADE_NOT_EXECUTED",
] as NodeId[];
const edges: Array<[NodeId, NodeId]> = [
  ["SOURCE", "W1"],
  ["SOURCE", "W2"],
  ["W1", "W3"],
  ["W2", "W3"],
  ["W3", "EVENT_DISCOVERY"],
  ["W3", "TRADE_INTENT"],
  ["TRADE_INTENT", "TRADE_EXECUTION"],
  ["TRADE_INTENT", "TRADE_NOT_EXECUTED"],
];
const graph = {
  graph_revision: 1,
  stream_cursor: "cursor",
  cases: {
    items: rows,
    has_more: false,
    next_cursor: null,
    snapshot_id: "view",
    limit: 20,
  },
  nodes: nodes.map((node_id) => ({
    node_id,
    case_count: node_id === "TRADE_EXECUTION" ? 1 : 3,
    failed_case_count: 0,
    low_confidence_case_count: null,
    latest_processed_at: v("2026-09-29T10:05:00Z"),
    average_seconds: v(1),
    result_counts: [],
  })),
  edges: edges.map(([from, to]) => ({
    edge_id: `${from}:${to}`,
    from,
    to,
    case_count: to === "TRADE_EXECUTION" ? 1 : 3,
  })),
} as GraphBaseline;

function Fixture() {
  const [node, setNode] = useState<NodeId>();
  const [selected, setSelected] = useState(new Set<string>());
  const [opened, setOpened] = useState("");
  const toggle = (caseId: string) =>
    setSelected((prior) => {
      const next = new Set(prior);
      if (next.has(caseId)) next.delete(caseId);
      else next.add(caseId);
      return next;
    });
  const toggleAll = () =>
    setSelected((prior) =>
      prior.size === rows.length
        ? new Set()
        : new Set(rows.map((row) => row.case_id)),
    );
  return (
    <main style={{ maxWidth: 1500, padding: 20, margin: "auto" }}>
      <div className={`runtime-flow-layout${node ? " has-node" : ""}`}>
        <FlowGraph
          graph={graph}
          selected={node}
          pathEdges={
            node
              ? graph.edges.filter(
                  (edge) => edge.from === node || edge.to === node,
                )
              : []
          }
          select={setNode}
        />
        {node && <aside className="node-detail">节点详情：{node}</aside>}
      </div>
      <section className="case-list">
        <div className="filter-bar">
          <h2>最近处理记录</h2>
          <span id="selected">已选择 {selected.size}</span>
        </div>
        <CaseRows
          rows={rows}
          select={setOpened}
          selectedIds={selected}
          toggleSelected={toggle}
          toggleAll={toggleAll}
        />
        <span id="opened">已打开 {opened}</span>
      </section>
    </main>
  );
}
createRoot(document.getElementById("root")!).render(<Fixture />);
