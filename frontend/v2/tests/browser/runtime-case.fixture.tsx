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
  node: "W1" | "W2",
  round: "R1" | "R2",
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
  summary: {
    title: value("Micron focused Runtime Case"),
    source: { name: "Synthetic News", kind: "api" },
  },
  failures: [],
  candidate_count: value(0),
  execution_count: value(0),
  messages: page([]),
  w1: resource({
    novelty: value("OLD"),
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
    ]),
    rounds: [
      { round: "R1", attempt_count: 1, not_executed_reason: null },
      { round: "R2", attempt_count: 1, not_executed_reason: null },
    ],
  }),
  w2: resource({
    skipped: mode === "skipped",
    policy_hit: value(!empty),
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
    ],
  }),
  w3: noReason,
} as unknown as CaseDetail;
const runtime = createRuntime(createTestAuth());
runtime.api.request = async (name) =>
  ({
    data:
      name === "Content"
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
