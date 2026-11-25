import { describe, expect, it } from "vitest";
import type { GlobalSourceCard, SourceCatalog } from "@contract";
import {
  catalogCardStatus,
  catalogExport,
  catalogMatches,
} from "../src/pages/source-catalog";
import { value } from "./fixtures/wire";

function source(overrides: Partial<GlobalSourceCard> = {}): GlobalSourceCard {
  return {
    source_id: "internal-source-id",
    name: "Example News Full Name",
    short_name: "Example News",
    description: "Example newsroom; monitors company news.",
    configured_intervals_seconds: [20, 30],
    poll_counts: { succeeded: 1, partial: 0, failed: 0, unknown: 0 },
    enabled: true,
    delivery_mode: "production",
    acquisition_mode: "by_search",
    classification: {
      applicability: "GLOBAL",
      sector_ids: [],
      listing: { primary: ["HTTP"], fallback: ["CHROME_CDP"] },
      body: { primary: ["CHROME_CDP"], fallback: [] },
    },
    entry_points: [
      { label: "News", url: "https://example.com/news/{ticker}", ticker: "MU" },
    ],
    subscriptions: Array.from({ length: 12 }, (_, i) => ({
      ticker: `T${i}`,
      binding_id: `hidden-binding-${i}`,
      enabled: i !== 11,
    })),
    state_counts: {
      running: 0,
      sleeping: 11,
      abnormal: 0,
      disabled: 1,
      unknown: 0,
    },
    errors: [],
    metrics: {
      last_success_at: value("2026-11-26T12:00:00Z"),
      published_today: value(0),
      mean_e2e_seconds: 0,
      p95_e2e_seconds: 0,
      e2e_samples: 1,
      mean_capture_seconds: null,
      capture_samples: 0,
      refresh_seconds: 30,
      refresh_samples: 19,
      body_ratio: 0,
      body_total: 1,
      body_full: 0,
      article_samples: 987654,
      body_applicable: true,
      poll_expected: 20,
      poll_succeeded: 0,
    },
    ...overrides,
  };
}

describe("source catalog local status filter", () => {
  it("uses the card's outcome, including partial failure, without inventing a state for unknown or inactive sources", () => {
    const normal = source();
    const partial = source({
      poll_counts: { succeeded: 1, partial: 0, failed: 1, unknown: 0 },
    });
    const partialOnly = source({
      poll_counts: { succeeded: 0, partial: 1, failed: 0, unknown: 0 },
    });
    const failed = source({
      poll_counts: { succeeded: 0, partial: 0, failed: 1, unknown: 0 },
    });
    const unknown = source({
      poll_counts: { succeeded: 0, partial: 0, failed: 0, unknown: 1 },
    });
    const inactive = [
      source({ enabled: false }),
      source({ subscriptions: [] }),
      unknown,
    ];
    const rows = [normal, partial, partialOnly, failed, ...inactive];
    expect(
      rows.filter((s) =>
        catalogMatches(s, new URLSearchParams("status=NORMAL")),
      ),
    ).toEqual([normal]);
    expect(
      rows.filter((s) =>
        catalogMatches(s, new URLSearchParams("status=FAILED")),
      ),
    ).toEqual([partial, partialOnly, failed]);
    expect(catalogCardStatus(unknown)).toBe("暂无记录");
    expect(
      rows.filter((s) => catalogMatches(s, new URLSearchParams())),
    ).toHaveLength(7);
  });

  it("intersects status with the existing filters and keeps a sleeping source's successful outcome", () => {
    expect(
      catalogMatches(
        source(),
        new URLSearchParams(
          "status=NORMAL&method=HTTP&distribution=by_search&q=example",
        ),
      ),
    ).toBe(true);
    expect(
      catalogMatches(source(), new URLSearchParams("status=NORMAL&method=RSS")),
    ).toBe(false);
    expect(
      catalogMatches(
        source(),
        new URLSearchParams("status=FAILED&method=HTTP"),
      ),
    ).toBe(false);
  });
});

describe("source catalog JSON snapshot", () => {
  it("exports only displayed groups, including all collapsible content, while omitting private API fields", () => {
    const selected = source({
      errors: [{ ticker: "MU", code: "HTTP_403", message: "Blocked" }],
    });
    const hidden = source({
      name: "Hidden source",
      source_id: "hidden-source",
    });
    const data: SourceCatalog = {
      snapshot_at: "2026-11-26T12:00:00Z",
      semantic_day: "2026-11-26",
      sectors: [],
      sources: [selected, hidden],
    };
    const output = catalogExport(
      data,
      [{ name: "全局通用消息源", sources: [selected] }],
      new URLSearchParams("q=example&status=NORMAL"),
    );
    const exported = output.groups[0].sources[0];
    expect(output.displayed_source_count).toBe(1);
    expect(output.total_source_count).toBe(2);
    expect(output.filters).toMatchObject({ search: "example", status: "正常" });
    expect(exported).toMatchObject({
      name: selected.name,
      short_name: selected.short_name,
      status: "成功",
      target_interval: "20–30s",
      subscription_status: "休眠11 禁用1",
    });
    expect(exported.subscriptions).toHaveLength(12);
    expect(exported.subscriptions.at(-1)).toEqual({
      ticker: "T11",
      enabled: false,
    });
    expect(exported.entry_points).toEqual(selected.entry_points);
    expect(exported.listing).toEqual({
      primary: ["HTTP"],
      fallback: ["Chrome/CDP"],
    });
    expect(exported.errors).toEqual([
      { ticker: "MU", code: "HTTP_403", message: "Blocked" },
    ]);
    expect(exported.metrics).toContainEqual(
      expect.objectContaining({ name: "当天新增", value: "0" }),
    );
    expect(exported.metrics).toContainEqual(
      expect.objectContaining({ name: "实时首发端到端", value: "0s · 1篇" }),
    );
    expect(exported.metrics).toContainEqual(
      expect.objectContaining({
        name: "正文补全成功率",
        value: "0.0% · 0/1篇",
      }),
    );
    const json = JSON.stringify(output);
    for (const forbidden of [
      "internal-source-id",
      "hidden-binding-",
      "Hidden source",
      "article_samples",
      "987654",
      "semantic_day",
      "poll_counts",
      "source_id",
    ])
      expect(json).not.toContain(forbidden);
  });

  it("preserves no-data and historical notes only when those notes are displayed", () => {
    const s = source();
    s.metrics = {
      ...s.metrics,
      mean_e2e_seconds: null,
      e2e_samples: 0,
      body_ratio: null,
      body_total: 0,
      last_historical: { at: "2026-11-01T12:00:00Z", e2e_seconds: 33 },
      last_historical_body: { at: "2026-11-01T12:00:00Z", full: false },
    };
    const data: SourceCatalog = {
      snapshot_at: "2026-11-26T12:00:00Z",
      semantic_day: "2026-11-26",
      sectors: [],
      sources: [s],
    };
    const card = () =>
      catalogExport(
        data,
        [{ name: "全局通用消息源", sources: [s] }],
        new URLSearchParams(),
      ).groups[0].sources[0];
    expect(card().metrics).toContainEqual(
      expect.objectContaining({
        name: "实时首发端到端",
        value: "暂无近期数据",
      }),
    );
    expect(card().historical_notes).toEqual([
      expect.stringContaining("最近历史发布：33s"),
      expect.stringContaining("历史正文：未完整"),
    ]);
    s.metrics.e2e_samples = 1;
    s.metrics.body_total = 1;
    expect(card().historical_notes).toEqual([]);
  });
});
