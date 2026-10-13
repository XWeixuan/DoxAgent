import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import type {
  ExpectationUnitV21,
  ParameterType,
  StateValueData,
} from "@contract";
import { StateTable, UnitV21 } from "../src/components/expectation-unit";
import { CitationProvider } from "../src/components/citation-text";
const values: [ParameterType, StateValueData][] = [
  ["NUMBER", { number: 100, unit: "USD" }],
  ["RANGE", { lower: 1, upper: 3, unit: "GW" }],
  [
    "TIME",
    { point: null, start: "2026-01", end: "2027-01", precision: "MONTH" },
  ],
  ["STAGE", { stage: "验证中" }],
  ["DIRECTION", { direction: "UP" }],
  ["EVIDENCE", { stance: "MIXED", strength: "MODERATE" }],
];
const state: ExpectationUnitV21["state"] = {
  parameters: [
    ...values.map(([value_type]) => ({
      name: value_type,
      definition: "口径定义",
      value_type,
      ref: ["O1"],
    })),
    { name: "空参数", definition: "未观测", value_type: "NUMBER", ref: [] },
  ],
  values: values.map(([name, value]) => ({
    name: `${name}观测`,
    parameter: name,
    source_role: "ACTUAL",
    value,
    previous_value: value,
    time_scope: "FY27",
    as_of: "2026-10-14",
    validity_state: "CURRENT",
    ref: ["O229"],
  })),
};
state.values.push({
  ...state.values[0],
  name: "历史观测",
  validity_state: "SUPERSEDED",
});
state.values.push({
  ...state.values[0],
  parameter: "缺定义参数",
  name: "保留孤立观测",
});
describe("native D2 reading", () => {
  it("preserves six value types, multiple observations, previous values and empty/orphan parameters", () => {
    const html = renderToStaticMarkup(
      <CitationProvider source={state}>
        <StateTable state={state} />
      </CitationProvider>,
    );
    for (const label of [
      "100 USD",
      "1–3 GW",
      "2026-01 — 2027-01",
      "验证中",
      "UP",
      "MIXED · MODERATE",
      "历史观测",
      "已替代",
      "空参数",
      "未提供",
      "保留孤立观测",
      "参数定义未提供",
      "先前值",
      "来源口径",
      "2026-10-14",
    ])
      expect(html).toContain(label);
    expect(html).toContain('rowSpan="2"');
    expect(html).toContain('aria-label="引用 O229"');
    expect(html).toContain('id="d2-state-7"');
  });
  it("keeps four distinct sections, named possibilities and baseline-only mode", () => {
    const unit: ExpectationUnitV21 = {
      schema_version: "document2.unit-view.v2.1",
      view_id: "u",
      ordinal: 0,
      name: "客户需求",
      scope: "美国",
      horizon: "FY27",
      ref: [],
      state,
      expectation_baseline: [
        {
          name: "共识基线",
          baseline: "当前预期",
          ordinary_progress: "正常推进",
          open_frontier: "新客户",
          time_scope: "FY27",
          ref: [],
        },
      ],
      realization_factors: [
        {
          name: "需求因素",
          mechanism: "重复订单",
          current_status: "已验证",
          materiality_context: "盈利",
          scope_boundary: "客户侧",
          ref: [],
        },
      ],
      potential_gaps: [
        {
          name: "持续性差异",
          why_live: "存在分歧",
          revision_logic: "调整预期",
          possibility_space: [
            { name: "持续增长", implication: "上修" },
            { name: "需求下降", implication: "下修" },
          ],
          ref: [],
        },
      ],
    };
    const html = renderToStaticMarkup(<UnitV21 unit={unit} mode="ALL" />);
    for (const label of [
      "d2-state-table",
      "d2-baseline-panel",
      "d2-factor-card",
      "d2-gap-card",
      "可能演化路径",
      "预期影响",
      "持续增长",
      "需求下降",
    ])
      expect(html).toContain(label);
    const baseline = renderToStaticMarkup(
      <UnitV21 unit={unit} mode="BASELINE" />,
    );
    expect(baseline).toContain("共识基线");
    expect(baseline).not.toContain("d2-state-table");
    expect(baseline).not.toContain("d2-gap-card");
  });
});
