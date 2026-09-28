import type { Period } from "@contract";
import { queryString, type ApiClient } from "@/core/api";

export async function readOverviewContext(
  api: Pick<ApiClient, "request">,
  period: Period,
  refresh: "OPEN" | "MANUAL",
  signal?: AbortSignal,
  calendarDefault = false,
) {
  const read = async (selected: Period) => {
    const response = await api.request(
      "ReadContext",
      "/read-context" +
        queryString({ page: "OVERVIEW", period: selected, refresh }),
      { signal },
    );
    if (
      response.data.page !== "OVERVIEW" ||
      response.data.ticker !== null ||
      response.data.period?.selected !== selected
    )
      throw new Error("读取范围不一致，请刷新页面。");
    return response;
  };
  if (period !== "CURRENT_TRADING_DAY") return read(period);
  // Bootstrap selectable periods with a window that is valid on closed days.
  const previous = await read("PREVIOUS_TRADING_DAY");
  if (
    previous.data.period_options.find(
      (option) => option.period === "CURRENT_TRADING_DAY",
    )?.selectable === false
  ) {
    if (calendarDefault) return previous;
    throw new Error("当前语义日休市，请选择前一交易日或其他可用周期。");
  }
  return read(period);
}
