import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Activity, Clock3, RefreshCw, ScanLine } from "lucide-react";
import type { Period } from "@contract";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import {
  Table,
  TableBody,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Empty, EmptyHeader, EmptyTitle } from "@/components/ui/empty";

import { Module, Notice } from "@/components/state";
import { valueText } from "@/core/format";
import { filtersSupported, periods, useOverview } from "./data";
import { Metrics } from "./metrics";
import { StartForm } from "./start-form";
import { MonitorRow } from "./monitor-row";

const sessions = {
  PRE_MARKET: "盘前",
  REGULAR: "盘中",
  POST_MARKET: "盘后",
  OVERNIGHT: "夜盘",
  CLOSED_MAINTENANCE: "休市维护",
  CLOSED_SLEEP: "休市休眠",
};
function SelectFilter({
  value,
  label,
  options,
  onChange,
  disabled = false,
}: {
  value: string;
  label: string;
  options: [string, string][];
  onChange: (value: string) => void;
  disabled?: boolean;
}) {
  return (
    <Select value={value} onValueChange={onChange} disabled={disabled}>
      <SelectTrigger
        aria-label={label}
        title={disabled ? "筛选暂不可用" : label}
        size="sm"
      >
        <SelectValue />
      </SelectTrigger>
      <SelectContent position="popper">
        <SelectGroup>
          {options.map(([value, text]) => (
            <SelectItem key={value} value={value}>
              {text}
            </SelectItem>
          ))}
        </SelectGroup>
      </SelectContent>
    </Select>
  );
}
export default function Overview() {
  const [params, setParams] = useSearchParams();
  const selected = params.get("period");
  const period: Period =
    periods.find(([value]) => value === selected)?.[0] ??
    "PREVIOUS_TRADING_DAY";
  const run =
    filtersSupported &&
    ["RUNNING", "INITIALIZING", "PAUSED", "STOPPED"].includes(
      params.get("run") ?? "",
    )
      ? params.get("run")!
      : "ALL";
  const health =
    filtersSupported &&
    ["NORMAL", "DEGRADED", "BLOCKED", "UNKNOWN"].includes(
      params.get("health") ?? "",
    )
      ? params.get("health")!
      : "ALL";
  const model = useOverview(period, run, health);
  const [refreshing, setRefreshing] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState("");
  const prefix =
    period === "TRADING_DAYS_7"
      ? "7日"
      : period === "TRADING_DAYS_30"
        ? "30日"
        : "当日";
  const set = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    next.set(key, value);
    setParams(next);
  };
  async function refresh() {
    setRefreshing(true);
    setError("");
    try {
      await model.refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setRefreshing(false);
    }
  }
  return (
    <main id="main" className="overview">
      <div className="page-heading">
        <h1>Overview</h1>
        <div className="page-controls">
          <ToggleGroup
            type="single"
            value={period}
            onValueChange={(v) => v && set("period", v)}
            aria-label="统计周期"
            className="period-control"
          >
            {periods.map(([value, text]) => (
              <ToggleGroupItem
                key={value}
                value={value}
                disabled={
                  model.context.data?.data.period_options.find(
                    (p) => p.period === value,
                  )?.selectable === false
                }
              >
                {text}
              </ToggleGroupItem>
            ))}
          </ToggleGroup>
          <Button
            variant="outline"
            onClick={() => void refresh()}
            disabled={refreshing}
            aria-label="刷新 Overview"
          >
            <RefreshCw
              className={refreshing ? "spin" : ""}
              data-icon="inline-start"
            />
            <span>刷新</span>
          </Button>
        </div>
      </div>
      {(error || model.context.error) && (
        <Notice danger>
          {error || model.context.error?.message}
          <Button variant="link" onClick={() => void refresh()}>
            重试
          </Button>
        </Notice>
      )}
      <div className="overview-summary">
        <section className="realtime-section" aria-label="实时状态">
          <Module query={model.status} label="实时状态">
            {(data) => (
              <div className="realtime-grid">
                <article className="realtime-card">
                  <div className="realtime-icon">
                    <Clock3 aria-hidden="true" />
                  </div>
                  <div className="realtime-content">
                    <h2>当前时段</h2>
                    <div className="session-value">
                      {valueText(data.clock.session, (s) => sessions[s])}
                      <span className="session-marker" />
                    </div>
                  </div>
                </article>
                <article className="realtime-card">
                  <div className="realtime-icon">
                    <Activity aria-hidden="true" />
                  </div>
                  <div className="realtime-content">
                    <h2>Ticker 状态</h2>
                    <div className="ticker-totals">
                      <span
                        className="healthy-count"
                        aria-label={`正常 ${valueText(data.normal_tickers)}`}
                        title="正常"
                      >
                        {valueText(data.normal_tickers)}
                      </span>
                      <i>/</i>
                      <span
                        className="blocked-count"
                        aria-label={`阻塞 ${valueText(data.blocked_tickers)}`}
                        title="阻塞"
                      >
                        {valueText(data.blocked_tickers)}
                      </span>
                    </div>
                  </div>
                </article>
              </div>
            )}
          </Module>
        </section>
        <section className="period-section" aria-label="区间统计">
          <Module query={model.metrics} label="区间统计">
            {(data) => <Metrics data={data} prefix={prefix} />}
          </Module>
        </section>
      </div>
      <div className="workspace-grid">
        <StartForm model={model} />
        <section className="monitor-panel" aria-labelledby="monitor-heading">
          <div className="monitor-heading">
            <div>
              <h2 id="monitor-heading">标的监控</h2>
            </div>
            <div className="filters">
              <SelectFilter
                label="运行状态筛选"
                value={run}
                onChange={(v) => set("run", v)}
                disabled={!filtersSupported}
                options={[
                  ["ALL", "全部运行状态"],
                  ["RUNNING", "运行中"],
                  ["INITIALIZING", "初始化"],
                  ["PAUSED", "已暂停"],
                  ["STOPPED", "已停止"],
                ]}
              />
              <SelectFilter
                label="健康状态筛选"
                value={health}
                onChange={(v) => set("health", v)}
                disabled={!filtersSupported}
                options={[
                  ["ALL", "全部健康状态"],
                  ["NORMAL", "正常"],
                  ["DEGRADED", "降级"],
                  ["BLOCKED", "阻塞"],
                  ["UNKNOWN", "未知"],
                ]}
              />
            </div>
          </div>
          <Module query={model.list} label="标的监控">
            {(page) =>
              page.items.length ? (
                <>
                  <Table className="monitor-table">
                    <TableHeader>
                      <TableRow>
                        {[
                          "Ticker",
                          "监测模式",
                          "状态",
                          "最近消息",
                          "Message Bus 状态",
                          `${prefix}交易`,
                          prefix === "当日" ? "消息数量" : `${prefix}消息`,
                          `${prefix}成本`,
                          "操作",
                        ].map((title, i) => (
                          <TableHead
                            key={title}
                            className={i >= 5 && i <= 7 ? "numeric" : undefined}
                          >
                            {title}
                          </TableHead>
                        ))}
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {page.items
                        .filter((row) => !row.state.removed)
                        .map((row) => (
                          <MonitorRow
                            key={row.state.ticker}
                            row={row}
                            prefix={prefix}
                            pending={model.operations.data[row.state.ticker]}
                            canOperate={
                              !!model.principal.data?.data.can_operate
                            }
                          />
                        ))}
                    </TableBody>
                  </Table>
                  {page.has_more && (
                    <div className="list-footer">
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={loadingMore}
                        onClick={() => {
                          setLoadingMore(true);
                          void model
                            .nextPage()
                            .catch((e) => setError((e as Error).message))
                            .finally(() => setLoadingMore(false));
                        }}
                      >
                        {loadingMore ? "加载中…" : "加载更多"}
                      </Button>
                    </div>
                  )}
                </>
              ) : (
                <Empty className="monitor-empty">
                  <EmptyHeader>
                    <ScanLine className="empty-icon" />
                    <EmptyTitle>
                      {run !== "ALL" || health !== "ALL"
                        ? "没有符合筛选的标的"
                        : "暂无监控标的"}
                    </EmptyTitle>
                  </EmptyHeader>
                  {(run !== "ALL" || health !== "ALL") && (
                    <Button
                      variant="outline"
                      onClick={() => {
                        const next = new URLSearchParams(params);
                        next.delete("run");
                        next.delete("health");
                        setParams(next);
                      }}
                    >
                      清除筛选
                    </Button>
                  )}
                </Empty>
              )
            }
          </Module>
        </section>
      </div>
    </main>
  );
}
