import { useQuery } from "@tanstack/react-query";
import type {
  PageKind,
  Period,
  Resource,
  Response,
  ReadContext,
} from "@contract";
import { useRuntime } from "./runtime";
import { ApiFailure, queryString, type Endpoints } from "./api";

export function usePageContext(
  ticker: string,
  page: PageKind,
  period?: Period,
) {
  const { api, scope, query: cache } = useRuntime();
  const key = [scope, ticker, page, "context", period];
  const result = useQuery({
    queryKey: key,
    queryFn: ({ signal }) =>
      api.request(
        "ReadContext",
        "/read-context" + queryString({ ticker, page, period }),
        { signal },
      ),
  });
  return {
    ...result,
    refetch: async () => {
      const next = await api.request(
        "ReadContext",
        "/read-context" +
          queryString({ ticker, page, period, refresh: "MANUAL" }),
      );
      const old = result.data?.data.view_id,
        view = next.data.view_id;
      const otherContexts = cache
        .getQueryCache()
        .findAll()
        .filter(
          (q) =>
            q.isActive() &&
            q.queryKey[0] === scope &&
            q.queryKey[1] === ticker &&
            q.queryKey[2] === page &&
            q.queryKey[3] === "context" &&
            q.queryKey[4] !== period,
        );
      const contextUpdates = await Promise.all(
        otherContexts.map(async (q) => ({
          key: q.queryKey,
          old: (q.state.data as Response<ReadContext>).data.view_id,
          data: await api.request(
            "ReadContext",
            "/read-context" +
              queryString({
                ticker,
                page,
                period: q.queryKey[4] as Period | undefined,
                refresh: "MANUAL",
              }),
          ),
        })),
      );
      const views = new Map<string, string>(old ? [[old, view]] : []);
      for (const c of contextUpdates) views.set(c.old, c.data.data.view_id);
      const active = cache
        .getQueryCache()
        .findAll()
        .filter(
          (q) =>
            q.isActive() &&
            q.queryKey[0] === scope &&
            ["read", "pages"].includes(String(q.queryKey[1])) &&
            !(
              q.queryKey[1] === "pages" &&
              (q.state.data as { pageParams?: unknown[] } | undefined)
                ?.pageParams?.[0]
            ) &&
            String(q.queryKey[3]).startsWith(tickerPath(ticker) + "/"),
        );
      const reads = await Promise.all(
        active.map(async (q) => {
          const name = q.queryKey[2] as keyof Endpoints,
            path = String(q.queryKey[3]);
          const priorView = q.queryKey[4] as string | undefined;
          const expected = priorView
            ? (views.get(priorView) ?? priorView)
            : undefined;
          const updated =
            priorView && expected !== priorView
              ? path.replace(
                  `view_id=${encodeURIComponent(priorView)}`,
                  `view_id=${encodeURIComponent(expected!)}`,
                )
              : path;
          if (q.queryKey[1] === "pages") {
            const prior = q.state.data as
              { pages: unknown[]; pageParams?: string[] } | undefined;
            const pages = [],
              pageParams = [];
            let cursor: string | undefined = prior?.pageParams?.[0];
            for (let i = 0; i < Math.max(1, prior?.pages.length ?? 1); i++) {
              const response = await api.request(
                name,
                updated +
                  (cursor
                    ? `${updated.includes("?") ? "&" : "?"}cursor=${encodeURIComponent(cursor)}`
                    : ""),
                { view: expected },
              );
              pages.push(response);
              pageParams.push(cursor);
              const resource = response.data as unknown as Resource<
                Record<string, unknown>
              >;
              const value = resource.data;
              const page =
                value &&
                (("next_cursor" in value
                  ? value
                  : Object.values(value).find(
                      (v) => v && typeof v === "object" && "next_cursor" in v,
                    )) as { next_cursor?: string | null } | undefined);
              cursor = page?.next_cursor ?? undefined;
              if (!cursor) break;
            }
            return {
              key: [scope, "pages", name, updated, expected],
              data: { pages, pageParams },
            };
          }
          const data = await api.request(name, updated, { view: expected });
          return { key: [scope, "read", name, updated, expected], data };
        }),
      );
      for (const item of reads) cache.setQueryData(item.key, item.data);
      cache.setQueryData(key, next);
      for (const c of contextUpdates) cache.setQueryData(c.key, c.data);
      await cache.refetchQueries({
        predicate: (q) =>
          q.isActive() &&
          q.queryKey[0] === scope &&
          q.queryKey[1] === "bus-minute" &&
          q.queryKey[2] === ticker,
      });
      return next;
    },
  };
}
export function useRead<K extends keyof Endpoints>(
  name: K,
  path: string | null,
  view?: string,
) {
  const { api, scope } = useRuntime();
  return useQuery({
    queryKey: [scope, "read", name, path, view],
    enabled: !!path,
    queryFn: async ({ signal }) => {
      const result = await api.request(name, path!, { signal, view });
      const data = result.data as unknown as Resource<unknown>;
      if (data?.state === "ERROR")
        throw new ApiFailure("READ_FAILED", "读取失败，已保留原有内容。");
      return result;
    },
  });
}
export function unwrap<T>(response?: Response<Resource<T>>) {
  return response?.data.data;
}
export const tickerPath = (ticker: string) =>
  `/tickers/${encodeURIComponent(ticker)}`;
export const id = encodeURIComponent;
