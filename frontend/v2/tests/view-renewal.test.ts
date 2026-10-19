import { afterEach, beforeEach, expect, it, vi } from "vitest";

const fixture = vi.hoisted(() => ({
  effects: [] as (() => void | (() => void))[],
  runtime: {} as Record<string, unknown>,
  data: {} as Record<string, unknown>,
}));
vi.mock("react", async (original) => ({
  ...(await original<typeof import("react")>()),
  useRef: (value: unknown) => ({ current: value }),
  useEffect: (effect: () => void | (() => void)) => fixture.effects.push(effect),
}));
vi.mock("@tanstack/react-query", async (original) => ({
  ...(await original<typeof import("@tanstack/react-query")>()),
  useQuery: () => ({ data: fixture.data }),
}));
vi.mock("../src/core/runtime", () => ({ useRuntime: () => fixture.runtime }));
import { usePageContext } from "../src/core/page-query";

function setup() {
  const key = ["owner", "MU", "POLICIES", "context", "ALL"];
  const cache = new Map<string, unknown>();
  const expires = new Date(Date.now() + 65_000).toISOString();
  fixture.data = { data: { view_id: "old", expires_at: expires } };
  cache.set(JSON.stringify(key), fixture.data);
  const active = {
    isActive: () => true,
    queryKey: ["owner", "pages", "Policies", "/tickers/MU/policies?view_id=old", "old"],
    state: { data: { pages: [1, 2, 3, 4, 5], pageParams: [undefined, "old-cursor"] } },
  };
  const request = vi.fn(async (name: string) => name === "ReadContext"
    ? { data: { view_id: "new", expires_at: new Date(Date.now() + 900_000).toISOString() } }
    : { data: { state: "AVAILABLE", data: { items: ["first"], next_cursor: "new-cursor" } } });
  const setQueryData = vi.fn((k: unknown[], value: unknown) => cache.set(JSON.stringify(k), value));
  fixture.runtime = { scope: "owner", api: { request }, query: {
    getQueryData: (k: unknown[]) => cache.get(JSON.stringify(k)), setQueryData,
    getQueryCache: () => ({ findAll: () => [active] }), refetchQueries: async () => {},
  } };
  const listeners = new Map<string, () => void>();
  const document = { visibilityState: "visible", addEventListener: (name: string, fn: () => void) => listeners.set(name, fn),
    removeEventListener: (name: string) => listeners.delete(name) };
  vi.stubGlobal("document", document);
  return { request, setQueryData, document, listeners, cache, key };
}
beforeEach(() => { vi.useFakeTimers(); fixture.effects = []; });
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

it("refreshes only the first page under a new view without replaying old cursors", async () => {
  const f = setup();
  await usePageContext("MU", "POLICIES", "ALL").refetch();
  expect(f.request.mock.calls.map((c) => c[0])).toEqual(["ReadContext", "Policies"]);
  const pages = f.setQueryData.mock.calls.find((c) => c[0][1] === "pages")!;
  expect(pages[0][3]).toContain("view_id=new");
  expect((pages[1] as { pages: unknown[] }).pages).toHaveLength(1);
});

it("defers renewal while hidden and renews on return before using the expired view", async () => {
  const f = setup();
  usePageContext("MU", "POLICIES", "ALL");
  const cleanup = fixture.effects[0]();
  f.document.visibilityState = "hidden";
  await vi.advanceTimersByTimeAsync(70_000);
  expect(f.request).not.toHaveBeenCalled();
  f.document.visibilityState = "visible";
  f.listeners.get("visibilitychange")!();
  await vi.advanceTimersByTimeAsync(0);
  expect(f.request.mock.calls.map((c) => c[0])).toEqual(["ReadContext", "Policies"]);
  cleanup?.();
});

it("does not overwrite a context replaced by another completed refresh", async () => {
  const f = setup();
  let resume!: (value: unknown) => void;
  f.request.mockImplementationOnce(() => new Promise((resolve) => { resume = resolve; }) as never);
  const pending = usePageContext("MU", "POLICIES", "ALL").refetch();
  const newer = { data: { view_id: "newer" } };
  f.cache.set(JSON.stringify(f.key), newer);
  resume({ data: { view_id: "late" } });
  await pending;
  expect(f.cache.get(JSON.stringify(f.key))).toBe(newer);
});
