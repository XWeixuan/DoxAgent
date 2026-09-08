import { useState } from "react";
import {
  Coins,
  ChartNoAxesCombined,
  ArrowDownToLine,
  ArrowUpFromLine,
  Database,
  Gauge,
  ChartPie,
  Network,
  Cpu,
  Code2,
  Wallet,
} from "lucide-react";
import { useParams, useSearchParams } from "react-router-dom";
import Decimal from "decimal.js";
import type {
  Period,
  CostNodeRow,
  CostTrend,
  Value,
  UsageTotals,
} from "@contract";
import { usePageContext, useRead, tickerPath } from "@/core/page-query";
import { usePages } from "@/core/paged-query";
import { queryString } from "@/core/api";
import { valueText, decimal } from "@/core/format";
import { PageTitle, Choices } from "@/components/page-kit";
import { PeriodPicker, periods } from "@/components/business-metrics";
import { Module, Notice } from "@/components/state";
import { Button } from "@/components/ui/button";
import { CoverageNote, MetricNote } from "./overview/metrics";
const tokens = (v: Value<string>) =>
  valueText(
    v,
    (x) => decimal(new Decimal(x).div(1_000_000).toString(), 3) + "M",
  );
const money = (v: Value<string>) => valueText(v, (x) => "$" + decimal(x, 3));
const ratio = (v: Value<string>) =>
  valueText(v, (x) => decimal(new Decimal(x).mul(100).toString(), 1) + "%");
