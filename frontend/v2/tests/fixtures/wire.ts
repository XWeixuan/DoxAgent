import type * as W from "@contract";
import Decimal from "decimal.js";
export const coverage: W.Coverage = {
  state: "COMPLETE",
  reasons: [],
  known_count: null,
  excluded_count: null,
  observed_through: null,
};
export const value = <T>(value: T): W.Value<T> => ({
  state: "AVAILABLE",
  value,
  reason: null,
});
export const resource = <T>(data: T): W.Resource<T> => ({
  state: "AVAILABLE",
  data,
  reason: null,
  coverage,
});
export function metric(
  id: string,
  current: string,
  previous = "0",
  change = "0",
  unit: W.Metric["unit"] = "COUNT",
): W.Metric {
  return {
    metric_id: id,
    unit,
    current: value(current),
    previous: value(previous),
    change_pct: value(change),
    current_coverage: coverage,
    previous_coverage: coverage,
    provisional: false,
  };
}
export const clock: W.ClockContext = {
  semantic_day: "2026-09-04",
  timezone: "America/New_York",
  boundary_local_time: "02:00:00",
  is_trading_day: value(true),
  previous_trading_day: value("2026-09-03"),
  calendar_version: "demo-calendar-v1",
  session: value("REGULAR"),
  session_start_at: value("2026-09-04T13:30:00Z"),
  session_end_at: value("2026-09-04T20:00:00Z"),
  next_minute_at: new Date(
    Math.ceil(Date.now() / 60_000) * 60_000,
  ).toISOString(),
};
export const capabilities: W.Capabilities = {
  monitoring: { available: true, reason: null, message: null },
  paper_trading: { available: true, reason: null, message: null },
  live_trading: { available: true, reason: null, message: null },
  revenue_audit: {
    available: false,
    reason: "NOT_SUPPORTED",
    message: "暂未开放",
  },
  message_stream: { available: true, reason: null, message: null },
  runtime_graph_stream: { available: true, reason: null, message: null },
};
export function context(period: W.Period): W.ReadContext {
  // Synthetic calendar belongs to the demo server, never to the frontend.
  const count =
    period === "TRADING_DAYS_30" ? 30 : period === "TRADING_DAYS_7" ? 7 : 1;
  const anchor = new Date(
    period === "CURRENT_TRADING_DAY"
      ? "2026-09-04T06:00:00Z"
      : "2026-09-03T06:00:00Z",
  );
  const days: string[] = [];
  while (days.length < count * 2) {
    const day = anchor.toISOString().slice(0, 10);
    if (
      ![0, 6].includes(anchor.getUTCDay()) &&
      !["2026-06-19", "2026-07-03"].includes(day)
    )
      days.push(day);
    anchor.setUTCDate(anchor.getUTCDate() - 1);
  }
  const window = (selected: string[]): W.DayWindow => {
    const ordered = [...selected].reverse();
    const end = new Date(`${selected[0]}T06:00:00Z`);
    end.setUTCDate(end.getUTCDate() + 1);
    return {
      start_at: `${ordered[0]}T06:00:00Z`,
      end_at: end.toISOString(),
      observed_until:
        end > new Date("2026-09-04T14:00:00Z")
          ? "2026-09-04T14:00:00Z"
          : end.toISOString(),
      trading_days: ordered,
      trading_day_count: ordered.length,
      membership: "LISTED_TRADING_DAYS",
      coverage,
    };
  };
  return {
    view_id: crypto.randomUUID(),
    expires_at: new Date(Date.now() + 3600_000).toISOString(),
    page: "OVERVIEW",
    ticker: null,
    clock,
    period_options: (
      [
        "PREVIOUS_TRADING_DAY",
        "CURRENT_TRADING_DAY",
        "TRADING_DAYS_7",
        "TRADING_DAYS_30",
      ] as const
    ).map((period) => ({ period, selectable: true, reason: null })),
    period: {
      selected: period,
      current: window(days.slice(0, count)),
      previous: window(days.slice(count)),
      comparison_applicable: true,
      comparison_reasons: [],
    },
    activation: {
      state: "NOT_PRODUCED",
      data: null,
      reason: "NOT_APPLICABLE",
      coverage,
    },
    coverage,
  };
}
export function periodMultiplier(period: W.Period) {
  return period === "TRADING_DAYS_30"
    ? 20
    : period === "TRADING_DAYS_7"
      ? 5
      : period === "CURRENT_TRADING_DAY"
        ? 2
        : 1;
}
export function metrics(period: W.Period): W.OverviewMetrics {
  const multiplier = periodMultiplier(period);
  const m = (
    id: string,
    n: number,
    prior: number,
    change: string,
    unit: W.Metric["unit"] = "COUNT",
  ) =>
    metric(
      id,
      new Decimal(n).mul(multiplier).toString(),
      new Decimal(prior).mul(multiplier).toString(),
      change,
      unit,
    );
  return {
    policy_hits: m("policy_hits", 12, 9, "33.3"),
    trade_executed: m("trade_executed", 4, 3, "33.3"),
    trade_triggered: m("trade_triggered", 6, 5, "20"),
    messages: m("messages", 1248, 1016, "22.8"),
    api_token_cost: m("api_token_cost", 18.42, 20.5, "-10.1", "USD"),
    nonroutine_repairs: metric("nonroutine_repairs", "0"),
    paper_realized_net_pnl: m("paper_pnl", 286.5, 214.2, "33.8", "USD"),
    live_realized_net_pnl: m("live_pnl", 124.8, 108.8, "14.7", "USD"),
  };
}
export function rowForPeriod(
  row: W.TickerOverview,
  period: W.Period,
): W.TickerOverview {
  const copy = structuredClone(row);
  for (const metric of Object.values(copy.metrics)) {
    for (const value of [metric.current, metric.previous]) {
      if (value.state === "AVAILABLE" && value.value !== null)
        value.value = new Decimal(value.value)
          .mul(periodMultiplier(period))
          .toString();
    }
  }
  return copy;
}
export function state(
  ticker: string,
  run: W.RunState = "RUNNING",
  mode: W.MonitorMode = "MESSAGE_MONITORING",
  health: W.Health = "NORMAL",
): W.TickerState {
  return {
    ticker,
    run_state: run,
    requested_mode: mode,
    effective_mode:
      run === "INITIALIZING"
        ? { state: "NOT_PRODUCED", value: null, reason: "NOT_PRODUCED" }
        : value(mode),
    health,
    health_reasons: health === "BLOCKED" ? ["消息源激活失败"] : [],
    initialization_id: run === "INITIALIZING" ? `init-${ticker}` : null,
    initialization_incomplete: run === "INITIALIZING",
    removed: false,
    removed_at: null,
    control_etag: `${ticker}-1`,
    control_revision: 1,
    work_epoch: 1,
    cutoff_at: null,
    mode_effective_at: null,
    existing_trades_continue: true,
    actions: {
      pause: {
        allowed: run === "RUNNING",
        reason: run === "RUNNING" ? null : "当前状态不可暂停",
      },
      restart: {
        allowed: run === "PAUSED" || run === "STOPPED",
        reason:
          run === "PAUSED" || run === "STOPPED" ? null : "当前状态不可重启",
      },
      remove: { allowed: true, reason: null },
      retry_initialization: { allowed: health === "BLOCKED", reason: null },
    },
  };
}
export function initialization(
  ticker: string,
  failed = false,
): W.InitializationProgress {
  const keys: W.StepKey[] = [
    "RESEARCH",
    "EVENT_LIBRARY",
    "EXPECTATIONS",
    "POLICIES",
    "SOURCES_ACTIVATION",
    "START_RUNTIME",
  ];
  const seconds = [432, 76, 218, 186, 48, 0];
  return {
    initialization_id: `init-${ticker}`,
    ticker,
    status: failed ? "FAILED" : "RUNNING",
    state_seq: 5,
    control_etag: `init-${ticker}-1`,
    manual_resume_allowed: failed,
    failed_node_keys: failed ? ["o4_activation"] : [],
    quality_annotations: [],
    failure: failed
      ? {
          code: "SOURCE_ACTIVATION_FAILED",
          message: "消息源激活未完成，已保留研究与策略成果。可从失败步骤继续。",
          retryable: true,
          request_id: "demo-failure",
          fields: [],
          content_id: null,
        }
      : null,
    steps: keys.map((step_key, i) => ({
      step_key,
      status:
        i < (failed ? 4 : 2)
          ? "SUCCEEDED"
          : i === (failed ? 4 : 2)
            ? failed
              ? "FAILED"
              : "RUNNING"
            : "PENDING",
      first_started_at:
        i <= (failed ? 4 : 2)
          ? value(new Date(Date.now() - seconds[i] * 1000).toISOString())
          : { state: "NOT_PRODUCED", value: null, reason: "NOT_PRODUCED" },
      settled_at:
        i < (failed ? 5 : 2)
          ? value(new Date().toISOString())
          : { state: "NOT_PRODUCED", value: null, reason: "NOT_PRODUCED" },
      duration_seconds:
        i <= (failed ? 4 : 2)
          ? value(seconds[i])
          : { state: "NOT_PRODUCED", value: null, reason: "NOT_PRODUCED" },
      quality_annotations: [],
    })),
  };
}
export function rows(): W.TickerOverview[] {
  return [
    state("MU", "RUNNING", "PAPER_TRADING"),
    state("NVDA", "RUNNING", "LIVE_TRADING"),
    state("AAPL", "PAUSED"),
    state("AMD", "INITIALIZING", "MESSAGE_MONITORING", "UNKNOWN"),
    state("TSLA", "INITIALIZING", "PAPER_TRADING", "BLOCKED"),
  ].map((s, i) => ({
    state: s,
    last_standard_message_at:
      i < 3
        ? value(`2026-09-04T14:${["42", "38", "12"][i]}:00Z`)
        : { state: "NOT_PRODUCED", value: null, reason: "NOT_PRODUCED" },
    source_counts: {
      normal: value([8, 6, 4, 0, 2][i]),
      abnormal: value(i === 4 ? 1 : 0),
    },
    metrics: {
      trade_executed: metric("trade_executed", String([3, 1, 0, 0, 0][i])),
      trade_triggered: metric("trade_triggered", String([4, 2, 0, 0, 0][i])),
      messages: metric("messages", String([642, 418, 188, 0, 0][i])),
      api_token_cost: metric(
        "api_token_cost",
        ["9.26", "6.42", "2.74", "0", "0"][i],
        "0",
        "0",
        "USD",
      ),
    },
    initialization: s.initialization_incomplete
      ? resource(initialization(s.ticker, s.health === "BLOCKED"))
      : null,
  }));
}
