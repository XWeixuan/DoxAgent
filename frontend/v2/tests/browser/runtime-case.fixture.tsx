// Component-only synthetic fixture: no business API, database, model or auth service.
import { createRoot } from "react-dom/client";
import { createRuntime, RuntimeProvider } from "../../src/core/runtime";
import { createTestAuth } from "../fixtures/auth";
import { resource, value } from "../fixtures/wire";
import CaseDetails from "../../src/pages/runtime-case";
import type { CaseDetail, ModelAttempt, Page, Resource } from "@contract";
import "../../src/styles.css";
import "../../src/business.css";
import "../../src/refinement.css";

const mode = new URLSearchParams(location.search).get("mode") ?? "executed";
const page = <T,>(items: T[]): Page<T> => ({
  items,
  has_more: false,
  next_cursor: null,
  snapshot_id: "fixture-view",
  limit: 20,
});
const noReason: Resource<never> = {
  state: "NOT_PRODUCED",
  data: null,
  reason: "NOT_RECORDED",
  coverage: resource(null).coverage,
};
const reasoningText =
  "The reported union demands may trigger the supply disruption policy.";
const reasonRef = {
  content_id: "fixture-reason",
  content_type: "text/plain" as const,
  size_bytes: reasoningText.length,
  sha256: "0".repeat(64),
};
const timing = {
  first_started_at: value("2026-09-15T01:00:00Z"),
  completed_at: value("2026-09-15T01:01:00Z"),
  wall_seconds: value(60),
  basis: "RECORDED" as const,
};
const attempt = (
  node: "W1" | "W2" | "W3",
  round: "R1" | "R2" | "R3" | "AGENT",
  status: ModelAttempt["status"],
  n = 1,
): ModelAttempt => ({
  attempt_id: `${node}-${round}-${n}`,
  turn_id: null,
  node_id: node,
  round,
  status,
  ordinal: n,
  attempt_number: n,
  timing,
  error: null,
});
const policy = {
  policy_id: "P8",
  policy_set_version: 4,
  policy_activation_revision: "fixed-revision",
  condition_ids: ["C2"],
  title: value("Micron memory supply disruption"),
};
const empty = mode === "empty" || mode === "skipped" || mode === "unknown";
const details = {
  runtime_activation_id: value("fixture-activation"),
  summary: {
    title: value("Micron focused Runtime Case"),
    source: { name: "Synthetic News", kind: "api" },
    first_round_tags: empty
      ? []
      : ["FIRST_ROUND_TRADE", "POLICY_RELATED_EVENT"],
  },
  failures: [],
  candidate_count: value(0),
  execution_count: value(0),
  messages: page([]),
  w1: resource({
    novelty: value("NEW"),
    extraction:
      mode === "extraction"
        ? resource({ capture_mode: "NEW_CAPTURE", candidate_count: 1 })
        : noReason,
    confidence: value("normal"),
    references: [
      {
        kind: "CANONICAL",
        event_id: "E14",
        event_key: "fixed-event",
        fact_ids: ["F281"],
        title: value("Micron Taiwan union profit-sharing dispute"),
        facts: [
          {
            fact_id: "F281",
            proposition: value(
              "Workers threatened to strike unless the profit-sharing demand is met.",
            ),
          },
        ],
      },
      {
        kind: "PROVISIONAL",
        event_id: "E81",
        fact_ids: [],
        provisional_proposition: value(
          "A newly admitted supply disruption report.",
        ),
      },
    ],
    reasoning: resource(reasonRef),
    attempts: page([
      attempt("W1", "R1", "SUCCEEDED"),
      attempt("W1", "R2", "SUCCEEDED"),
      ...(mode === "extraction" ? [attempt("W1", "R3", "SUCCEEDED")] : []),
    ]),
    rounds: [
      { round: "R1", attempt_count: 1, not_executed_reason: null },
      { round: "R2", attempt_count: 1, not_executed_reason: null },
      {
        round: "R3",
        attempt_count: mode === "extraction" ? 1 : 0,
        not_executed_reason: mode === "extraction" ? null : "DELTA_OWNED_BY_W3",
      },
    ],
  }),
  w2: resource({
    skipped: mode === "skipped",
    policy_hit: value(!empty),
    policy_related: value(!empty),
    related_policies: empty ? [] : [policy],
    novel_fact_alignment: {
      status: empty ? "NOT_EXECUTED" : "SUCCEEDED",
      not_executed_reason: empty ? "NO_POLICY_HIT_OR_RELATED" : null,
      policy_fact_novelty: empty
        ? { state: "UNAVAILABLE", value: null, reason: "NOT_APPLICABLE" }
        : value("NEW"),
      condition_results: empty
        ? []
        : [{ condition_ids: ["P8/C2"], is_new: true }],
    },
    confidence: value("normal"),
    policies: empty ? [] : [policy],
    ...(mode === "unknown"
      ? {}
      : { candidate_policies: empty ? [] : [policy] }),
    reasoning: resource(reasonRef),
    attempts: page(
      empty
        ? mode === "skipped"
          ? []
          : [attempt("W2", "R1", "SUCCEEDED")]
        : [
            attempt("W2", "R1", "SUCCEEDED"),
            attempt("W2", "R2", "FAILED"),
            attempt("W2", "R2", "SUCCEEDED", 2),
            attempt("W2", "R3", "SUCCEEDED"),
          ],
    ),
    rounds: [
      {
        round: "R1",
        attempt_count: mode === "skipped" ? 0 : 1,
        not_executed_reason: mode === "skipped" ? "W2_SKIPPED" : null,
      },
      {
        round: "R2",
        attempt_count: empty ? 0 : 2,
        not_executed_reason:
          mode === "empty"
            ? "NO_POLICY_CANDIDATE"
            : mode === "skipped"
              ? "W2_SKIPPED"
              : null,
      },
      {
        round: "R3",
        attempt_count: empty ? 0 : 1,
        not_executed_reason: empty ? "NO_POLICY_HIT_OR_RELATED" : null,
      },
    ],
  }),
  ...(mode === "watch"
    ? {
        policy_watch: {
          followup: [
            {
              policy_id: "P8",
              reason: "等待客户确认",
              expires_at: "2026-10-12T04:00:00Z",
            },
          ],
          actions: [
            { policy_id: "P8", action: "CANCEL", reason: "证据已完整" },
          ],
          receipt: {
            outcomes: [
              { policy_id: "P8", action: "CANCEL", status: "APPLIED" },
            ],
          },
        },
        policy_execution: {
          selected_condition: null,
          reason: "尚不满足交易条件",
        },
      }
    : {}),
  ...(mode === "selection"
    ? {
        policy_execution: {
          selected_condition: { policy_id: "P8", condition_id: "C2" },
          reason: "采用明确命中边界",
        },
      }
    : {}),
  w3:
    mode === "selection"
      ? resource({
          status: "RESOLVED",
          novelty: value("NEW"),
          policy_hit: value(true),
          expert_trade: value(false),
          direction: value(null),
          prior_expectation: value(null),
          expectation_delta: value(null),
          references: [],
          policies: [policy],
          attempts: page([]),
          reasoning: {
            novelty: noReason,
            policy: noReason,
            expert_trade: noReason,
          },
        })
      : noReason,
} as unknown as CaseDetail;
const runtime = createRuntime(createTestAuth());
if (mode === "v4") {
  details.summary.source_name = "Reuters";
  details.w3 = resource({
    ...details.w3.data,
    status: "RESOLVED",
    mode: "REVALIDATE_THEN_EVALUATE",
    decision: {
      decision: "EXPERT_TRADE",
      reason: "新增产能改变短期预期",
      policy_trade: null,
      expert_trade: {
        direction: "LONG",
        prior_expectation: "试产",
        expectation_delta: "量产",
      },
    },
    operations_status: "FAILED",
    operations_error: "后置操作重试耗尽",
    novelty: value(null),
    policy_hit: value(null),
    references: [],
    policies: [],
    expert_trade: value(true),
    expert_trade_evaluated: value(true),
    direction: value("LONG"),
    prior_expectation: value("试产"),
    expectation_delta: value("量产"),
    timing,
    attempts: page([
      { ...attempt("W3", "AGENT", "SUCCEEDED"), phase: "trade_decision" },
      {
        ...attempt("W3", "AGENT", "FAILED", 2),
        phase: "post_decision_operations",
      },
    ]),
    reasoning: {
      novelty: noReason,
      policy: noReason,
      expert_trade: resource(reasonRef),
    },
  } as never);
  details.w3_state = {
    watch: {
      items: [
        {
          policy_id: "P8",
          reason: "等待订单",
          expires_at: "2026-10-14T04:00:00Z",
        },
      ],
    },
    pins: {
      items: [
        {
          pin_id: "pin1",
          message: {
            title: "量产基线",
            source_name: "Reuters",
            body: "完整业务正文",
          },
          reason: "观察后续",
          expires_at: "2026-10-14T04:00:00Z",
        },
      ],
    },
    receipt: { watch: [{ policy_id: "P8", action: "SET", status: "APPLIED" }] },
  };
}
if (mode === "v5" || mode === "v5-failed") {
  details.summary.final_policy_source = "W2";
  details.summary.w3_decision = value("NO_TRADE");
  details.w3 = resource({
    status: "RESOLVED",
    mode: "REVALIDATE_THEN_EVALUATE",
    decision: {
      decision: "NO_TRADE",
      reason: "原W2命中仍保留，当前无交易机会",
      prior_expectation: null,
      expectation_delta: null,
    },
    operations_status: mode === "v5-failed" ? "FAILED" : "COMMITTED",
    operations_error: mode === "v5-failed" ? "后置操作重试耗尽" : null,
    operations_result:
      mode === "v5-failed"
        ? null
        : {
            novelty: {
              result: "OLD",
              reference_ids: ["E14"],
              reason: "Covered",
            },
            delta_candidates: [],
            watch_actions: [],
            pin_actions: [],
          },
    novelty: value("OLD"),
    policy_hit: value(true),
    references: [],
    policies: [policy],
    expert_trade: {
      state: "NOT_APPLICABLE",
      value: null,
      reason: "NOT_APPLICABLE",
    },
    expert_trade_evaluated: {
      state: "NOT_APPLICABLE",
      value: null,
      reason: "NOT_APPLICABLE",
    },
    direction: value("NO_TRADE"),
    prior_expectation: {
      state: "NOT_APPLICABLE",
      value: null,
      reason: "NOT_APPLICABLE",
    },
    expectation_delta: {
      state: "NOT_APPLICABLE",
      value: null,
      reason: "NOT_APPLICABLE",
    },
    timing,
    attempts: page([]),
    reasoning: {
      novelty: noReason,
      policy: resource(reasonRef),
      expert_trade: noReason,
      trade: resource(reasonRef),
    },
  } as never);
}
runtime.api.request = async (name) =>
  ({
    data:
      name === "Candidates"
        ? resource(
            page([
              {
                candidate_id: "candidate-1",
                originating_node: "W1_R3",
                proposition: "Synthetic qualification newly completed.",
                assertion_state: "ACTUAL",
                occurrence_date: null,
              },
            ]),
          )
        : name === "Content"
          ? resource({
              content: reasonRef,
              chunk_index: 0,
              text: reasoningText,
              next_cursor: null,
              complete: true,
            })
          : resource(details),
    meta: { view_id: "fixture-view" },
  }) as never;
createRoot(document.getElementById("root")!).render(
  <RuntimeProvider runtime={runtime}>
    <main className="p-4">
      <CaseDetails ticker="MU" caseId="fixture-case" view="fixture-view" />
    </main>
  </RuntimeProvider>,
);
