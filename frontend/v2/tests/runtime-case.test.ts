import { describe, expect, it } from "vitest";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import {
  RoundRows,
  EventEvidence,
  PolicyEvidence,
} from "../src/pages/runtime-case";
import { value } from "./fixtures/wire";

describe("Runtime Case presentation", () => {
  it("does not invent a successful R2 for an empty or unknown recall", () => {
    const render = (reason: "NO_POLICY_CANDIDATE" | null, count = 0) =>
      renderToStaticMarkup(
        createElement(RoundRows, {
          node: "W2",
          round: "R2",
          attempts: [],
          summary: {
            round: "R2",
            attempt_count: count,
            not_executed_reason: reason,
          },
        }),
      );
    expect(render("NO_POLICY_CANDIDATE")).toContain(
      "未执行：未召回候选 Policy",
    );
    expect(render(null)).toContain("暂无执行记录");
    expect(render(null, 21)).toContain("请加载更多尝试");
    expect(render("NO_POLICY_CANDIDATE")).not.toContain("SUCCEEDED");
  });
  it("shows exact attributed fact text, not an entire event's facts", () => {
    const html = renderToStaticMarkup(
      createElement(EventEvidence, {
        ticker: "MU",
        references: [
          {
            kind: "CANONICAL",
            event_key: "key",
            event_id: "E14",
            fact_ids: ["F281"],
            library_snapshot_id: "snapshot",
            library_version: 4,
            provisional_snapshot_version: null,
            semantic_day: null,
            title: value("Union dispute"),
            facts: [
              { fact_id: "F281", proposition: value("Strike threatened") },
              { fact_id: "F999", proposition: value("Unrelated fact") },
            ],
          },
        ],
      }),
    );
    expect(html).toContain("Union dispute");
    expect(html).toContain("Strike threatened");
    expect(html).not.toContain("Unrelated fact");
    expect(html).toContain('target="_blank"');
    expect(html).toContain("snapshot=snapshot&amp;event=E14&amp;fact=F281");
  });
  it("keeps provisional and unresolved identities honest", () => {
    const html = renderToStaticMarkup(
      createElement(EventEvidence, {
        ticker: "MU",
        references: [
          {
            kind: "PROVISIONAL",
            event_key: null,
            event_id: "E81",
            fact_ids: [],
            library_snapshot_id: "snapshot",
            library_version: 4,
            provisional_snapshot_version: 3,
            semantic_day: "2026-09-15",
            provisional_proposition: value("Provisional supply news"),
          },
        ],
        unresolved: ["E99"],
        attributions: [{ event_id: "E99", fact_ids: ["F7"] }],
      }),
    );
    expect(html).toContain("Provisional supply news");
    expect(html).toContain("F7");
    expect(html).not.toContain("F81");
    expect(html).not.toContain('target="_blank"');
  });
  it("distinguishes unknown recall and unresolved policy names", () => {
    expect(
      renderToStaticMarkup(
        createElement(PolicyEvidence, { ticker: "MU", label: "R1 召回" }),
      ),
    ).toContain("候选信息未记录");
    expect(
      renderToStaticMarkup(
        createElement(PolicyEvidence, {
          ticker: "MU",
          label: "R2 命中",
          policies: [],
          unresolved: ["P7"],
        }),
      ),
    ).toContain("固定快照名称未解析");
  });
  it("opens a case-pinned policy in a new tab", () => {
    const html = renderToStaticMarkup(
      createElement(PolicyEvidence, {
        ticker: "MU",
        label: "最终命中 Policy",
        filter: "HIT",
        activationId: "run-17",
        policies: [
          {
            policy_id: "pol-1",
            policy_set_version: 3,
            policy_activation_revision: "rev",
            condition_ids: ["cond-1"],
            title: value("HBM delivery"),
          },
        ],
      }),
    );
    expect(html).toContain('target="_blank"');
    expect(html).toContain(
      "filter=HIT&amp;policy=pol-1&amp;policy_set_version=3&amp;activation=run-17",
    );
  });
});
