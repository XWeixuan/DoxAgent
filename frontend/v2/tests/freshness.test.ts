import { describe, expect, it } from "vitest";
import type { Meta } from "@contract";
import { staleMessage } from "../src/core/api";

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
