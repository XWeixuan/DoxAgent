// Synthetic UI contract fixture; no production API or authentication calls.
import { createRoot } from "react-dom/client";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import type { Period, MessageSummary } from "@contract";
import { createRuntime, RuntimeProvider } from "../../src/core/runtime";
import { createTestAuth } from "../fixtures/auth";
import * as wire from "../fixtures/wire";
import MessageBus from "../../src/pages/message-bus";
import "../../src/styles.css";
import "../../src/business.css";
import "../../src/refinement.css";

import { ApiFailure } from "../../src/core/api";

const runtime = createRuntime(createTestAuth());
const calls: string[] = [];
let serial = 0;
const control = { delay: 0, fail: false, reset: () => {} };
Object.assign(window, { busFixtureCalls: calls, busControl: control });
const body = {
  content_id: "body",
  content_type: "text/plain" as const,
  sha256: "0".repeat(64),
  size_bytes: 400,
};
const source = {
  source_id: "yahoo_finance_news",
  binding_id: "MU:yahoo_finance_news",
  name: "Yahoo Finance",
  kind: "crawler" as const,
};
const messages: MessageSummary[] = Array.from({ length: 60 }, (_, i) => ({
  standard_message_id: `m${60 - i}`,
  revision: 1,
  row_revision: 60 - i,
  title: wire.value(`Message ${60 - i}`),
  source,
  url: "https://example.com/news",
  source_published_at: "2026-10-30T14:00:00Z",
  collected_at: "2026-10-30T14:00:01Z",
  normalized_at: "2026-10-30T14:00:10Z",
  stream_published_at: new Date(
    Date.UTC(2026, 9, 30, 14, 0, 60 - i),
  ).toISOString(),
  stream_item_id: `s${60 - i}`,
  stream_offset: 60 - i,
  member_index: 0,
  exact_duplicate_count: wire.value(0),
  case: {
    case_id: null,
    status: null,
    initial_route: null,
    resolved_route: null,
    w3_status: null,
    route_group: "NOT_PROCESSED",
  },
  body,
}));
const originalFetch = window.fetch;
window.fetch = async (input, init) => {
  if (String(input).includes("/messages/events"))
    return new Response(
      new ReadableStream({
        start(controller) {
          control.reset = () =>
            controller.enqueue(
              new TextEncoder().encode("event: reset\ndata: {}\n\n"),
            );
          init?.signal?.addEventListener("abort", () => {
            try {
              controller.close();
            } catch {
              /* already closed */
            }
          });
        },
      }),
      { headers: { "content-type": "text/event-stream" } },
    );
  return originalFetch(input, init);
};
runtime.api.request = (async (name: string, path: string) => {
  calls.push(name + ":" + path);
  const query = new URL(path, location.origin).searchParams;
  const meta = { as_of: new Date().toISOString() };
  if (name === "ReadContext") {
    const period = query.get("period") as Period,
      context = wire.context("CURRENT_TRADING_DAY");
    return {
      meta,
      data: {
        ...context,
        view_id: `view-${++serial}`,
        page: "MESSAGE_BUS",
        ticker: "MU",
        expires_at: new Date(Date.now() + 15 * 60000).toISOString(),
        period: { ...context.period, selected: period },
        clock: {
          ...context.clock,
          next_minute_at: new Date(Date.now() + 60000).toISOString(),
        },
      },
    };
  }
  if (name === "BusDashboard") {
    if (control.delay) await new Promise((r) => setTimeout(r, control.delay));
    if (control.fail)
      throw new ApiFailure(
        "INTERNAL_ERROR",
        "数据处理失败",
        500,
        "fixture-request",
      );
    return {
      meta,
      data: wire.resource({
        metrics: {
          published_revisions: wire.metric("published_revisions", "0"),
          body_completion_success_ratio: wire.metric(
            "body_completion_success_ratio",
            "0",
          ),
          new_article_body: {
            total: 100,
            full: 84,
            pending: 6,
            failed: 10,
            ratio: 0.84,
            basis: "FIRST_OBSERVED_ARTICLE",
            legacy_excluded: true,
            network_attempts: null,
            network_succeeded: null,
            cache_reuses: null,
          },
        },
        counts: {
          total: 32,
          running: 2,
          sleeping: 30,
          disabled: 0,
          abnormal: 0,
          unknown: 0,
        },
        mean_e2e_seconds: 4.2,
        e2e_samples: 20,
        coverage: { expected: 20, succeeded: 17, ratio: 0.85 },
        sources: [
          {
            source,
            binding_version: 1,
            source_version: 1,
            publication_mode: "immediate",
            enabled: true,
            global_enabled: true,
            poll_status: "succeeded",
            health_group: "NORMAL",
            last_success_at: wire.value("2026-10-30T18:00:00Z"),
            last_published_count: wire.value(0),
            published_today: wire.value(0),
            last_poll_latency_seconds: wire.value(0.02),
            current_error: null,
            target_interval_seconds: 30,
            active_windows: [],
            next_target_at: wire.value(
              new Date(Date.now() + 20000).toISOString(),
            ),
            countdown_state: "CLOSED_SLEEP",
            recent: {
              coverage: { expected: 20, succeeded: 17, ratio: 0.85 },
              mean_e2e_seconds: 4.2,
              p95_e2e_seconds: 9.5,
              e2e_samples: 20,
              refresh_seconds: 31.2,
              body_total: 10,
              body_full: 8,
              body_ratio: 0.8,
            },
          },
        ],
      }),
    };
  }
  if (name === "Messages") {
    const start = Number(query.get("cursor")?.split(":").at(-1) ?? 0);
    const after = query.get("reading_after");
    const next = after
      ? messages.findIndex((m) => m.standard_message_id === after) + 1
      : start + 20;
    return {
      meta,
      data: wire.resource({
        messages: {
          items: messages.slice(start, start + 20),
          has_more: next < messages.length,
          next_cursor:
            next < messages.length ? `${query.get("view_id")}:${next}` : null,
          limit: 20,
          snapshot_id: query.get("view_id"),
        },
        stream_cursor: `stream-${query.get("view_id")}`,
      }),
    };
  }
  if (name === "Content")
    return {
      meta,
      data: wire.resource({
        content: body,
        chunk_index: 0,
        text: "A readable body paragraph. ".repeat(12),
        next_cursor: null,
        complete: true,
      }),
    };
  throw new Error("Unexpected fixture request: " + name);
}) as typeof runtime.api.request;
createRoot(document.getElementById("root")!).render(
  <RuntimeProvider runtime={runtime}>
    <MemoryRouter initialEntries={["/tickers/MU/message-bus"]}>
      <Routes>
        <Route path="/tickers/:ticker/message-bus" element={<MessageBus />} />
      </Routes>
    </MemoryRouter>
  </RuntimeProvider>,
);
