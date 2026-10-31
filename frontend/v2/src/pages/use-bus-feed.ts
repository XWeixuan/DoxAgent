import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type RefObject,
} from "react";
import { useQuery } from "@tanstack/react-query";
import type { MessageDelta, MessageSummary } from "@contract";
import { useRuntime } from "@/core/runtime";
import { queryString } from "@/core/api";
import { tickerPath, useViewRenewal } from "@/core/page-query";
import { useLive } from "@/core/live-query";
import { compareMessageOrder } from "@/core/updates";

export function useBusFeed(
  ticker: string,
  filters: Record<string, string | undefined>,
  scroll: RefObject<HTMLDivElement | null>,
) {
  const { api, scope } = useRuntime();
  const [rows, setRows] = useState<MessageSummary[]>([]);
  const rowsRef = useRef(rows);
  rowsRef.current = rows;
  const anchor = useRef<{ id?: string; offset: number; top: number } | null>(
    null,
  );
  const remember = () => {
    const box = scroll.current;
    if (!box) return;
    const item = [
      ...box.querySelectorAll<HTMLElement>("[data-message-id]"),
    ].find(
      (node) =>
        node.getBoundingClientRect().bottom > box.getBoundingClientRect().top,
    );
    anchor.current = {
      id: item?.dataset.messageId,
      offset: item
        ? item.getBoundingClientRect().top - box.getBoundingClientRect().top
        : 0,
      top: box.scrollTop,
    };
  };
  const merge = (incoming: MessageSummary[], remove?: string) => {
    remember();
    setRows((previous) => {
      const byId = new Map(
        previous
          .filter((m) => m.standard_message_id !== remove)
          .map((m) => [m.standard_message_id, m]),
      );
      for (const m of incoming)
        if (
          !byId.has(m.standard_message_id) ||
          byId.get(m.standard_message_id)!.row_revision <= m.row_revision
        )
          byId.set(m.standard_message_id, m);
      return [...byId.values()].sort(compareMessageOrder);
    });
  };
  useLayoutEffect(() => {
    const box = scroll.current,
      saved = anchor.current;
    if (!box || !saved) return;
    const item = [
      ...box.querySelectorAll<HTMLElement>("[data-message-id]"),
    ].find((n) => n.dataset.messageId === saved.id);
    box.scrollTop = item
      ? box.scrollTop +
        item.getBoundingClientRect().top -
        box.getBoundingClientRect().top -
        saved.offset
      : saved.top;
    anchor.current = null;
  }, [rows, scroll]);
  const baseline = useQuery({
    queryKey: [scope, "bus-feed", ticker, filters],
    queryFn: async ({ signal }) => {
      const context = await api.request(
        "ReadContext",
        "/read-context" +
          queryString({ ticker, page: "MESSAGE_BUS", period: "ALL" }),
        { signal },
      );
      const view = context.data.view_id;
      const response = await api.request(
        "Messages",
        tickerPath(ticker) +
          "/messages" +
          queryString({
            view_id: view,
            ...filters,
            limit: "20",
            reading_after: rowsRef.current.at(-1)?.standard_message_id,
          }),
        { view, signal },
      );
      return { context, response };
    },
    refetchOnWindowFocus: false,
  });
  const view = baseline.data?.context.data.view_id;
  const [next, setNext] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [pageError, setPageError] = useState<Error | null>(null);
  const generation = useRef(view);
  generation.current = view;
  useEffect(() => {
    const page = baseline.data?.response.data.data?.messages;
    if (!page) return;
    merge(page.items);
    setNext(page.next_cursor);
    setPageError(null);
    // A replacement baseline merges the reading buffer, never truncates it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [baseline.data]);
  const renew = useCallback(async () => {
    await baseline.refetch({ throwOnError: true, cancelRefetch: false });
  }, [baseline.refetch]);
  useViewRenewal(baseline.data?.context.data, renew);
  const streamError = useLive<MessageDelta>(
    view
      ? tickerPath(ticker) +
          "/messages/events" +
          queryString({ view_id: view, ...filters })
      : null,
    view,
    baseline.data?.response.data.data?.stream_cursor,
    async (delta, cursor) => {
      if (delta.action === "REMOVE") {
        merge([], delta.standard_message_id);
        return;
      }
      const row =
        delta.row ??
        (
          await api.request(
            "Message",
            tickerPath(ticker) +
              `/messages/${encodeURIComponent(delta.standard_message_id)}/revisions/${delta.revision}` +
              queryString({ view_id: view, stream_cursor: cursor }),
            { view },
          )
        ).data.data;
      if (
        !row ||
        row.standard_message_id !== delta.standard_message_id ||
        row.revision !== delta.revision ||
        row.row_revision !== delta.row_revision
      )
        throw new Error("Message revision mismatch");
      merge([row]);
    },
    () => {
      void renew().catch(() => undefined);
    },
  );
  const fetchNextPage = async () => {
    if (!view || !next || loading) return;
    const atView = view;
    setLoading(true);
    try {
      const result = await api.request(
        "Messages",
        tickerPath(ticker) +
          "/messages" +
          queryString({ view_id: view, ...filters, cursor: next, limit: "20" }),
        { view },
      );
      if (generation.current !== atView) return;
      const page = result.data.data?.messages;
      if (page) {
        merge(page.items);
        setNext(page.next_cursor);
        setPageError(null);
      }
    } catch (e) {
      if (generation.current === atView) setPageError(e as Error);
    } finally {
      setLoading(false);
    }
  };
  const source = baseline.data?.response;
  const data = source
    ? {
        ...source,
        data: {
          ...source.data,
          data: { ...source.data.data!.messages, items: rows },
        },
      }
    : undefined;
  return {
    data,
    isPending: baseline.isPending,
    error: pageError ?? baseline.error,
    refetch: renew,
    hasNextPage: !!next,
    isFetchingNextPage: loading,
    fetchNextPage,
    streamError,
  };
}