export default function Audit() {
  const { ticker = "" } = useParams(),
    [p, setP] = useSearchParams();
  const revenue = p.get("audit") === "REVENUE";
  return (
    <>
      <div className="business-heading">
        <h1>{ticker} 收益 / 成本审计</h1>
        <Choices
          value={revenue ? "REVENUE" : "COST"}
          items={[
            ["REVENUE", "收益审计"],
            ["COST", "成本审计"],
          ]}
          icons={{ REVENUE: Wallet, COST: Coins }}
          label="审计类型"
          onChange={(v) => {
            const n = new URLSearchParams(p);
            n.set("audit", v);
            setP(n);
          }}
        />
      </div>
      {revenue ? (
        <div className="module-empty">暂未开放</div>
      ) : (
        <CostAudit ticker={ticker} />
      )}
    </>
  );
}
function CostAudit({ ticker }: { ticker: string }) {
  const [p, setP] = useSearchParams();
  const scope = p.get("scope") === "CODEX" ? "CODEX" : "API",
    dimension = p.get("dimension") === "MODEL" ? "MODEL" : "NODE";
  const period = (periods.find((x) => x[0] === p.get("period"))?.[0] ??
    "PREVIOUS_TRADING_DAY") as Period;
  const c = usePageContext(ticker, "COST", period),
    view = c.data?.data.view_id;
  const set = (key: string, v: string) => {
    const n = new URLSearchParams(p);
    if (v) n.set(key, v);
    else n.delete(key);
    if (key === "scope" || key === "period") {
      n.delete("node");
      n.delete("model");
      n.delete("provider");
    }
    setP(n);
  };
  const base = tickerPath(ticker) + "/audit/cost";
  const qs = queryString({ view_id: view, scope });
  const summary = useRead(
      "CostSummary",
      view ? base + "/summary" + qs : null,
      view,
    ),
    trend = useRead(
      "CostTrend",
      view
        ? base +
            "/trend" +
            queryString({ view_id: view, scope, max_points: "60" })
        : null,
      view,
    ),
    shares = useRead(
      "CostBreakdown",
      view
        ? base +
            "/breakdown" +
            queryString({ view_id: view, scope, dimension, limit: "10" })
        : null,
      view,
    ),
    filters = useRead(
      "CostFilters",
      view
        ? base +
            "/filters" +
            queryString({ view_id: view, scope, limit: "100" })
        : null,
      view,
    );
  const nodes = usePages<CostNodeRow, "CostNodes">(
    "CostNodes",
    view
      ? base +
          "/nodes" +
          queryString({
            view_id: view,
            scope,
            node_id: p.get("node") ?? undefined,
            provider: p.get("provider") ?? undefined,
            model_id: p.get("model") ?? undefined,
            limit: "20",
          })
      : null,
    (r) => r.data,
    view,
  );
  return (
    <>
      <div className="audit-controls">
        <Choices
          value={scope}
          items={[
            ["API", "API"],
            ["CODEX", "Codex"],
          ]}
          onChange={(v) => set("scope", v)}
          icons={{ API: Cpu, CODEX: Code2 }}
          label="调用类型"
        />
        <PeriodPicker
          value={period}
          options={c.data?.data.period_options}
          onChange={(v) => set("period", v)}
        />
        <PageTitle title="" refresh={c.refetch} />
      </div>
      {c.error && <Notice danger>{c.error.message}</Notice>}
      <Module query={summary} label="用量汇总">
        {(d) => (
          <>
            <UsageKpis totals={d.totals} codex={scope === "CODEX"} />
            {scope === "API" &&
              (d.totals.unpriced_request_count.state !== "AVAILABLE" ||
                d.totals.unpriced_request_count.value > 0) && (
                <p className="coverage-note">
                  未计价请求：{valueText(d.totals.unpriced_request_count)}
                </p>
              )}
          </>
        )}
      </Module>
      <div className="audit-charts">
        <section className="chart-panel">
          <h2>
            <ChartNoAxesCombined aria-hidden="true" />
            {scope === "API" ? "用量与成本趋势" : "Token 用量趋势"}
          </h2>
          <Module query={trend} label="趋势">
            {(d) => <Trend data={d} />}
          </Module>
        </section>
        <section className="chart-panel">
          <header>
            <h2>
              <ChartPie aria-hidden="true" />
              {scope === "API" ? "成本占比" : "Token 占比"}
            </h2>
            <Choices
              value={dimension}
              items={[
                ["NODE", "按节点"],
                ["MODEL", "按模型"],
              ]}
              onChange={(v) => set("dimension", v)}
              label="占比维度"
            />
          </header>
          <Module query={shares} label="占比">
            {(d) =>
              d.items.length ? (
                <div className="share-bars">
                  <CoverageNote coverage={d.coverage} />
                  {[...d.items, ...(d.other ? [d.other] : [])].map((r, i) => (
                    <div key={r.key}>
                      <header>
                        <span>{shareLabel(r.label)}</span>
                        <strong>
                          {new Decimal(r.ratio).mul(100).toFixed(1)}%
                        </strong>
                      </header>
                      <div className="share-track">
                        <span
                          style={{
                            width:
                              new Decimal(r.ratio)
                                .mul(100)
                                .clamp(0, 100)
                                .toString() + "%",
                            opacity: 1 - i * 0.06,
                          }}
                        />
                      </div>
                      <small>
                        {d.unit === "USD"
                          ? "$" + decimal(r.value, 3)
                          : decimal(
                              new Decimal(r.value).div(1e6).toString(),
                              3,
                            ) + "M"}
                      </small>
                    </div>
                  ))}
                </div>
              ) : (
                <Notice>当前范围没有可计算的占比</Notice>
              )
            }
          </Module>
        </section>
      </div>
      <section className="cost-nodes">
        <div className="filter-bar">
          <h2>
            <Network aria-hidden="true" />
            节点明细
          </h2>
          <div className="business-filters">
            <label>
              节点
              <select
                value={p.get("node") ?? ""}
                onChange={(e) => set("node", e.target.value)}
              >
                <option value="">全部节点</option>
                {filters.data?.data.data?.nodes.items.map((n) => (
                  <option key={n.node_id} value={n.node_id}>
                    {n.label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              模型
              <select
                value={
                  p.get("model")
                    ? JSON.stringify([p.get("provider"), p.get("model")])
                    : ""
                }
                onChange={(e) => {
                  const n = new URLSearchParams(p);
                  n.delete("provider");
                  n.delete("model");
                  if (e.target.value) {
                    const [provider, model] = JSON.parse(e.target.value);
                    n.set("provider", provider);
                    n.set("model", model);
                  }
                  setP(n);
                }}
              >
                <option value="">全部模型</option>
                {filters.data?.data.data?.models.items.map((m) => (
                  <option
                    key={m.provider + ":" + m.model_id}
                    value={JSON.stringify([m.provider, m.model_id])}
                  >
                    {m.model_id}
                  </option>
                ))}
              </select>
            </label>
          </div>
        </div>
        {filters.error && <Notice danger>{filters.error.message}</Notice>}
        <Module query={nodes} label="节点明细">
          {(d) =>
            d.items.length ? (
              <div className="domain-table-scroll">
                <table className="domain-table cost-table">
                  <thead>
                    <tr>
                      {[
                        "节点",
                        "模型",
                        "请求数量",
                        "Input Token / 成本",
                        "Output Token / 成本",
                        "缓存命中",
                        "命中比例",
                        "平均每请求 Token",
                        "总成本",
                      ].map((h) => (
                        <th key={h}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {d.items.map((n) => (
                      <tr key={n.node_id}>
                        <td>
                          {n.label}
                          <MetricNote metric={n.totals.total_tokens} />
                        </td>
                        <td>
                          {n.models.map((m) => (
                            <span
                              className="model-name"
                              key={m.provider + m.model_id}
                            >
                              {m.model_id}
                            </span>
                          ))}
                        </td>
                        <td>{valueText(n.totals.requests)}</td>
                        <td>
                          {tokens(n.totals.input_tokens.current)}
                          <small>
                            {scope === "CODEX"
                              ? "不适用"
                              : money(n.totals.input_cost_usd.current)}
                          </small>
                        </td>
                        <td>
                          {tokens(n.totals.output_tokens.current)}
                          <small>
                            {scope === "CODEX"
                              ? "不适用"
                              : money(n.totals.output_cost_usd.current)}
                          </small>
                        </td>
                        <td>{tokens(n.totals.cached_input_tokens.current)}</td>
                        <td>{ratio(n.totals.cache_hit_ratio.current)}</td>
                        <td>
                          {tokens(n.totals.average_total_tokens_per_request)}
                        </td>
                        <td>
                          {scope === "CODEX"
                            ? "不适用"
                            : money(n.totals.total_cost_usd.current)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <Notice>当前范围没有节点用量</Notice>
            )
          }
        </Module>
        {nodes.hasNextPage && (
          <Button variant="outline" onClick={() => void nodes.fetchNextPage()}>
            更多节点
          </Button>
        )}
      </section>
    </>
  );
}
function UsageKpis({
  totals: t,
  codex,
}: {
  totals: UsageTotals;
  codex: boolean;
}) {
  return (
    <div className="business-metrics audit-metrics">
      {[
        {
          label: "Token 总量",
          value: tokens(t.total_tokens.current),
          metric: t.total_tokens,
        },
        {
          label: "可计价总成本",
          value: codex ? "不适用" : money(t.total_cost_usd.current),
          metric: t.total_cost_usd,
        },
        {
          label: "Input Token",
          value: tokens(t.input_tokens.current),
          cost: codex ? "不适用" : money(t.input_cost_usd.current),
          metric: t.input_tokens,
        },
        {
          label: "Output Token",
          value: tokens(t.output_tokens.current),
          cost: codex ? "不适用" : money(t.output_cost_usd.current),
          metric: t.output_tokens,
        },
        {
          label: "缓存命中 Token",
          value: tokens(t.cached_input_tokens.current),
          metric: t.cached_input_tokens,
        },
        {
          label: "缓存命中比例",
          value: ratio(t.cache_hit_ratio.current),
          metric: t.cache_hit_ratio,
        },
      ].map((k, i) => (
        <article key={k.label}>
          <span>
            {(() => {
              const Icon = [
                Database,
                Coins,
                ArrowDownToLine,
                ArrowUpFromLine,
                Database,
                Gauge,
              ][i];
              return <Icon aria-hidden="true" />;
            })()}
            {k.label}
          </span>
          <strong>{k.value}</strong>
          {k.cost && <span>{k.cost}</span>}
          <MetricNote metric={k.metric} />
        </article>
      ))}
    </div>
  );
}
function shareLabel(value: string) {
  try {
    const v = JSON.parse(value);
    return Array.isArray(v) ? v.join(" · ") : value;
  } catch {
    return value === "OTHER" ? "其他" : value;
  }
}
function Trend({ data }: { data: CostTrend }) {
  const [hover, setHover] = useState<number | null>(null);
  const points = data.points;
  if (!points.length) return <Notice>当前范围没有趋势数据</Notice>;
  const series = (
    ["total_tokens", ...(data.scope === "API" ? ["cost_usd"] : [])] as (
      "total_tokens" | "cost_usd"
    )[]
  ).filter((key) => points.some((p) => p[key].state === "AVAILABLE"));
  if (!series.length) return <Notice>当前范围没有可用趋势数据</Notice>;
  const maxFor = (key: "total_tokens" | "cost_usd") =>
    Math.max(
      ...points.map((p) =>
        p[key].state === "AVAILABLE" ? Number(p[key].value) : 0,
      ),
      1e-8,
    );
  const active = hover === null ? undefined : points[hover];
  const xFor = (i: number) =>
    points.length === 1 ? 335 : 65 + (i * 540) / (points.length - 1);
  return (
    <figure className="usage-trend">
      <figcaption>
        {points.some((p) => p.coverage.state !== "COMPLETE") && (
          <CoverageNote
            coverage={
              points.find((p) => p.coverage.state === "UNKNOWN")?.coverage ??
              points.find((p) => p.coverage.state !== "COMPLETE")!.coverage
            }
          />
        )}
        <span className="token-legend">Token · M</span>
        {data.scope === "API" && (
          <span className="cost-legend">
            成本 · {series.includes("cost_usd") ? "USD" : "不适用"}
          </span>
        )}
        {active && (
          <output>
            {active.start_at.slice(0, 10)} · {tokens(active.total_tokens)}
            {data.scope === "API" && ` · ${money(active.cost_usd)}`}
          </output>
        )}
      </figcaption>
      <svg
        viewBox="0 0 680 218"
        role="img"
        aria-label={data.scope === "API" ? "用量与成本趋势" : "Token 用量趋势"}
      >
        {[0, 0.5, 1].map((f) => (
          <g key={f}>
            <line
              x1="65"
              x2="605"
              y1={174 - f * 145}
              y2={174 - f * 145}
              className="chart-grid"
            />
            {series.includes("total_tokens") && (
              <text x="56" y={178 - f * 145} textAnchor="end">
                {((maxFor("total_tokens") * f) / 1e6).toFixed(3)}
              </text>
            )}
            {series.includes("cost_usd") && (
              <text x="615" y={178 - f * 145}>
                {(maxFor("cost_usd") * f).toFixed(3)}
              </text>
            )}
          </g>
        ))}
        {points.map(
          (p, i) =>
            (i === 0 ||
              i === points.length - 1 ||
              i % Math.max(1, Math.ceil(points.length / 6)) === 0) && (
              <text key={p.start_at} x={xFor(i)} y="203" textAnchor="middle">
                {p.start_at.slice(5, 10)}
              </text>
            ),
        )}
        {series.map((key, si) => (
          <g key={key} className={"series-" + key}>
            {points.map((p, i) => {
              const val = p[key];
              if (val.state !== "AVAILABLE") return null;
              const x =
                  xFor(i) + (points.length === 1 ? (si === 0 ? -14 : 14) : 0),
                y = 174 - (Number(val.value) / maxFor(key)) * 145,
                prev = points[i - 1]?.[key];
              return (
                <g
                  key={p.start_at}
                  tabIndex={0}
                  aria-label={`${p.start_at.slice(0, 10)} ${key === "total_tokens" ? tokens(val) : money(val)}`}
                  onFocus={() => setHover(i)}
                  onBlur={() => setHover(null)}
                  onMouseEnter={() => setHover(i)}
                  onMouseLeave={() => setHover(null)}
                >
                  {points.length === 1 && (
                    <rect
                      x={x - 9}
                      y={y}
                      width="18"
                      height={174 - y}
                      rx="3"
                      className="chart-single-bar"
                    />
                  )}
                  {i > 0 && prev?.state === "AVAILABLE" && (
                    <>
                      <path
                        className="chart-area"
                        d={`M${xFor(i - 1)} 174 L${xFor(i - 1)} ${174 - (Number(prev.value) / maxFor(key)) * 145} L${x} ${y} L${x} 174Z`}
                      />
                      <line
                        x1={xFor(i - 1)}
                        y1={174 - (Number(prev.value) / maxFor(key)) * 145}
                        x2={x}
                        y2={y}
                        className="chart-line"
                      />
                    </>
                  )}
                  <circle
                    cx={x}
                    cy={y}
                    r={hover === i ? 5 : 3.5}
                    className="chart-dot"
                  >
                    <title>
                      {p.start_at.slice(0, 10)} ·{" "}
                      {key === "total_tokens" ? tokens(val) : money(val)}
                    </title>
                  </circle>
                </g>
              );
            })}
          </g>
        ))}
      </svg>
    </figure>
  );
}
