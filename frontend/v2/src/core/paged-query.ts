import { useInfiniteQuery } from "@tanstack/react-query";
import type { Page, Resource, Response } from "@contract";
import { useRuntime } from "./runtime";
import { type Endpoints } from "./api";
export function usePages<T, K extends keyof Endpoints>(
  name: K,
  path: string | null,
  extract: (data: Endpoints[K]["data"]) => Page<T> | null,
  view?: string,
  preceding?: Page<T>,
) {
  const { api, scope } = useRuntime();
  const query = useInfiniteQuery({
    queryKey: [scope, "pages", name, path, view],
    enabled: !!path && (!preceding || preceding.has_more),
    initialPageParam:
      preceding?.next_cursor ?? (undefined as string | undefined),
    queryFn: ({ signal, pageParam }) =>
      api.request(
        name,
        path! +
          (pageParam
            ? `${path!.includes("?") ? "&" : "?"}cursor=${encodeURIComponent(pageParam)}`
            : ""),
        { signal, view },
      ),
    getNextPageParam: (last) => extract(last.data)?.next_cursor ?? undefined,
  });
  const first = query.data?.pages[0];
  const pages = query.data?.pages.map((p) => extract(p.data));
  const base = pages?.[0];
  const inconsistent = pages?.some(
    (p) => p && base && p.snapshot_id !== base.snapshot_id,
  );
  const resource = first?.data as Resource<unknown> | undefined;
  const data: Response<Resource<Page<T>>> | undefined =
    first && resource
      ? {
          meta: first.meta,
          data: {
            ...resource,
            data: base
              ? {
                  ...base,
                  items: [
                    ...(preceding?.items ?? []),
                    ...(pages?.flatMap((p) => p?.items ?? []) ?? []),
                  ],
                  has_more: !!query.hasNextPage,
                  next_cursor: pages?.at(-1)?.next_cursor ?? null,
                }
              : null,
          },
        }
      : undefined;
  return {
    ...query,
    data: inconsistent ? undefined : data,
    error: inconsistent
      ? new Error("分页范围不一致，请刷新当前范围。")
      : query.error,
    source: first?.data as Endpoints[K]["data"] | undefined,
  };
}
