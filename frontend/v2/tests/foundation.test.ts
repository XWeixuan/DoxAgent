import { afterEach, describe, expect, it, vi } from "vitest";
import type { Meta, Operation, StreamEnvelope } from "@contract";
import { ApiClient, validateWire } from "../src/core/api";
import { createQueryClient } from "../src/core/cache";
import { createRuntime } from "../src/core/runtime";
import { Operations, trackingDelay } from "../src/core/operations";
import { valueText, decimal } from "../src/core/format";
import {
  SseParser,
  StreamCursor,
  nextMinuteDelay,
  validateGraphRevision,
} from "../src/core/updates";
import { createTestAuth } from "./fixtures/auth";
import {
  capabilities,
  clock,
  context,
  initialization,
  metrics,
  resource,
  rows,
  value,
} from "./fixtures/wire";
const meta: Meta = {
  contract_version: "2.0.0-draft.2",
  workflow_generation: "V2",
  request_id: "test",
  view_id: "view",
  scope_key: "scope",
  as_of: "2026-09-04T14:00:00Z",
  representation_revision: "1",
  freshness: "FRESH",
  refresh_error: null,
};
const wrap = <T>(data: T) => ({ data, meta });
const json = (data: unknown, status = 200) =>
  new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
afterEach(() => vi.useRealTimers());
describe("shared wire boundary", () => {
  it("validates every Overview fixture through generated shared schemas", () => {
    validateWire("Capabilities", wrap(capabilities));
    validateWire("ReadContext", wrap(context("PREVIOUS_TRADING_DAY")));
    validateWire(
      "Status",
      wrap(
        resource({
          clock,
          normal_tickers: value(3),
          blocked_tickers: value(1),
        }),
      ),
    );
    validateWire("Metrics", wrap(resource(metrics("PREVIOUS_TRADING_DAY"))));
    validateWire(
      "Tickers",
      wrap(
        resource({
          items: rows(),
          next_cursor: null,
          has_more: false,
          snapshot_id: "one",
          limit: 20,
        }),
      ),
    );
    validateWire("Initialization", wrap(resource(initialization("MU"))));
  });
  it("rejects V1 and malformed local data without accepting an unknown generation", () => {
    expect(() =>
      validateWire("Capabilities", {
        data: capabilities,
        meta: { ...meta, workflow_generation: "V1" },
      }),
    ).toThrow();
    expect(() =>
      validateWire(
        "Metrics",
        wrap(resource({ ...metrics("PREVIOUS_TRADING_DAY"), messages: 0 })),
      ),
    ).toThrow();
  });
  it("preserves explicit zero, unknown and exact decimal precision", () => {
    expect(valueText(value(0))).toBe("0");
    expect(
      valueText({ state: "NOT_RECORDED", value: null, reason: "NOT_RECORDED" }),
    ).toBe("未记录");
    expect(decimal("9007199254740993.125", 2)).toBe("9,007,199,254,740,993.13");
  });
});
describe("session cache and authenticated transport", () => {
  it("deduplicates reads and retains scope cache without focus or mount refetch", async () => {
    const query = createQueryClient(),
      fn = vi.fn(async () => 42);
    const options = { queryKey: ["user", "ticker", "period"], queryFn: fn };
    await Promise.all([query.fetchQuery(options), query.fetchQuery(options)]);
    await query.fetchQuery(options);
    expect(fn).toHaveBeenCalledTimes(1);
    expect(query.getDefaultOptions().queries).toMatchObject({
      refetchOnMount: false,
      refetchOnWindowFocus: false,
      refetchOnReconnect: false,
      retry: false,
      staleTime: Infinity,
      gcTime: Infinity,
    });
    query.clear();
  });
  it("rejects view mismatch and late response after session reset", async () => {
    const auth = createTestAuth();
    const wrongView = new ApiClient(auth, async () => json(wrap(capabilities)));
    await expect(
      wrongView.request("Capabilities", "/capabilities", { view: "other" }),
    ).rejects.toThrow("范围");
    let finish!: (res: Response) => void;
    const client = new ApiClient(
      auth,
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    const pending = client.request("Capabilities", "/capabilities");
    client.reset();
    finish(json(wrap(capabilities)));
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
  });
  it("uses one refresh for concurrent 401 and keeps 403 local", async () => {
    let token = "old";
    const invalidate = vi.fn(),
      refresh = vi.fn(async () => {
        await Promise.resolve();
        token = "new";
        return true;
      });
    const fetcher = vi.fn(async (_url: unknown, options?: RequestInit) =>
      new Headers(options?.headers).get("Authorization") === "Bearer old"
        ? json({}, 401)
        : json(wrap(capabilities)),
    );
    const client = new ApiClient(
      { token: () => token, refresh, invalidate },
      fetcher,
    );
    await Promise.all([
      client.request("Capabilities", "/capabilities"),
      client.request("Capabilities", "/capabilities"),
    ]);
    expect(refresh).toHaveBeenCalledTimes(1);
    const forbidden = new ApiClient(
      { token: () => token, refresh, invalidate },
      async () =>
        json({ error: { code: "FORBIDDEN", message: "forbidden" } }, 403),
    );
    await expect(
      forbidden.request("Capabilities", "/capabilities"),
    ).rejects.toMatchObject({ status: 403 });
    expect(invalidate).not.toHaveBeenCalled();
  });
});
describe("operation receipts", () => {
  it("never applies an ACCEPTED pause as completion; only SUCCEEDED changes caches", async () => {
    vi.useFakeTimers();
    const runtime = createRuntime(createTestAuth()),
      row = rows()[0];
    const key = [
      runtime.scope,
      "overview-list",
      "PREVIOUS_TRADING_DAY",
      "ALL",
      "ALL",
    ];
    runtime.query.setQueryData(
      key,
      wrap(
        resource({
          items: [row],
          next_cursor: null,
          has_more: false,
          snapshot_id: "s",
          limit: 20,
        }),
      ),
    );
    const op: Operation = {
      operation_id: "op",
      ticker: "MU",
      kind: "PAUSE",
      status: "ACCEPTED",
      created_at: meta.as_of,
      completed_at: null,
      outcome: null,
      initialization_id: null,
      ticker_state: row.state,
      error: null,
      retry_after_seconds: 2,
    };
    vi.spyOn(runtime.api, "request")
      .mockResolvedValueOnce(wrap(op))
      .mockResolvedValueOnce(
        wrap<Operation>({
          ...op,
          status: "SUCCEEDED",
          outcome: "PAUSED",
          ticker_state: { ...row.state, run_state: "PAUSED" },
        }),
      );
    const running = new Operations(runtime).submit({
      ticker: "MU",
      kind: "PAUSE",
      state: row.state,
    });
    await vi.advanceTimersByTimeAsync(1);
    expect(runtime.query.getQueryData<typeof wrap>(key)).toBeDefined();
    expect(JSON.stringify(runtime.query.getQueryData(key))).toContain(
      "RUNNING",
    );
    await vi.advanceTimersByTimeAsync(2000);
    await running;
    expect(JSON.stringify(runtime.query.getQueryData(key))).toContain("PAUSED");
    runtime.dispose();
    runtime.query.clear();
  });
  it("bounds server retry intervals", () => {
    expect(trackingDelay(0)).toBe(2000);
    expect(trackingDelay(100)).toBe(30000);
  });
});
describe("future-page update primitives remain separate from Overview", () => {
  it("uses real server minute boundaries and never catches up on return", () => {
    expect(
      nextMinuteDelay(
        "2026-09-04T14:01:00Z",
        "2026-09-04T14:00:40Z",
        1000,
        1000,
      ),
    ).toBe(20_000);
    expect(
      nextMinuteDelay(
        "2026-09-04T14:01:00Z",
        "2026-09-04T14:00:40Z",
        1000,
        81_000,
      ),
    ).toBe(60_000);
  });
  it("parses chunk boundaries, CRLF, heartbeat and multiline data", () => {
    const parser = new SseParser();
    expect(
      parser.push(
        ": keepalive\r\n\r\nid: c\r\nevent: message.delta\r\ndata: a\r",
      ),
    ).toEqual([]);
    expect(parser.push("\ndata: b\r\n\r\n")).toEqual([
      { id: "c", event: "message.delta", data: "a\nb" },
    ]);
  });
  it("deduplicates large sequence values and does not advance on a revision gap", () => {
    const cursor = new StreamCursor("scope", "view", "baseline"),
      apply = vi.fn();
    const envelope: StreamEnvelope<null> = {
      event_id: "one",
      stream_cursor: "next",
      scope_key: "scope",
      view_id: "view",
      sequence: "900719925474099300",
      emitted_at: meta.as_of,
      payload: null,
    };
    const frame = { id: "next", event: "message.delta", data: "" };
    expect(cursor.accept(frame, envelope, apply)).toBe(true);
    expect(cursor.accept(frame, envelope, apply)).toBe(false);
    expect(apply).toHaveBeenCalledTimes(1);
    expect(() =>
      cursor.accept(
        { ...frame, id: "bad" },
        {
          ...envelope,
          event_id: "two",
          stream_cursor: "bad",
          sequence: "900719925474099301",
        },
        () => {
          throw new Error("gap");
        },
      ),
    ).toThrow();
    expect(cursor.cursor).toBe("next");
    expect(() =>
      validateGraphRevision(3, {
        previous_graph_revision: 2,
        graph_revision: 4,
        upsert_cases: [],
        remove_case_ids: [],
        replace_nodes: [],
        replace_edges: [],
      }),
    ).toThrow();
  });
});
