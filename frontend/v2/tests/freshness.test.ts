import { describe, expect, it } from "vitest";
import type { Meta } from "@contract";
import { staleMessage, projectionWarning } from "../src/core/api";
import type { SourceProjection } from "@contract";

const meta: Meta = {
  contract_version: "2.0.0-draft.2",
  workflow_generation: "V2",
  request_id: "request",
  view_id: "view",
  scope_key: "scope",
  as_of: "2026-09-12T00:00:00Z",
  representation_revision: "1",
  freshness: "STALE",
  refresh_error: null,
};

describe("freshness warnings", () => {
  it("reports lag separately from HTTP refresh and historical gaps", () => {
    const row: SourceProjection = {
      source: "runtime",
      head: 20,
      checkpoint: 10,
      checked_at: "2026-11-12T00:01:00Z",
      source_event_at: "2026-11-12T00:00:00Z",
      oldest_pending_at: "2026-11-12T00:00:01Z",
      historical_gaps: 17204,
      error: null,
    };
    expect(projectionWarning([row], row.checked_at)).toContain("59 秒");
    expect(
      projectionWarning([{ ...row, checkpoint: 20 }], row.checked_at),
    ).toBe("");
    expect(
      projectionWarning([{ ...row, oldest_pending_at: null }], row.checked_at),
    ).toContain("尚未确认");
    expect(
      projectionWarning([{ ...row, checkpoint: 20 }], "2026-11-12T00:02:00Z"),
    ).toContain("尚未及时核验");
  });
  it("does not warn for saved or globally stale data without a refresh error", () => {
    expect(staleMessage(meta)).toBeNull();
    expect(staleMessage({ ...meta, freshness: "FRESH" })).toBeNull();
    expect(staleMessage()).toBeNull();
  });

  it("preserves explicit refresh errors regardless of freshness", () => {
    const refresh_error = {
      code: "SOURCE_FAILED",
      message: "数据源读取失败",
      retryable: true,
    } as NonNullable<Meta["refresh_error"]>;
    expect(staleMessage({ ...meta, refresh_error })).toBe("数据源读取失败");
    expect(staleMessage({ ...meta, freshness: "FRESH", refresh_error })).toBe(
      "数据源读取失败",
    );
    expect(
      staleMessage({
        ...meta,
        refresh_error: { ...refresh_error, message: " " },
      }),
    ).toBe("刷新失败，显示已保存的内容。");
  });

  it("clears the warning after a successful refresh even if the source remains stale", () => {
    const failed = {
      ...meta,
      refresh_error: {
        code: "SOURCE_FAILED",
        message: "刷新失败",
        retryable: true,
      } as NonNullable<Meta["refresh_error"]>,
    };
    expect(staleMessage(failed)).toBe("刷新失败");
    expect(staleMessage({ ...failed, refresh_error: null })).toBeNull();
  });
});
