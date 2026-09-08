import { afterEach, expect, it, vi } from "vitest";
import type { Operation, Meta, ContentRef, ContentChunk } from "@contract";
import { ApiFailure } from "../src/core/api";
import { createRuntime } from "../src/core/runtime";
import { Operations, type PendingCommand } from "../src/core/operations";
import { appendContent } from "../src/core/content";
import { createTestAuth } from "./fixtures/auth";
import { rows, resource } from "./fixtures/wire";
const meta: Meta = {
  contract_version: "2.0.0-draft.2",
  workflow_generation: "V2",
  request_id: "test",
  view_id: null,
  scope_key: "test",
  as_of: "2026-09-04T14:00:00Z",
  representation_revision: "1",
  freshness: "FRESH",
  refresh_error: null,
};
const row = rows()[0];
const operation: Operation = {
  operation_id: "op",
  ticker: "MU",
  kind: "PAUSE",
  status: "SUCCEEDED",
  created_at: meta.as_of,
  completed_at: meta.as_of,
  outcome: "PAUSED",
  initialization_id: null,
  ticker_state: { ...row.state, run_state: "PAUSED" },
  error: null,
  retry_after_seconds: 2,
};
const readPending = (runtime: ReturnType<typeof createRuntime>) =>
  runtime.query.getQueryData<Record<string, PendingCommand>>([
    runtime.scope,
    "operations",
  ])!.MU;
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});
it("unknown submission retry retains exact idempotency key and original ETag", async () => {
  const runtime = createRuntime(createTestAuth()),
    operations = new Operations(runtime);
  const request = vi
    .spyOn(runtime.api, "request")
    .mockRejectedValueOnce(new TypeError("Network lost"))
    .mockResolvedValueOnce({ data: operation, meta });
  await operations.submit({ ticker: "MU", kind: "PAUSE", state: row.state });
  const pending = readPending(runtime);
  expect(pending.phase).toBe("unknown");
  await operations.resume(pending);
  expect(request.mock.calls[0][2]?.key).toBe(request.mock.calls[1][2]?.key);
  expect(request.mock.calls[0][2]?.etag).toBe(request.mock.calls[1][2]?.etag);
  expect(readPending(runtime).phase).toBe("done");
});
it("412 obtains current state but requires a new explicit submit", async () => {
  const runtime = createRuntime(createTestAuth()),
    operations = new Operations(runtime);
  const request = vi
    .spyOn(runtime.api, "request")
    .mockRejectedValueOnce(new ApiFailure("REVISION_CONFLICT", "conflict", 412))
    .mockResolvedValueOnce({
      data: resource({ ...row.state, control_etag: "new-etag" }),
      meta,
    })
    .mockResolvedValueOnce({ data: operation, meta });
  await operations.submit({ ticker: "MU", kind: "PAUSE", state: row.state });
  expect(request).toHaveBeenCalledTimes(2);
  expect(readPending(runtime).phase).toBe("conflict");
  await operations.resume(readPending(runtime));
  expect(request.mock.calls[2][2]?.etag).toBe("new-etag");
  expect(request.mock.calls[2][2]?.key).not.toBe(request.mock.calls[0][2]?.key);
});
it("operation timeout remains pending, and hidden pages perform no receipt polling", async () => {
  vi.useFakeTimers();
  vi.stubGlobal("document", { hidden: true });
  const runtime = createRuntime(createTestAuth()),
    operations = new Operations(runtime);
  const request = vi
    .spyOn(runtime.api, "request")
    .mockResolvedValue({
      data: { ...operation, status: "ACCEPTED", outcome: null },
      meta,
    });
  const pending = operations.submit({
    ticker: "MU",
    kind: "PAUSE",
    state: row.state,
  });
  await vi.advanceTimersByTimeAsync(300_001);
  await pending;
  expect(request).toHaveBeenCalledTimes(1);
  expect(readPending(runtime).phase).toBe("waiting");
  expect(readPending(runtime).message).toContain("仍在处理");
});
it("a completed mutation respects loaded server filter scopes", async () => {
  const runtime = createRuntime(createTestAuth()),
    operations = new Operations(runtime);
  const key = [
    runtime.scope,
    "overview-list",
    "PREVIOUS_TRADING_DAY",
    "RUNNING",
    "ALL",
  ];
  runtime.query.setQueryData(key, {
    data: resource({
      items: [row],
      next_cursor: null,
      has_more: false,
      snapshot_id: "s",
      limit: 20,
    }),
    meta,
  });
  vi.spyOn(runtime.api, "request").mockResolvedValue({ data: operation, meta });
  await operations.submit({ ticker: "MU", kind: "PAUSE", state: row.state });
  expect(JSON.stringify(runtime.query.getQueryData(key))).not.toContain(
    '"ticker":"MU"',
  );
});
it("foreign operation receipt cannot mutate the target cache", async () => {
  const runtime = createRuntime(createTestAuth()),
    operations = new Operations(runtime);
  vi.spyOn(runtime.api, "request").mockResolvedValue({
    data: { ...operation, ticker: "NVDA" },
    meta,
  });
  await operations.submit({ ticker: "MU", kind: "PAUSE", state: row.state });
  expect(readPending(runtime).phase).toBe("unknown");
});
it("logout clears caches and isolates a later sign-in under a new generation", () => {
  const base = createTestAuth(),
    listeners = new Set<() => void>();
  const auth = {
    ...base,
    subscribe: (listener: () => void) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
  };
  const runtime = createRuntime(auth),
    original = runtime.scope;
  runtime.query.setQueryData([original, "sensitive"], {
    secret: "only in old scope",
  });
  auth.identity = null;
  listeners.forEach((listener) => listener());
  expect(runtime.query.getQueryCache().getAll()).toHaveLength(0);
  auth.identity = base.identity;
  listeners.forEach((listener) => listener());
  expect(runtime.scope).not.toBe(original);
});
it("content chunks retain immutable identity, ordering and explicit completeness", () => {
  const ref: ContentRef = {
    content_id: "content",
    content_type: "text/markdown",
    size_bytes: 2,
    sha256: "hash",
  };
  const first: ContentChunk = {
    content: ref,
    chunk_index: 0,
    text: "a",
    complete: false,
    next_cursor: "next",
  };
  const next: ContentChunk = {
    content: ref,
    chunk_index: 1,
    text: "b",
    complete: true,
    next_cursor: null,
  };
  expect(
    appendContent([first], next, ref)
      .map((chunk) => chunk.text)
      .join(""),
  ).toBe("ab");
  expect(() => appendContent([], next, ref)).toThrow();
  expect(() =>
    appendContent(
      [first],
      { ...next, content: { ...ref, sha256: "other" } },
      ref,
    ),
  ).toThrow();
});
