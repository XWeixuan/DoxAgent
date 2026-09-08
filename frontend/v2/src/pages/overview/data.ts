import { useQuery } from "@tanstack/react-query";
import type { Period } from "@contract";
import { useRuntime } from "@/core/runtime";
import { queryString, type Endpoints } from "@/core/api";
import type { PendingCommand } from "@/core/operations";
export const periods = [
  ["PREVIOUS_TRADING_DAY", "前一交易日"],
  ["CURRENT_TRADING_DAY", "本交易日"],
  ["TRADING_DAYS_7", "7 天"],
  ["TRADING_DAYS_30", "30 天"],
] as const;
export const filtersSupported = true;
export function useOverview(period: Period, run: string, health: string) {
  const runtime = useRuntime();
  const { query, api, scope } = runtime;
  const contextKey = [scope, "context", "OVERVIEW", period];
  const readContext = async (
    refresh: "OPEN" | "MANUAL",
    signal?: AbortSignal,
  ) => {
    const response = await api.request(
      "ReadContext",
      "/read-context" + queryString({ page: "OVERVIEW", period, refresh }),
      { signal },
    );
    if (
      response.data.page !== "OVERVIEW" ||
      response.data.ticker !== null ||
      response.data.period?.selected !== period
    )
      throw new Error("读取范围不一致，请刷新页面。");
    return response;
  };
  const context = useQuery({
    queryKey: contextKey,
    queryFn: ({ signal }) => readContext("OPEN", signal),
  });
  const load = async <K extends "Status" | "Metrics" | "Tickers">(
    name: K,
    path: string,
    signal?: AbortSignal,
    cursor?: string,
  ) => {
    const current = query.getQueryData<Endpoints["ReadContext"]>(contextKey);
    if (!current) throw new Error("请先重试页面读取范围。");
    const view = current.data.view_id;
    const response = await api.request(
      name,
      path +
        queryString({
          view_id: view,
          ...(name === "Tickers"
            ? {
                limit: "20",
                cursor,
                run_state: filtersSupported && run !== "ALL" ? run : undefined,
                health:
                  filtersSupported && health !== "ALL" ? health : undefined,
              }
            : {}),
        }),
      { signal, view },
    );
    if (response.data.state === "ERROR")
      throw new Error("该模块暂时无法读取，请重试。");
    return response;
  };
  const status = useQuery({
    queryKey: [scope, "overview-status"],
    enabled: !!context.data,
    queryFn: ({ signal }) => load("Status", "/overview/status", signal),
  });
  const metrics = useQuery({
    queryKey: [scope, "overview-metrics", period],
    enabled: !!context.data,
    queryFn: ({ signal }) => load("Metrics", "/overview/metrics", signal),
  });
  const listKey = [scope, "overview-list", period, run, health];
  const list = useQuery({
    queryKey: listKey,
    enabled: !!context.data,
    queryFn: ({ signal }) => load("Tickers", "/overview/tickers", signal),
  });
  const capabilities = useQuery({
    queryKey: [scope, "capabilities"],
    queryFn: ({ signal }) =>
      api.request("Capabilities", "/capabilities", { signal }),
  });
  const principal = useQuery({
    queryKey: [scope, "principal"],
    queryFn: ({ signal }) => api.request("Principal", "/auth/me", { signal }),
  });
  const operations = useQuery<Record<string, PendingCommand>>({
    queryKey: [scope, "operations"],
    initialData: {},
    enabled: false,
  });
  async function refresh() {
    await query.cancelQueries({ queryKey: contextKey });
    const fresh = await readContext("MANUAL");
    if (scope !== runtime.scope) return;
    query.setQueryData(contextKey, fresh);
    await Promise.allSettled([
      status.refetch(),
      metrics.refetch(),
      list.refetch(),
    ]);
  }
  async function nextPage() {
    const old = list.data;
    const page = old?.data.data;
    if (!page?.next_cursor) return;
    const result = await load(
      "Tickers",
      "/overview/tickers",
      undefined,
      page.next_cursor,
    );
    const next = result.data.data;
    if (!next || next.snapshot_id !== page.snapshot_id)
      throw new Error("列表快照已变化，请刷新后继续。");
    if (query.getQueryData(listKey) !== old) return;
    query.setQueryData(listKey, {
      ...result,
      data: {
        ...result.data,
        data: {
          ...next,
          items: [
            ...page.items,
            ...next.items.filter(
              (row) =>
                !page.items.some(
                  (item) => item.state.ticker === row.state.ticker,
                ),
            ),
          ],
        },
      },
    });
  }
  return {
    context,
    status,
    metrics,
    list,
    capabilities,
    principal,
    operations,
    refresh,
    nextPage,
  };
}
