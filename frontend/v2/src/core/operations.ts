import type {
  InitializationProgress,
  Operation,
  StartTickerRequest,
  TickerState,
  TickerOverview,
  Metric,
} from "@contract";
import type { Runtime } from "./runtime";
import { ApiFailure, type Endpoints } from "./api";
export type Command = {
  ticker: string;
  kind: Operation["kind"];
  state?: TickerState;
  progress?: InitializationProgress;
  body?: StartTickerRequest;
};
export type PendingCommand = {
  command: Command;
  key: string;
  etag?: string;
  operation?: Operation;
  phase:
    | "submitting"
    | "tracking"
    | "waiting"
    | "unknown"
    | "conflict"
    | "failed"
    | "done";
  message: string;
};
const blankMetric: Metric = {
  metric_id: "pending",
  unit: "COUNT",
  current: { state: "NOT_RECORDED", value: null, reason: "NOT_RECORDED" },
  previous: { state: "NOT_RECORDED", value: null, reason: "NOT_RECORDED" },
  change_pct: {
    state: "NOT_APPLICABLE",
    value: null,
    reason: "NO_PREVIOUS_WINDOW",
  },
  current_coverage: {
    state: "UNKNOWN",
    reasons: [],
    known_count: null,
    excluded_count: null,
    observed_through: null,
  },
  previous_coverage: null,
  provisional: false,
};
const unknownCount = {
  state: "NOT_RECORDED",
  value: null,
  reason: "NOT_RECORDED",
} as const;
export function pendingRow(state: TickerState): TickerOverview {
  return {
    state,
    last_standard_message_at: unknownCount,
    source_counts: { normal: unknownCount, abnormal: unknownCount },
    metrics: {
      trade_executed: blankMetric,
      trade_triggered: blankMetric,
      messages: blankMetric,
      api_token_cost: { ...blankMetric, unit: "USD" },
    },
    initialization: null,
  };
}
export const trackingDelay = (seconds: number | null) =>
  Math.min(30, Math.max(2, seconds ?? 2)) * 1000;
export const matchesFilters = (
  state: TickerState,
  run: unknown,
  health: unknown,
) =>
  !state.removed &&
  (run === "ALL" || run === state.run_state) &&
  (health === "ALL" || health === state.health);
