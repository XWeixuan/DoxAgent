import { describe, expect, it, vi } from "vitest";
import type { Period } from "@contract";
import type { ApiClient, Endpoints } from "../src/core/api";
import { readOverviewContext } from "../src/pages/overview/context";
import {
  overviewGatewayReady,
  overviewSummaryKey,
} from "../src/pages/overview/data";
import { context } from "./fixtures/wire";

function reply(period: Period, trading: boolean): Endpoints["ReadContext"] {
  const data = context(period);
  data.clock.is_trading_day = {
    state: "AVAILABLE",
    value: trading,
    reason: null,
  };
  data.period_options = data.period_options.map((option) =>
    option.period === "CURRENT_TRADING_DAY"
      ? {
          ...option,
          selectable: trading,
          reason: trading ? null : "NON_TRADING_DAY",
        }
      : option,
  );
  return {
    data,
    meta: {
      contract_version: "2.0.0-draft.2",
      workflow_generation: "V2",
      request_id: "test",
      view_id: data.view_id,
      scope_key: "overview",
      as_of: "2026-09-27T17:25:00Z",
      representation_revision: "1",
      freshness: "FRESH",
      refresh_error: null,
    },
  };
}

describe("Overview calendar bootstrap", () => {
  it("waits for the selected period before starting a Gateway probe", () => {
    expect(
      overviewGatewayReady("CURRENT_TRADING_DAY", "PREVIOUS_TRADING_DAY", true),
    ).toBe(false);
    expect(
      overviewGatewayReady(
        "PREVIOUS_TRADING_DAY",
        "PREVIOUS_TRADING_DAY",
        false,
      ),
    ).toBe(true);
    expect(
      overviewGatewayReady("CURRENT_TRADING_DAY", "CURRENT_TRADING_DAY", true),
    ).toBe(true);
  });

  it("starts a new Gateway/status query when the calendar default resolves to another view", () => {
    for (const kind of ["gateway", "status"] as const) {
      expect(
        overviewSummaryKey(
          "owner",
          kind,
          "CURRENT_TRADING_DAY",
          true,
          "view-before",
        ),
      ).not.toEqual(
        overviewSummaryKey(
          "owner",
          kind,
          "PREVIOUS_TRADING_DAY",
          false,
          "view-after",
        ),
      );
      expect(
        overviewSummaryKey(
          "owner",
          kind,
          "PREVIOUS_TRADING_DAY",
          false,
          "view-before",
        ),
      ).not.toEqual(
        overviewSummaryKey(
          "owner",
          kind,
          "PREVIOUS_TRADING_DAY",
          false,
          "view-after",
        ),
      );
    }
  });

  it.each(["OPEN", "MANUAL"] as const)(
    "uses previous session on closed days for %s",
    async (refresh) => {
      const request = vi
        .fn()
        .mockResolvedValue(reply("PREVIOUS_TRADING_DAY", false));
      const api = { request } as unknown as Pick<ApiClient, "request">;
      const result = await readOverviewContext(
        api,
        "CURRENT_TRADING_DAY",
        refresh,
        undefined,
        true,
      );
      expect(result.data.period?.selected).toBe("PREVIOUS_TRADING_DAY");
      expect(request).toHaveBeenCalledTimes(1);
      expect(request.mock.calls[0][1]).toContain("period=PREVIOUS_TRADING_DAY");
      expect(request.mock.calls[0][1]).toContain(`refresh=${refresh}`);
    },
  );

  it("keeps the current session on a trading day", async () => {
    const request = vi
      .fn()
      .mockResolvedValueOnce(reply("PREVIOUS_TRADING_DAY", true))
      .mockResolvedValueOnce(reply("CURRENT_TRADING_DAY", true));
    const result = await readOverviewContext(
      { request } as unknown as Pick<ApiClient, "request">,
      "CURRENT_TRADING_DAY",
      "OPEN",
    );
    expect(result.data.period?.selected).toBe("CURRENT_TRADING_DAY");
    expect(request).toHaveBeenCalledTimes(2);
  });

  it("does not replace an explicitly selected closed current day", async () => {
    const request = vi
      .fn()
      .mockResolvedValue(reply("PREVIOUS_TRADING_DAY", false));
    await expect(
      readOverviewContext(
        { request } as unknown as Pick<ApiClient, "request">,
        "CURRENT_TRADING_DAY",
        "OPEN",
      ),
    ).rejects.toThrow("当前语义日休市");
    expect(request).toHaveBeenCalledTimes(1);
  });

  it("does not hide an unavailable store or scope mismatch", async () => {
    const request = vi.fn().mockRejectedValue(new Error("STORE_UNAVAILABLE"));
    const api = { request } as unknown as Pick<ApiClient, "request">;
    await expect(
      readOverviewContext(api, "CURRENT_TRADING_DAY", "OPEN"),
    ).rejects.toThrow("STORE_UNAVAILABLE");
    request.mockResolvedValue(reply("CURRENT_TRADING_DAY", false));
    await expect(
      readOverviewContext(api, "CURRENT_TRADING_DAY", "OPEN"),
    ).rejects.toThrow("读取范围不一致");
  });
});
