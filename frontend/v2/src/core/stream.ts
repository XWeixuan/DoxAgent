import { API_PREFIX, type AuthDriver } from "./api";
import { SseParser, type SseFrame } from "./updates";
/** Transport only. The page owns its bounded reducer and reset baseline; Overview never calls this. */
export function connectVisibleStream(options: {
  path: string;
  cursor: () => string;
  auth: AuthDriver;
  onFrame: (frame: SseFrame) => void | Promise<void>;
  onReset: () => void;
  onError: (message: string) => void;
  signal: AbortSignal;
}) {
  if (
    !options.path.startsWith("/tickers/") ||
    !/\/(stream|events)(\?|$)/.test(options.path)
  )
    throw new Error("Unsupported stream path");
  let connection: AbortController | null = null;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let failures = 0;
  let stopped = false;
  let notified = false;
  const stop = () => {
    stopped = true;
    connection?.abort();
    clearTimeout(timer);
    document.removeEventListener("visibilitychange", visibility);
    options.signal.removeEventListener("abort", stop);
  };
  const report = (message: string) => {
    if (!notified) {
      notified = true;
      options.onError(message);
    }
  };
  const start = async () => {
    if (stopped || document.hidden || connection) return;
    const active = new AbortController();
    connection = active;
    try {
      const token = options.auth.token();
      if (!token) {
        stop();
        return;
      }
      // Last-Event-ID carries the atomic baseline cursor, also on initial connection.
      const response = await fetch(API_PREFIX + options.path, {
        headers: {
          Authorization: `Bearer ${token}`,
          Accept: "text/event-stream",
          "Last-Event-ID": options.cursor(),
        },
        signal: active.signal,
        cache: "no-store",
        redirect: "error",
      });
      if (response.status === 401) {
        if (!(await options.auth.refresh())) {
          options.auth.invalidate();
          stop();
          return;
        }
        throw new Error("会话已续期，正在恢复更新。");
      }
      if (response.status === 403) {
        report("此范围的实时更新暂无权限。");
        stop();
        return;
      }
      if (response.status === 410) {
        stop();
        options.onReset();
        return;
      }
      if (
        !response.ok ||
        !response.body ||
        !response.headers.get("content-type")?.includes("text/event-stream")
      )
        throw new Error("实时更新暂时中断，正在恢复。");
      const parser = new SseParser(),
        decoder = new TextDecoder(),
        reader = response.body.getReader();
      while (!stopped) {
        const { value, done } = await reader.read();
        if (done) break;
        for (const frame of parser.push(
          decoder.decode(value, { stream: true }),
        )) {
          if (
            frame.event === "stream.reset" ||
            frame.event === "reset" ||
            frame.event === "scope.rolled"
          ) {
            stop();
            options.onReset();
            return;
          }
          try {
            await options.onFrame(frame);
          } catch {
            stop();
            options.onReset();
            return;
          }
          failures = 0;
          notified = false;
        }
      }
      if (!stopped && !document.hidden)
        throw new Error("实时更新连接中断，正在恢复。");
    } catch (error) {
      if (!active.signal.aborted) {
        failures++;
        if (failures >= 3) report((error as Error).message);
      }
    } finally {
      if (connection === active) connection = null;
      if (!stopped && !document.hidden)
        timer = setTimeout(
          () => void start(),
          Math.min(30_000, 1000 * 2 ** Math.min(failures, 5)) *
            (0.8 + Math.random() * 0.2),
        );
    }
  };
  const visibility = () => {
    clearTimeout(timer);
    if (document.hidden) connection?.abort();
    else void start();
  };
  document.addEventListener("visibilitychange", visibility);
  options.signal.addEventListener("abort", stop, { once: true });
  if (options.signal.aborted) stop();
  else void start();
  return stop;
}
