import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import type { SourceStatus, Metric } from "@contract";
import { SourceCard } from "../src/pages/message-bus";
import { MetricNote } from "../src/pages/overview/metrics";

const missing = {
  state: "NOT_RECORDED",
  value: null,
  reason: "NOT_RECORDED",
} as const;
const metric: Metric = {
  metric_id: "published_revisions",
  unit: "COUNT",
  previous: missing,
  change_pct: missing,
  previous_coverage: null,
  current: { state: "AVAILABLE", value: "17", reason: null },
  current_coverage: {
    state: "COMPLETE",
    reasons: [],
    known_count: null,
    excluded_count: null,
    observed_through: null,
  },
  provisional: false,
};
const status = {
  source: { name: "Source", kind: "api" },
  publication_mode: "immediate",
  poll_status: "succeeded",
  countdown_state: "UNKNOWN",
  next_target_at: missing,
  target_interval_seconds: 60,
  last_success_at: missing,
  last_published_count: { state: "AVAILABLE", value: 999, reason: null },
  last_poll_latency_seconds: missing,
  current_error: null,
} as SourceStatus;
it("shows the day aggregate, never the last poll count under the day label", () => {
  const html = renderToStaticMarkup(
    createElement(SourceCard, { status, today: metric }),
  );
  expect(html).toContain("当天新增");
  expect(html).toContain("17");
  expect(html).not.toContain("999");
  expect(html).not.toContain("最近新增");
  const absent = renderToStaticMarkup(createElement(SourceCard, { status }));
  expect(absent).toContain("未记录");
  expect(absent).not.toContain("999");
});
it("does not duplicate missing metrics with a generic coverage badge and retains partial-count warnings", () => {
  const unknown = {
    ...metric,
    current: missing,
    current_coverage: { ...metric.current_coverage, state: "UNKNOWN" as const },
  };
  expect(
    renderToStaticMarkup(createElement(MetricNote, { metric: unknown })),
  ).toBe("");
  expect(
    renderToStaticMarkup(
      createElement(MetricNote, {
        metric: {
          ...metric,
          current_coverage: { ...metric.current_coverage, state: "PARTIAL" },
        },
      }),
    ),
  ).toContain("统计不完整");
});
