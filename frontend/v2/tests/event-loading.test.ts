import { beforeEach, expect, it, vi } from "vitest";
import { createElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import type { EventSummary } from "@contract";
import { EventCard } from "../src/pages/events";

const hooks = vi.hoisted(() => ({ read: vi.fn(), pages: vi.fn() }));
vi.mock("@/core/page-query", () => ({
  useRead: hooks.read,
  usePageContext: vi.fn(),
  tickerPath: (ticker: string) => `/tickers/${ticker}`,
  id: encodeURIComponent,
}));
vi.mock("@/core/paged-query", () => ({ usePages: hooks.pages }));
vi.mock("@/components/state", () => ({
  Module: ({
    query,
    children,
  }: {
    query: { data?: { data: { data: unknown } } };
    children: (data: unknown) => ReactNode;
  }) => (query.data ? children(query.data.data.data) : null),
  Notice: ({ children }: { children: ReactNode }) => children,
}));

const summary = {
  event_key: "event-1",
  event_id: "E1",
  title: "已发布事件",
  library_snapshot_id: "snapshot-1",
  active_fact_count: 21,
} as EventSummary;
const detail = {
  library: { library_snapshot_id: "snapshot-1" },
  event: {
    event_id: "E1",
    is_important: true,
    canonical_summary: "事件正文",
    related_event_ids: [],
    derived_from_event_ids: [],
  },
  facts: { items: [], has_more: true, next_cursor: "facts-page-2" },
};
beforeEach(() => {
  hooks.read.mockReset().mockReturnValue({ data: { data: { data: detail } } });
  hooks.pages
    .mockReset()
    .mockReturnValue({
      hasNextPage: false,
      isFetching: false,
      error: null,
      fetchNextPage: vi.fn(),
    });
});

it("loads details and remaining facts while collapsed, displaying importance without opening", () => {
  const html = renderToStaticMarkup(
    createElement(EventCard, {
      ticker: "MU",
      summary,
      open: false,
      onOpen: vi.fn(),
    }),
  );
  expect(hooks.read).toHaveBeenCalledWith(
    "Event",
    "/tickers/MU/event-library/snapshots/snapshot-1/events/E1?limit=20",
  );
  expect(hooks.pages).toHaveBeenCalledWith(
    "Facts",
    "/tickers/MU/event-library/snapshots/snapshot-1/events/E1/facts?limit=20",
    expect.any(Function),
    undefined,
    detail.facts,
  );
  expect(html).toContain('aria-expanded="false"');
  expect(html).toContain('class="event-important">重要');
  expect(html).toContain('hidden=""');
  expect(html).toContain("事件正文");
  expect(html).not.toContain("更多事实");
});

it("loads each newly listed event by its own snapshot and does not fabricate importance before its detail arrives", () => {
  hooks.read.mockReturnValue({});
  const html = renderToStaticMarkup(
    createElement(EventCard, {
      ticker: "MU",
      summary: {
        ...summary,
        event_id: "E21",
        library_snapshot_id: "snapshot-filtered",
      },
      open: false,
      onOpen: vi.fn(),
    }),
  );
  expect(hooks.read).toHaveBeenCalledWith(
    "Event",
    "/tickers/MU/event-library/snapshots/snapshot-filtered/events/E21?limit=20",
  );
  expect(html).toContain("已发布事件");
  expect(html).not.toContain('class="event-important"');
  expect(hooks.pages).not.toHaveBeenCalled();
});
