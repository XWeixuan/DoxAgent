import { afterEach, expect, it, vi } from "vitest";
import { ApiClient, retryAfterMs } from "../src/core/api";
import { capabilities } from "./fixtures/wire";
const auth = {
  token: () => "test",
  refresh: async () => false,
  invalidate: () => {},
};
const success = () =>
  new Response(
    JSON.stringify({
      data: capabilities,
      meta: {
        contract_version: "2.0.0-draft.2",
        workflow_generation: "V2",
        request_id: "r",
        view_id: null,
        scope_key: "s",
        as_of: "2026-09-04T14:00:00Z",
        representation_revision: "1",
        freshness: "FRESH",
        refresh_error: null,
      },
    }),
  );
const failure = (retryable = true, code = "SERVICE_BUSY", after?: string) =>
  new Response(
    JSON.stringify({
      error: { code, message: "raw", retryable, request_id: "r", fields: [] },
    }),
    { status: 503, headers: after ? { "Retry-After": after } : {} },
  );
afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});
it("retries a transient GET in the same view and URL", async () => {
  vi.useFakeTimers();
  vi.spyOn(Math, "random").mockReturnValue(0);
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(failure())
    .mockResolvedValueOnce(success());
  const pending = new ApiClient(auth, fetcher).request(
    "Capabilities",
    "/capabilities?view_id=frozen",
  );
  await vi.advanceTimersByTimeAsync(2000);
  expect((await pending).data).toEqual(capabilities);
  expect(fetcher).toHaveBeenCalledTimes(2);
  expect(fetcher.mock.calls[0][0]).toBe(fetcher.mock.calls[1][0]);
});
it("caps recovery at two retries", async () => {
  vi.useFakeTimers();
  vi.spyOn(Math, "random").mockReturnValue(0);
  const fetcher = vi.fn().mockImplementation(() => Promise.resolve(failure()));
  const pending = new ApiClient(auth, fetcher).request(
    "Capabilities",
    "/capabilities",
  );
  const check = expect(pending).rejects.toMatchObject({
    code: "SERVICE_BUSY",
    retryable: true,
    requestId: "r",
  });
  await vi.advanceTimersByTimeAsync(6000);
  await check;
  expect(fetcher).toHaveBeenCalledTimes(3);
});
it.each([
  ["GET", "/capabilities", false, "SERVICE_BUSY"],
  ["POST", "/capabilities", true, "SERVICE_BUSY"],
  ["GET", "/overview/gateway-status?view_id=v", true, "SERVICE_BUSY"],
  ["GET", "/capabilities", true, "CONTENT_UNAVAILABLE"],
])("does not replay %s %s", async (method, path, retryable, code) => {
  const fetcher = vi.fn().mockResolvedValue(failure(retryable, code));
  await expect(
    new ApiClient(auth, fetcher).request("Capabilities", path, { method }),
  ).rejects.toMatchObject({ code });
  expect(fetcher).toHaveBeenCalledTimes(1);
});
it.each(["abort", "session"])("cancels retry wait on %s", async (cause) => {
  vi.useFakeTimers();
  const fetcher = vi.fn().mockResolvedValue(failure());
  const client = new ApiClient(auth, fetcher),
    controller = new AbortController();
  const pending = client.request("Capabilities", "/capabilities", {
    signal: controller.signal,
  });
  const check = expect(pending).rejects.toMatchObject({ name: "AbortError" });
  await vi.advanceTimersByTimeAsync(10);
  if (cause === "abort") controller.abort();
  else client.reset();
  await check;
  await vi.advanceTimersByTimeAsync(10000);
  expect(fetcher).toHaveBeenCalledTimes(1);
});
it("honors recovery window and parses Retry-After seconds/date", async () => {
  const fetcher = vi.fn().mockResolvedValue(failure(true, "STORE_BUSY", "21"));
  await expect(
    new ApiClient(auth, fetcher).request("Capabilities", "/capabilities"),
  ).rejects.toMatchObject({ code: "STORE_BUSY" });
  expect(fetcher).toHaveBeenCalledTimes(1);
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-10-02T00:00:00Z"));
  expect(retryAfterMs("2")).toBe(2000);
  expect(retryAfterMs("Fri, 02 Oct 2026 00:00:04 GMT")).toBe(4000);
  expect(retryAfterMs("invalid")).toBeUndefined();
});
