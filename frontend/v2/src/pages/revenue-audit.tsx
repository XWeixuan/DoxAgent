import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import {
  ChartNoAxesCombined,
  Wallet,
  ChevronLeft,
  ChevronRight,
  X,
  ExternalLink,
  Clock3,
} from "lucide-react";
import type {
  Period,
  RevenueMode,
  RevenueRecord,
  RevenueReport,
  RevenueLeg,
} from "@contract";
import { useRuntime } from "@/core/runtime";
import { queryString } from "@/core/api";
import { formatInstant, valueText } from "@/core/format";
import { Choices, DetailPanel, PageTitle } from "@/components/page-kit";
import { periods } from "@/components/business-metrics";
import { Notice } from "@/components/state";
import { Button } from "@/components/ui/button";
import "./revenue-audit.css";

const modes = [
  ["ACTUAL", "实际成交"],
  ["ALL", "全部记录"],
  ["UNEXECUTED", "未执行意图"],
] as const;
const amount = (v: string | null | undefined, signed = true) =>
  v == null
    ? "—"
    : `${signed && Number(v) > 0 ? "+" : Number(v) < 0 ? "−" : ""}$${Math.abs(Number(v)).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const percent = (v: string | null | undefined) =>
  v == null
    ? "—"
    : `${Number(v) > 0 ? "+" : ""}${(Number(v) * 100).toFixed(2)}%`;
const tone = (v: string | null | undefined) =>
  v == null || Number(v) === 0
    ? ""
    : Number(v) > 0
      ? "revenue-profit"
      : "revenue-loss";
const time = (v: string | null) => (v ? formatInstant(v) : "—");
const state = (r: RevenueRecord) =>
  r.kind === "PENDING"
    ? "执行中"
    : r.kind === "UNEXECUTED"
      ? "未执行"
      : r.completed
        ? "已平仓"
        : r.status === "PARTIAL_FILLED"
          ? "部分成交"
          : r.legs.some((l) => Number(l.qty) > 0)
            ? "持仓中"
            : "待对账";
const reasonNames: Record<string, string> = {
  RUNTIME_DELAY_EXCEEDED: "Runtime 消费延迟超过 12 分钟",
  EXPIRED_SEMANTIC_DAY: "交易意图已过期",
  BANNED_TRADE_DAY: "特殊日期禁止交易",
  DIRECTION_DISABLED: "交易配置禁止该方向",
  CASH_SHORT_SUBSTITUTE_DISABLED: "未开启现金账户 SHORT 替代",
  PORTFOLIO_CAPACITY_EXHAUSTED: "可用策略资金或名额不足",
  DELIVERY_FAILED: "执行端接收失败",
  ENTRY_RETRIES_EXHAUSTED: "三次下单机会已用尽",
  ENTRY_SPREAD_TIMEOUT: "盘口观察窗口未通过",
  ENTRY_BOOK_TIMEOUT: "30 秒盘口观察窗口未通过",
  RETRY_BUDGET_EXHAUSTED: "三次下单机会已用尽",
  INSUFFICIENT_MARGIN: "可用保证金不足",
  ENTRY_FAILURE_BUDGET_EXHAUSTED: "执行或账户连接连续失败，已放弃入场",
  EXECUTION_CONFIGURATION_NOT_APPLIED: "交易配置尚未获执行端应用确认",
  PRICE_OUTSIDE_LIMIT: "盘口连续越过当前限价，提前撤单",
};
const reason = (v: string) => reasonNames[v] || v;

export function RevenueAudit() {
  const { api, scope, query: cache } = useRuntime();
  const [pageId] = useState(() => crypto.randomUUID());
  const [environment, setEnvironment] = useState<"LIVE" | "PAPER">("PAPER");
  const [period, setPeriod] = useState<Period>("ALL");
  const [mode, setMode] = useState<RevenueMode>("ACTUAL");
  const [offset, setOffset] = useState(0),
    [selected, setSelected] = useState<string | null>(null);
  const [calculating, setCalculating] = useState(false),
    [lazyError, setLazyError] = useState("");
  const evaluated = useRef(new Set<string>());
  const capture = useQuery({
    queryKey: [scope, "revenue-frame", pageId],
    queryFn: ({ signal }) =>
      api.request("RevenueSnapshot", "/audit/revenue/refresh", {
        method: "POST",
        body: {},
        signal,
      }),
  });
  const frame = capture.data?.data.frame_id;
  const report = useQuery({
    queryKey: [
      scope,
      "revenue-report",
      frame,
      environment,
      period,
      mode,
      offset,
    ],
    enabled: !!frame,
    queryFn: ({ signal }) =>
      api.request(
        "RevenueReport",
        "/audit/revenue/report" +
          queryString({
            frame_id: frame,
            environment,
            period,
            mode,
            offset: String(offset),
            limit: "12",
          }),
        { signal },
      ),
  });
  const data = report.data?.data.data;
  useEffect(() => {
    if (!frame || !data || mode === "ACTUAL") return;
    const ids = data.hypothesis_pending_ids;
    const key = JSON.stringify([frame, environment, ids]);
    if (!ids.length || evaluated.current.has(key)) return;
    evaluated.current.add(key);
    setCalculating(true);
    setLazyError("");
    let active = true;
    void cache
      .fetchQuery({
        queryKey: [scope, "revenue-evaluate", frame, environment, ids],
        queryFn: ({ signal }) =>
          api.request("RevenueEvaluation", "/audit/revenue/hypotheticals", {
            method: "POST",
            signal,
            body: { frame_id: frame, environment, record_ids: ids },
          }),
      })
      .then(() => {
        return cache.invalidateQueries({
          queryKey: [scope, "revenue-report", frame, environment],
        });
      })
      .catch((e: Error) => {
        if (active) setLazyError(e.message);
      })
      .finally(() => {
        if (active) setCalculating(false);
      });
    return () => {
      active = false;
      setCalculating(false);
    };
  }, [api, cache, scope, frame, environment, mode, data]);
  const change = (apply: () => void) => {
    apply();
    setOffset(0);
    setSelected(null);
    setLazyError("");
  };
  return (
    <section className="revenue-audit">
      <div className="audit-controls revenue-controls">
        <Choices
          label="收益账户"
          value={environment}
          items={[
            ["LIVE", "LIVE"],
            ["PAPER", "PAPER"],
          ]}
          onChange={(v) => change(() => setEnvironment(v as "LIVE" | "PAPER"))}
        />
        <div className="revenue-control-right">
          <Choices
            label="收益期间"
            value={period}
            items={periods}
            onChange={(v) => change(() => setPeriod(v as Period))}
          />
          <PageTitle
            title=""
            refresh={async () => {
              setSelected(null);
              await capture.refetch();
            }}
          />
        </div>
      </div>
      {capture.error && <Notice danger>{capture.error.message}</Notice>}
      {report.error && <Notice danger>{report.error.message}</Notice>}
      {lazyError && <Notice danger>{lazyError}</Notice>}
      {!data ? (
        <div className="module-loading" role="status">
          {capture.isPending ? "正在更新收益快照…" : "正在读取收益审计…"}
        </div>
      ) : (
        <>
          <div className="revenue-asof">
            截至 {time(data.as_of)}（美东）
            {mode !== "ACTUAL" && (
              <span>
                已结算假设 {data.kpis.hypothetical_count} /{" "}
                {data.hypothesis_total} 笔 · 可计美元{" "}
                {data.kpis.hypothetical_usd_count} 笔
                {data.hypothesis_pending_count > 0 &&
                  ` · 待处理 ${data.hypothesis_pending_count} 笔`}
              </span>
            )}
          </div>
          <RevenueKpis data={data} />
          <div className="revenue-middle">
            <section className="chart-panel revenue-curve">
              <header>
                <h2>
                  <ChartNoAxesCombined />
                  累计收益
                </h2>
                <div className="revenue-legend">
                  <span>
                    <i />
                    {mode === "ACTUAL"
                      ? "净收益"
                      : mode === "ALL"
                        ? "净收益（含假设）"
                        : "假设收益"}
                  </span>
                  {mode !== "UNEXECUTED" && (
                    <span>
                      <i />
                      实际已实现
                    </span>
                  )}
                </div>
              </header>
              <RevenueChart data={data} />
            </section>
            <section className="chart-panel revenue-holdings">
              <header>
                <h2>
                  <Wallet />
                  当前持仓
                </h2>
                <span>{data.holdings.length} 笔</span>
              </header>
              <div className="revenue-holding-list">
                {!data.holdings.length ? (
                  <div className="module-empty">暂无持仓</div>
                ) : (
                  data.holdings.map((r) => (
                    <button
                      key={r.id}
                      className="revenue-holding"
                      onClick={() => setSelected(r.id)}
                    >
                      <div className="revenue-holding-heading">
                        <div>
                          <strong>{r.ticker}</strong>
                          <span>
                            {r.direction} · {r.environment}
                          </span>
                        </div>
                        <div className={tone(r.upl)}>
                          <strong>{amount(r.upl)}</strong>
                          <span>{percent(groupUplRoi(r))}</span>
                        </div>
                      </div>
                      <p title={r.title}>{r.title}</p>
                      {r.legs
                        .filter((l) => Number(l.qty) > 0)
                        .map((l) => (
                          <HoldingLeg key={l.id} leg={l} />
                        ))}
                    </button>
                  ))
                )}
              </div>
            </section>
          </div>
          <section className="revenue-records">
            <header>
              <h2>
                交易记录 <span>{data.total}</span>
              </h2>
              <Choices
                label="交易记录筛选"
                value={mode}
                items={modes}
                onChange={(v) => change(() => setMode(v as RevenueMode))}
              />
              {calculating && (
                <span role="status">正在计算所选期间假设收益…</span>
              )}
            </header>
            <table className="revenue-table">
              <thead>
                <tr>
                  <th>意图时间</th>
                  <th>Ticker / 方向 · 消息</th>
                  <th>账户</th>
                  <th>入场</th>
                  <th>退出</th>
                  <th>区间收益</th>
                  <th>状态</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((r) => {
                  const main = r.legs.find((l) => l.role === "MAIN"),
                    h = r.hypothesis;
                  return (
                    <tr
                      key={r.id}
                      className={
                        r.kind !== "ACTUAL" ? "revenue-unexecuted" : ""
                      }
                      onClick={() => setSelected(r.id)}
                    >
                      <td>{time(r.intent_at)}</td>
                      <td>
                        <button
                          className="revenue-record-link"
                          onClick={(e) => {
                            e.stopPropagation();
                            setSelected(r.id);
                          }}
                        >
                          <strong>{r.ticker}</strong>
                          <span>{r.direction}</span>
                          <p title={r.title}>{r.title}</p>
                        </button>
                      </td>
                      <td>{r.environment}</td>
                      <td>
                        {main ? (
                          <>
                            {amount(main.entry_price, false)}
                            <small>
                              {main.asset} · {main.entry_qty} 股
                            </small>
                          </>
                        ) : (
                          "—"
                        )}
                      </td>
                      <td>{main ? amount(main.exit_price, false) : "—"}</td>
                      <td className={r.kind === "ACTUAL" ? tone(r.net) : ""}>
                        {r.kind === "ACTUAL" ? (
                          <>
                            <strong>{amount(r.net)}</strong>
                            <small>{percent(r.roi)}</small>
                          </>
                        ) : (
                          <>
                            <strong>
                              {h?.state === "PRICED" ? amount(h.amount) : "—"}
                            </strong>
                            <small>
                              {h?.state === "WAIT_EXIT"
                                ? "待假设退出"
                                : h?.state === "UNPRICEABLE"
                                  ? "报价不可用"
                                  : `假设 ${percent(h?.roi)}`}
                            </small>
                          </>
                        )}
                      </td>
                      <td>
                        <span className="revenue-status">{state(r)}</span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {!data.items.length && (
              <div className="module-empty">
                所选期间暂无
                {mode === "ACTUAL"
                  ? "实际成交"
                  : mode === "UNEXECUTED"
                    ? "未执行意图"
                    : "交易记录"}
              </div>
            )}
            <footer>
              <span>
                {data.total
                  ? `${offset + 1}–${Math.min(offset + data.limit, data.total)} / ${data.total}`
                  : "0 条记录"}
              </span>
              <div>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={offset === 0 || report.isFetching}
                  onClick={() => setOffset(Math.max(0, offset - 12))}
                >
                  <ChevronLeft />
                  上一页
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={offset + 12 >= data.total || report.isFetching}
                  onClick={() => setOffset(offset + 12)}
                >
                  下一页
                  <ChevronRight />
                </Button>
              </div>
            </footer>
          </section>
        </>
      )}
      {selected && frame && (
        <RevenueDrawer
          identity={selected}
          environment={environment}
          frame={frame}
          close={() => setSelected(null)}
        />
      )}
    </section>
  );
}

function groupUplRoi(r: RevenueRecord) {
  const cost = r.legs.reduce(
    (v, l) => v + Number(l.qty) * Number(l.holding_price),
    0,
  );
  return r.upl !== null && cost ? String(Number(r.upl) / cost) : null;
}
function HoldingLeg({ leg: l }: { leg: RevenueLeg }) {
  return (
    <div className="revenue-holding-leg">
      <div>
        <span>{l.role === "MAIN" ? "主仓" : "对冲"}</span>
        <strong>{l.asset}</strong>
        <span>{l.sign === 1 ? "LONG" : "SHORT"}</span>
        <b className={tone(l.upl)}>{percent(l.roi)}</b>
      </div>
      <div>
        <span>
          {l.qty} 股 / {amount(l.holding_price, false)}
        </span>
        <span>
          <Clock3 />
          {time(l.exit_at)}
        </span>
      </div>
      <div className="revenue-opened">开仓 {time(l.opened_at)}</div>
    </div>
  );
}

function RevenueKpis({ data: d }: { data: RevenueReport }) {
  const k = d.kpis;
  return (
    <div className="audit-metrics revenue-kpis">
      <article>
        <span>当前策略总资产</span>
        <strong>{amount(k.strategy_value, false)}</strong>
        <span>自有股票市值＋策略现金</span>
      </article>
      <article>
        <span>
          {d.mode === "ACTUAL"
            ? "净收益 / 收益率"
            : d.mode === "ALL"
              ? "潜在净收益 / 收益率"
              : "假设收益 / 收益率"}
        </span>
        <strong className={d.mode === "ACTUAL" ? tone(k.net) : ""}>
          {amount(k.net)}
        </strong>
        <span className={d.mode === "ACTUAL" ? tone(k.roi) : ""}>
          {percent(k.roi)}
        </span>
      </article>
      <article>
        <span>
          {d.mode === "UNEXECUTED" ? "已结算假设收益" : "实际已实现净收益"}
        </span>
        <strong className={d.mode === "UNEXECUTED" ? "" : tone(k.realized)}>
          {amount(k.realized)}
        </strong>
        <span>
          {d.mode === "UNEXECUTED"
            ? "仅计有可靠美元额度的结果"
            : "扣除已归属交易成本"}
        </span>
      </article>
      <article>
        <span>当前浮动盈亏</span>
        <strong className={tone(k.upl)}>{amount(k.upl)}</strong>
        <span>实际主仓与对冲仓合计</span>
      </article>
      <article>
        <span>实际交易 / 交易意图</span>
        <strong>
          {k.actual_count}
          <em> / {k.intent_count}</em>
        </strong>
        <span>已完整平仓 {k.completed_count} 笔</span>
      </article>
      <article>
        <span>
          {d.mode === "ACTUAL"
            ? "交易胜率"
            : d.mode === "ALL"
              ? "综合胜率（含假设）"
              : "假设胜率"}
        </span>
        <strong>
          {k.win_rate == null
            ? "—"
            : `${(Number(k.win_rate) * 100).toFixed(1)}%`}
        </strong>
        <span>
          {d.mode === "ACTUAL" ? "按已完整平仓交易计算" : "仅计可结算结果"}
        </span>
      </article>
      <article>
        <span>实际交易费用</span>
        <strong>{amount(k.fees, false)}</strong>
        <span>实际回报及归属费用</span>
      </article>
    </div>
  );
}

function RevenueChart({ data: d }: { data: RevenueReport }) {
  const [hover, setHover] = useState<number | null>(null);
  const points = d.curve;
  const values = points
    .flatMap((p) => [p.net, d.mode === "UNEXECUTED" ? null : p.realized])
    .filter((x): x is string => x !== null)
    .map(Number);
  const low = Math.min(0, ...values),
    high = Math.max(0, ...values);
  const min = low === high ? low - 5 : low,
    max = low === high ? high + 5 : high;
  const range = max - min;
  const left = 66,
    right = 770,
    top = 24,
    bottom = 276;
  const begin = Date.parse(d.start_at),
    end = Date.parse(d.end_at);
  const x = (at: string) =>
    left + ((Date.parse(at) - begin) / (end - begin || 1)) * (right - left);
  const y = (value: number) =>
    bottom - ((value - min) / range) * (bottom - top);
  const path = (key: "net" | "realized") => {
    let gap = true;
    return points
      .map((p) => {
        if (p[key] === null) {
          gap = true;
          return "";
        }
        const command = gap
          ? "M"
          : key === "realized" || d.mode === "UNEXECUTED"
            ? "H"
            : "L";
        gap = false;
        if (command === "H") return `H${x(p.at)}V${y(Number(p[key]))}`;
        return `${command}${x(p.at)},${y(Number(p[key]))}`;
      })
      .join(" ");
  };
  const selected = hover == null ? null : points[hover];
  return (
    <div className="revenue-chart-body">
      <svg
        viewBox="0 0 800 322"
        role="img"
        aria-label="累计净收益与实际已实现净收益曲线"
        onMouseLeave={() => setHover(null)}
      >
        {[0, 0.25, 0.5, 0.75, 1].map((f) => (
          <g key={f}>
            <line
              x1={left}
              x2={right}
              y1={top + (bottom - top) * f}
              y2={top + (bottom - top) * f}
              className="revenue-grid"
            />
            <text
              x={left - 10}
              y={top + (bottom - top) * f + 5}
              textAnchor="end"
            >
              ${(max - range * f).toFixed(0)}
            </text>
          </g>
        ))}
        <path d={path("net")} className="revenue-net-line" />
        {d.mode !== "UNEXECUTED" && (
          <path d={path("realized")} className="revenue-realized-line" />
        )}
        {points.map(
          (p) =>
            p.net !== null && (
              <circle
                key={p.at}
                cx={x(p.at)}
                cy={y(Number(p.net))}
                r={3}
                className="revenue-point"
              />
            ),
        )}
        <text x={left} y={308}>
          {time(d.start_at)}
        </text>
        <text x={right} y={308} textAnchor="end">
          {time(d.end_at)}
        </text>
        {points.map((p, i) => (
          <rect
            key={p.at + "hit"}
            x={Math.max(left, x(p.at) - 12)}
            y={top}
            width={24}
            height={bottom - top}
            fill="transparent"
            onMouseEnter={() => setHover(i)}
          />
        ))}
      </svg>
      {selected && (
        <div className="revenue-chart-tooltip">
          {time(selected.at)}
          <strong>{amount(selected.net)}</strong>
          {d.mode !== "UNEXECUTED" && (
            <span>已实现 {amount(selected.realized)}</span>
          )}
        </div>
      )}
    </div>
  );
}

function RevenueDrawer({
  identity,
  environment,
  frame,
  close,
}: {
  identity: string;
  environment: string;
  frame: string;
  close: () => void;
}) {
  const { api, scope } = useRuntime();
  const query = useQuery({
    queryKey: [scope, "revenue-detail", frame, environment, identity],
    queryFn: ({ signal }) =>
      api.request(
        "RevenueDetail",
        `/audit/revenue/records/${encodeURIComponent(identity)}` +
          queryString({ frame_id: frame, environment }),
        { signal },
      ),
  });
  const r = query.data?.data.data;
  return (
    <DetailPanel label="交易收益详情" className="revenue-drawer" close={close}>
      <header>
        <h2>交易收益详情</h2>
        <Button
          variant="ghost"
          size="icon"
          aria-label="关闭详情"
          onClick={close}
        >
          <X />
        </Button>
      </header>
      {query.error && <Notice danger>{query.error.message}</Notice>}
      {!r ? (
        <div className="module-loading">正在读取交易详情…</div>
      ) : (
        <>
          <section className="revenue-detail-intro">
            <div>
              <h3>
                {r.ticker}
                <span>
                  {r.direction} · {r.environment}
                </span>
              </h3>
              <span className="revenue-status">{state(r)}</span>
            </div>
            <p>{r.title}</p>
            <span>交易意图 {time(r.intent_at)}</span>
            {r.case_id && (
              <Link
                to={`/ticker/${encodeURIComponent(r.ticker)}/runtime?case=${encodeURIComponent(r.case_id)}`}
              >
                查看 W3 Case
                <ExternalLink />
              </Link>
            )}
          </section>
          {r.kind === "ACTUAL" ? (
            <>
              <div className="revenue-detail-result">
                <span>组合净收益</span>
                <strong className={tone(r.net)}>{amount(r.net)}</strong>
                <span>{percent(r.roi)}</span>
              </div>
              <section>
                <h3>收益拆解</h3>
                <p>组合实际费用 {amount(r.fees, false)}</p>
                {r.legs.map((l) => (
                  <div className="revenue-leg-detail" key={l.id}>
                    <h4>
                      {l.role === "MAIN" ? "主仓" : "对冲仓"} · {l.asset} ·{" "}
                      {l.sign === 1 ? "LONG" : "SHORT"}
                    </h4>
                    <dl>
                      <dt>开仓数量 / 均价</dt>
                      <dd>
                        {l.entry_qty} 股 / {amount(l.entry_price, false)}
                      </dd>
                      <dt>退出数量 / 均价</dt>
                      <dd>
                        {l.exit_qty} 股 / {amount(l.exit_price, false)}
                      </dd>
                      <dt>实际开仓</dt>
                      <dd>{time(l.opened_at)}</dd>
                      <dt>计划退出</dt>
                      <dd>{time(l.exit_at)}</dd>
                      <dt>已实现 / 当前浮盈</dt>
                      <dd>
                        {amount(l.realized)} / {amount(l.upl)}
                      </dd>
                      <dt>实际费用</dt>
                      <dd>{amount(l.fees, false)}</dd>
                    </dl>
                  </div>
                ))}
              </section>
              {!!r.orders.length && (
                <section>
                  <h3>执行过程</h3>
                  <ol className="revenue-timeline">
                    {r.orders
                      .slice()
                      .sort((a, b) =>
                        String(a.sent_at.value).localeCompare(
                          String(b.sent_at.value),
                        ),
                      )
                      .map((o) => (
                        <li key={o.order_attempt_id}>
                          <div>
                            <strong>
                              {o.leg} · {o.role} · {o.traded_ticker}
                            </strong>
                            <span>{o.state}</span>
                          </div>
                          <p>
                            {o.side} {o.quantity} 股 · {o.order_type}{" "}
                            {amount(o.limit_price, false)}
                          </p>
                          <small>
                            {valueText(o.sent_at, time)} ·{" "}
                            {o.broker_status || o.state}
                          </small>
                          {o.reason && <p>{reason(o.reason)}</p>}
                          {r.fills
                            .filter(
                              (f) => f.order_attempt_id === o.order_attempt_id,
                            )
                            .map((f) => (
                              <p key={f.fill_id}>
                                成交 {f.quantity} 股 @ {amount(f.price, false)}{" "}
                                · {time(f.executed_at)}
                              </p>
                            ))}
                        </li>
                      ))}
                  </ol>
                </section>
              )}
            </>
          ) : (
            <section>
              <h3>{r.kind === "PENDING" ? "执行状态" : "未执行原因"}</h3>
              <p>{reason(r.reason)}</p>
              {r.hypothesis && (
                <>
                  <h3>假设结果</h3>
                  <dl>
                    <dt>假设入场</dt>
                    <dd>
                      {time(r.hypothesis.entry_at)} /{" "}
                      {amount(r.hypothesis.entry_price, false)}
                    </dd>
                    <dt>假设退出</dt>
                    <dd>
                      {time(r.hypothesis.exit_at)} /{" "}
                      {amount(r.hypothesis.exit_price, false)}
                    </dd>
                    <dt>假设收益</dt>
                    <dd>
                      {amount(r.hypothesis.amount)} /{" "}
                      {percent(r.hypothesis.roi)}
                    </dd>
                  </dl>
                  {r.hypothesis.state !== "PRICED" && (
                    <p>
                      {r.hypothesis.state === "WAIT_EXIT"
                        ? "尚未到达假设退出时间"
                        : "对应历史报价不可用"}
                    </p>
                  )}
                </>
              )}
            </section>
          )}
        </>
      )}
    </DetailPanel>
  );
}