export class Operations {
  private running = new Set<string>();
  constructor(private runtime: Runtime) {}
  private key() {
    return [this.runtime.scope, "operations"];
  }
  private publish(entry: PendingCommand) {
    this.runtime.query.setQueryData<Record<string, PendingCommand>>(
      this.key(),
      (old) => ({ ...old, [entry.command.ticker]: { ...entry } }),
    );
  }
  private apply(operation: Operation, progress?: Endpoints["Initialization"]) {
    const { query, scope } = this.runtime;
    for (const [key, old] of query.getQueriesData<Endpoints["Tickers"]>({
      predicate: (q) =>
        q.queryKey[0] === scope && q.queryKey[1] === "overview-list",
    })) {
      if (!old?.data.data) continue;
      const page = old.data.data;
      const found = page.items.some(
        (row) => row.state.ticker === operation.ticker,
      );
      let items = page.items.map((row) =>
        row.state.ticker === operation.ticker
          ? {
              ...row,
              state: operation.ticker_state,
              ...(progress ? { initialization: progress.data } : {}),
            }
          : row,
      );
      items = items.filter((row) => matchesFilters(row.state, key[3], key[4]));
      if (
        !found &&
        operation.kind === "START" &&
        operation.status !== "FAILED" &&
        matchesFilters(operation.ticker_state, key[3], key[4])
      )
        items = [
          {
            ...pendingRow(operation.ticker_state),
            initialization: progress?.data ?? null,
          },
          ...items,
        ];
      query.setQueryData(key, {
        ...old,
        data: { ...old.data, state: "AVAILABLE", data: { ...page, items } },
      });
    }
    // Navigation is separately scoped; keep existing loaded pages synchronized without a global refetch.
    query.setQueriesData<Endpoints["Navigation"]>(
      {
        predicate: (q) =>
          q.queryKey[0] === scope && q.queryKey[1] === "navigation",
      },
      (old) => {
        if (!old?.data.data) return old;
        const state = operation.ticker_state;
        const items = old.data.data.items.filter(
          (row) => row.ticker !== state.ticker,
        );
        if (!state.removed && !state.initialization_incomplete)
          items.push({
            ticker: state.ticker,
            run_state: state.run_state,
            health: state.health,
            removed: false,
            initialization_incomplete: false,
          });
        return {
          ...old,
          data: { ...old.data, data: { ...old.data.data, items } },
        };
      },
    );
  }
  async submit(command: Command, reuse?: PendingCommand) {
    const scope = this.runtime.scope;
    const runKey = `${scope}:${command.ticker}`;
    if (this.running.has(runKey)) return;
    this.running.add(runKey);
    const entry: PendingCommand = reuse
      ? { ...reuse, phase: "submitting" }
      : {
          command,
          key: crypto.randomUUID(),
          etag: command.progress?.control_etag ?? command.state?.control_etag,
          phase: "submitting",
          message: "正在提交…",
        };
    this.publish(entry);
    let submitted = false;
    try {
      if (!reuse && command.kind === "START") {
        try {
          const state = await this.runtime.api.request(
            "Ticker",
            `/tickers/${encodeURIComponent(command.ticker)}`,
          );
          entry.etag = state.data.data?.control_etag;
        } catch (error) {
          if (!(error instanceof ApiFailure && error.status === 404))
            throw error;
        }
      }
      if (scope !== this.runtime.scope) return;
      const base = `/tickers/${encodeURIComponent(command.ticker)}`;
      const path =
        command.kind === "START"
          ? "/tickers"
          : command.kind === "REMOVE"
            ? base
            : command.kind === "RESUME_INITIALIZATION"
              ? `${base}/initializations/${encodeURIComponent(command.progress!.initialization_id)}/resume`
              : `${base}/${command.kind.toLowerCase()}`;
      submitted = true;
      const response = await this.runtime.api.request("Operation", path, {
        method: command.kind === "REMOVE" ? "DELETE" : "POST",
        body: command.kind === "REMOVE" ? undefined : (command.body ?? {}),
        key: entry.key,
        etag: entry.etag,
      });
      if (
        response.data.ticker !== command.ticker ||
        response.data.kind !== command.kind
      )
        throw new ApiFailure(
          "OPERATION_SCOPE_MISMATCH",
          "操作回执与目标不一致，尚不能确认结果。",
        );
      if (scope !== this.runtime.scope) return;
      entry.operation = response.data;
      if (command.kind === "START") this.apply(response.data);
      await this.track(entry, scope);
    } catch (error) {
      if (scope !== this.runtime.scope) return;
      const failure = error instanceof ApiFailure ? error : null;
      entry.phase =
        failure?.status === 412
          ? "conflict"
          : submitted &&
              (!failure || failure.status === 0 || failure.status >= 500)
            ? "unknown"
            : "failed";
      entry.message =
        entry.phase === "conflict"
          ? "该标的已发生变化。已读取最新状态，请核对后重新提交。"
          : entry.phase === "unknown"
            ? "提交结果尚未确认。重试将沿用同一操作身份。"
            : (error as Error).message;
      if (entry.phase === "conflict") {
        try {
          const latest = await this.runtime.api.request(
            "Ticker",
            `/tickers/${encodeURIComponent(command.ticker)}`,
          );
          if (latest.data.data)
            entry.command = { ...command, state: latest.data.data };
          if (command.progress) {
            const progress = await this.runtime.api.request(
              "Initialization",
              `/tickers/${command.ticker}/initializations/${command.progress.initialization_id}`,
            );
            if (progress.data.data) entry.command.progress = progress.data.data;
          }
        } catch {
          entry.message = "版本冲突；最新状态读取失败。请刷新后重新操作。";
        }
      }
      if (scope === this.runtime.scope) this.publish(entry);
    } finally {
      this.running.delete(runKey);
    }
  }
  async resume(entry: PendingCommand) {
    if (entry.phase === "unknown") return this.submit(entry.command, entry);
    if (entry.phase === "conflict" || entry.phase === "failed")
      return this.submit(entry.command);
    const key = `${this.runtime.scope}:${entry.command.ticker}`;
    if (this.running.has(key)) return;
    this.running.add(key);
    try {
      await this.track({ ...entry }, this.runtime.scope);
    } finally {
      this.running.delete(key);
    }
  }
  private async track(entry: PendingCommand, scope: string) {
    const until = Date.now() + 5 * 60_000;
    while (scope === this.runtime.scope) {
      const op = entry.operation!;
      if (op.status === "SUCCEEDED" || op.status === "FAILED") {
        entry.phase = op.status === "SUCCEEDED" ? "done" : "failed";
        entry.message =
          op.status === "FAILED"
            ? (op.error?.message ?? "操作失败，请重试。")
            : op.outcome === "INITIALIZATION_QUEUED"
              ? "已加入初始化队列；使用页面刷新查看进度。"
              : "操作已完成。";
        if (op.status === "SUCCEEDED") this.apply(op);
        this.publish(entry);
        if (
          op.initialization_id &&
          op.ticker_state.initialization_incomplete &&
          !op.ticker_state.removed
        ) {
          try {
            const progress = await this.runtime.api.request(
              "Initialization",
              `/tickers/${op.ticker}/initializations/${op.initialization_id}`,
            );
            if (scope === this.runtime.scope) this.apply(op, progress);
          } catch {
            /* Receipt remains valid; manual Overview refresh can recover progress. */
          }
        }
        return;
      }
      if (Date.now() >= until) {
        entry.phase = "waiting";
        entry.message = "仍在处理，可手动查看结果。";
        this.publish(entry);
        return;
      }
      entry.phase = "tracking";
      entry.message = "操作已接受，正在等待执行结果…";
      this.publish(entry);
      await new Promise((resolve) =>
        setTimeout(resolve, trackingDelay(op.retry_after_seconds)),
      );
      if (scope !== this.runtime.scope) return;
      if (typeof document !== "undefined" && document.hidden) continue;
      try {
        const latest = (
          await this.runtime.api.request(
            "Operation",
            `/operations/${op.operation_id}`,
          )
        ).data;
        if (
          latest.operation_id !== op.operation_id ||
          latest.ticker !== op.ticker ||
          latest.kind !== op.kind
        )
          throw new Error("Operation receipt scope mismatch");
        entry.operation = latest;
      } catch {
        if (scope === this.runtime.scope) {
          entry.phase = "waiting";
          entry.message = "暂时无法确认结果，请手动查看。";
          this.publish(entry);
        }
        return;
      }
    }
  }
}
const instances = new WeakMap<Runtime, Operations>();
export function operationsFor(runtime: Runtime) {
  let operations = instances.get(runtime);
  if (!operations) {
    operations = new Operations(runtime);
    instances.set(runtime, operations);
  }
  return operations;
}
