import { useEffect, useRef, useState } from "react";
import { useRuntime } from "./runtime";
import { scheduleVisibleMinutes, StreamCursor, type SseFrame } from "./updates";
import { connectVisibleStream } from "./stream";
import { validateStream } from "./api";
import type { ReadContext, Response, StreamEnvelope } from "@contract";
export function useMinuteReads(
  context: Response<ReadContext> | undefined,
  read: () => Promise<void>,
) {
  const [error, setError] = useState("");
  const latest = useRef(read);
  useEffect(() => {
    latest.current = read;
  }, [read]);
  useEffect(() => {
    if (!context) return;
    const c = context.data.clock;
    return scheduleVisibleMinutes(
      {
        nextMinuteAt: c.next_minute_at,
        asOf: context.meta.as_of,
        receivedAt: Date.now(),
      },
      async () => {
        try {
          await latest.current();
          setError("");
        } catch (e) {
          setError((e as Error).message);
        }
      },
    );
  }, [context]);
  return error;
}
export function useLive<T>(
  path: string | null,
  view: string | undefined,
  baseline: string | undefined,
  apply: (data: T, cursor: string) => void | Promise<void>,
  reset: () => void,
) {
  const { auth, scope, query } = useRuntime();
  const [error, setError] = useState("");
  const callbacks = useRef({ apply, reset });
  useEffect(() => {
    callbacks.current = { apply, reset };
  }, [apply, reset]);
  useEffect(() => {
    if (!path || !view || !baseline) return;
    const key = [scope, "stream-cursor", path, baseline];
    let cursor = query.getQueryData<StreamCursor>(key);
    const abort = new AbortController();
    return connectVisibleStream({
      path,
      auth,
      signal: abort.signal,
      cursor: () => cursor?.cursor ?? baseline,
      onError: setError,
      onReset: () => {
        query.removeQueries({ queryKey: key });
        callbacks.current.reset();
      },
      onFrame: async (frame: SseFrame) => {
        const e = JSON.parse(frame.data) as StreamEnvelope<T>;
        validateStream(
          path.includes("/runtime/graph/") ? "GraphStream" : "MessageStream",
          e,
        );
        if (
          !e ||
          e.view_id !== view ||
          typeof e.scope_key !== "string" ||
          !e.payload
        )
          throw new Error("Invalid stream");
        cursor ??= new StreamCursor(e.scope_key, view, baseline);
        await cursor.acceptAsync(frame, e, async (p) => {
          await callbacks.current.apply(p, e.stream_cursor);
        });
        query.setQueryData(key, cursor);
        setError("");
      },
    });
  }, [path, view, baseline, auth, query, scope]);
  return error;
}
