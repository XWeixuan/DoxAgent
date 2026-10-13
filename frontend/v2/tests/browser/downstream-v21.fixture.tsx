// Isolated reading test; real=1 uses a published research snapshot. No business actions.
import { createRoot } from "react-dom/client";
import { MemoryRouter, useLocation, useSearchParams } from "react-router-dom";
import { createRuntime, RuntimeProvider } from "../../src/core/runtime";
import { createTestAuth } from "../fixtures/auth";
import { resource, value } from "../fixtures/wire";
import { ShellBody } from "../../src/pages/expectations";
import { PolicyDocument } from "../../src/pages/strategy";
import type {
  ExpectationUnitV21,
  ExpectationsSummary,
  ShellSummary,
  PolicyV3,
  PolicyDetailV3,
  RelatedExpectation,
} from "@contract";
import realSnapshot from "../fixtures/v21-reading.real.json";
import { Document } from "../../src/components/document";
import "../../src/styles.css";
import "../../src/business.css";
import "../../src/refinement.css";
const unit: ExpectationUnitV21 = {
  schema_version: "document2.unit-view.v2.1",
  view_id: "d2#/shells/0/units/0",
  ordinal: 0,
  name: "客户订单",
  scope: "客户需求",
  horizon: "FY27",
  ref: Array.from({ length: 35 }, (_, i) => `O${i + 1}`),
  state: {
    parameters: [
      {
        name: "收入",
        definition: "客户收入",
        value_type: "NUMBER",
        ref: ["D1-O1"],
      },
    ],
    values: [
      {
        name: "预测",
        parameter: "收入",
        source_role: "SELL_SIDE",
        value: { number: 100, unit: "USD" },
        previous_value: null,
        time_scope: "FY27",
        as_of: "2026-10-10",
        validity_state: "CURRENT",
        ref: ["O9"],
      },
    ],
  },
  expectation_baseline: [
    {
      name: "共识基线",
      baseline: "按期交付",
      ordinary_progress: "交付增长",
      open_frontier: "新客户",
      time_scope: "FY27",
      ref: ["D1-O1"],
    },
  ],
  realization_factors: [
    {
      name: "需求持续性",
      mechanism: "重复订单",
      current_status: "存续",
      materiality_context: "盈利",
      scope_boundary: "客户",
      ref: ["D1-O1"],
    },
  ],
  potential_gaps: [
    {
      name: "客户结构",
      why_live: "新增客户",
      revision_logic: "市场扩展",
      possibility_space: [
        { name: "持续", implication: "上修" },
        { name: "客户流失", implication: "订单减少，收入预期下修。" },
      ],
      ref: ["D1-O1"],
    },
  ],
};
const policy: PolicyV3 = {
  policy_id: "P1",
  title: "双向客户进展",
  ref: ["context/document2.json#客户订单"],
  transmission: "订单影响收入与盈利",
  match_scope: "客户订单",
  activation_conditions: [
    {
      condition_id: "C1",
      decision: "LONG",
      trigger_layer: "MIDDLE",
      criterion: "订单超预期",
      calibration: { reference_state: "共识", trigger_boundary: "增量订单" },
    },
    {
      condition_id: "C2",
      decision: "SHORT",
      trigger_layer: "OUTER",
      criterion: "订单取消",
      calibration: {
        reference_state: "订单存续",
        trigger_boundary: "正式取消",
      },
    },
  ],
};
const runtime = createRuntime(createTestAuth());
const real = new URLSearchParams(location.search).has("real");
runtime.api.request = async (_schema, path) =>
  ({
    data: resource({
      units: {
        items: real
          ? ((realSnapshot.units_by_shell as Record<string, unknown[]>)[
              decodeURIComponent(
                path.match(/shells\/([^/]+)/)?.[1] ??
                  realSnapshot.shell.shell_id,
              )
            ] ?? [])
          : [unit],
        has_more: false,
        next_cursor: null,
        snapshot_id: "fixture",
        limit: 20,
      },
    }),
    meta: { view_id: "fixture" },
  }) as never;
const data = {
  run: {
    run_id: real ? realSnapshot.run : "d2",
    is_active: true,
    run_status: "PUBLISHED",
    publication_state: "COMPLETE",
    published_at: value("2026-10-10T12:00:00Z"),
  },
} as ExpectationsSummary;
const shell = {
  schema_version: "document2.shell-view.v2.1",
  view_id: "d2#/shells/0",
  shell_id: "shell-0000",
  ordinal: 0,
  name: "需求研究",
  scope: "商业需求",
  boundary: "客户侧",
  ref: [],
  status: "COMPLETED",
  content: resource(null),
} as unknown as ShellSummary;
const mode = new URLSearchParams(location.search).get("mode");
const relation: RelatedExpectation[] = [
  {
    run_id: "d2",
    shell_id: "shell-0000",
    shell_name: "需求研究",
    unit_id: "unit-0000",
    unit_name: "客户订单",
    content: "ALL",
    item: null,
    item_name: null,
    refs: [],
  },
];
function Fixture() {
  const loc = useLocation();
  const [params] = useSearchParams();
  const detail = realSnapshot.detail as unknown as PolicyDetailV3;
  const selectedShell =
    realSnapshot.shells.find((s) => s.shell_id === params.get("shell")) ??
    realSnapshot.shell;
  return (
    <main className="p-4">
      {mode === "markdown" ? (
        <Document
          markdown={
            "# 报告正文\n\n**结论【cite:O1】**与段落内引用【cite:O229】。\n\n| 指标 | 证据 |\n| --- | --- |\n| 收入 | 【cite:O1】 |\n\n`【cite:O3】`"
          }
        />
      ) : mode === "policy" && loc.pathname === "/" ? (
        <PolicyDocument
          ticker={real ? realSnapshot.ticker : "MU"}
          policy={real ? detail.policy : policy}
          sourceRun={real ? realSnapshot.run : "d2"}
          relatedExpectations={real ? detail.related_expectations : relation}
          eligibility={
            real
              ? {
                  executable: detail.summary.executable_directions,
                  consumed: detail.summary.consumed_directions,
                }
              : { executable: ["LONG"], consumed: ["SHORT"] }
          }
        />
      ) : (
        <ShellBody
          ticker={real ? realSnapshot.ticker : "MU"}
          data={data}
          shell={real ? (selectedShell as unknown as ShellSummary) : shell}
        />
      )}
    </main>
  );
}
createRoot(document.getElementById("root")!).render(
  <RuntimeProvider runtime={runtime}>
    <MemoryRouter>
      <Fixture />
    </MemoryRouter>
  </RuntimeProvider>,
);
