import { useState } from "react";
import {
  Coins,
  ChartNoAxesCombined,
  ArrowDownToLine,
  ArrowUpFromLine,
  Database,
  Gauge,
  ChartPie,
  Cpu,
  Code2,
  Wallet,
} from "lucide-react";
import { useParams, useSearchParams, Link } from "react-router-dom";
import Decimal from "decimal.js";
import type {
  Period,
  CostReport,
  CostTrend,
  Value,
  UsageTotals,
} from "@contract";
import { usePageContext, useRead } from "@/core/page-query";
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
const money = (v: Value<string>) => valueText(v, (x) => "$" + decimal(x, 4));
const ratio = (v: Value<string>) =>
  valueText(v, (x) => decimal(new Decimal(x).mul(100).toString(), 1) + "%");

export default function Audit() {
  const { ticker = "" } = useParams();
  const [p, setP] = useSearchParams();
  const revenue = p.get("audit") === "REVENUE";
  return (
    <>
      <div className="business-heading">
        <h1>收益 / 成本审计</h1>
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
  const scope = p.get("scope") === "CODEX" ? "CODEX" : "API";
  const dimension = p.get("dimension") === "MODEL" ? "MODEL" : "NODE";
  const period = (periods.find((x) => x[0] === p.get("period"))?.[0] ??
    "PREVIOUS_TRADING_DAY") as Period;
  // A global view lets the ticker selector include inactive and shared owners.
  const c = usePageContext("", "COST", period),
    view = c.data?.data.view_id;
  const selection = p.get("ticker") ?? (ticker || "ALL");
  const set = (key: string, value: string) => {
    const n = new URLSearchParams(p);
    if (value) n.set(key, value);
    else n.delete(key);
    if (key !== "offset") n.delete("offset");
    if (["scope", "period", "ticker"].includes(key)) {
      n.delete("node");
      n.delete("provider");
      n.delete("model");
    }
    setP(n);
  };
  const report = useRead(
    "CostReport",
    view
      ? "/audit/cost/report" +
          queryString({
            view_id: view,
            scope,
            dimension,
            ticker_selector: selection,
            node_id: p.get("node") ?? undefined,
            provider: p.get("provider") ?? undefined,
            model_id: p.get("model") ?? undefined,
            offset: p.get("offset") ?? "0",
            limit: "12",
          })
      : null,
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
          icons={{ API: Cpu, CODEX: Code2 }}
          label="调用类型"
          onChange={(v) => set("scope", v)}
        />
        <PeriodPicker
          value={period}
          options={c.data?.data.period_options}
          onChange={(v) => set("period", v)}
        />
        <PageTitle title="" refresh={c.refetch} />
      </div>
      {c.error && <Notice danger>{c.error.message}</Notice>}
      <Module query={report} label="成本审计">
        {(d) => (
          <Report
            data={d}
            scope={scope}
            dimension={dimension}
            selection={selection}
            p={p}
            set={set}
            setP={setP}
          />
        )}
      </Module>
    </>
  );
}
function Report({
  data: d,
  scope,
  dimension,
  selection,
  p,
  set,
  setP,
}: {
  data: CostReport;
  scope: "API" | "CODEX";
  dimension: "NODE" | "MODEL";
  selection: string;
  p: URLSearchParams;
  set: (k: string, v: string) => void;
  setP: (p: URLSearchParams) => void;
}) {
  const t = d.totals;
  return (
    <>
      <div className="filter-bar audit-report-filters">
        <div className="business-filters">
          <label>
            标的
            <select
              aria-label="审计标的"
              value={selection}
              onChange={(e) => set("ticker", e.target.value)}
            >
              <option value="ALL">全部标的</option>
              {Array.from(
                new Set([
                  ...d.filter_choices.tickers,
                  ...(selection !== "ALL" ? [selection] : []),
                ]),
              ).map((v) => (
                <option key={v} value={v}>
                  {v === "GLOBAL" ? "共享研究" : v}
                </option>
              ))}
            </select>
          </label>
          <label>
            节点
            <select
              aria-label="审计节点"
              value={p.get("node") ?? ""}
              onChange={(e) => set("node", e.target.value)}
            >
              <option value="">全部节点</option>
              {d.filter_choices.nodes.map((v) => (
                <option key={v} value={v}>
                  {v}
                </option>
              ))}
            </select>
          </label>
          <label>
            模型
            <select
              aria-label="审计模型"
              value={
                p.get("model")
                  ? JSON.stringify([p.get("provider"), p.get("model")])
                  : ""
              }
              onChange={(e) => {
                const n = new URLSearchParams(p);
                n.delete("provider");
                n.delete("model");
                n.delete("offset");
                if (e.target.value) {
                  const [provider, model] = JSON.parse(e.target.value);
                  n.set("provider", provider);
                  n.set("model", model);
                }
                setP(n);
              }}
            >
              <option value="">全部模型</option>
              {d.filter_choices.models.map((m) => (
                <option
                  key={m.provider + ":" + m.model_id}
                  value={JSON.stringify([m.provider, m.model_id])}
                >
                  {m.provider} · {m.model_id}
                </option>
              ))}
            </select>
          </label>
        </div>
        {selection !== "ALL" && <Link to="/audit/cost">全部标的审计</Link>}
      </div>
      <UsageKpis totals={t} codex={scope === "CODEX"} />
      <div className="audit-observation-counts">
        <span>
          有效请求 {t.usage_known_attempts} / 已观测 {t.observed_attempts}
        </span>
        {t.usage_unknown_attempts > 0 && (
          <span>缺 usage {t.usage_unknown_attempts}</span>
        )}
        {scope === "API" && (
          <>
            <span>未计价 {t.unpriced_attempts}{t.unknown_model_price_attempts > 0 ? `（价格未配 ${t.unknown_model_price_attempts}）` : ""}</span>
            <span>
              已报费用 {t.reported_cost_attempts} / 估价{" "}
              {t.estimated_cost_attempts}
            </span>
          </>
        )}
        <CoverageNote coverage={d.source_coverage} />
        {t.normalized_attempts < t.observed_attempts && (
          <span>
            历史重算 {t.normalized_attempts}/{t.observed_attempts}
          </span>
        )}
      </div>
      <div className="audit-charts">
        <section className="chart-panel">
          <h2>
            <ChartNoAxesCombined aria-hidden="true" />
            用量与成本趋势
          </h2>
          <Trend data={d.trend} />
        </section>
        <section className="chart-panel">
          <header>
            <h2>
              <ChartPie aria-hidden="true" />
              {scope === "API" ? "已知成本占比" : "Token 占比"}
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
          {d.breakdown.items.length ? (
            <div className="share-bars">
              {d.breakdown.items.slice(0, 12).map((r) => (
                <div key={r.key}>
                  <header>
                    <span>{shareLabel(r.label)}</span>
                    <strong>{new Decimal(r.ratio).mul(100).toFixed(1)}%</strong>
                  </header>
                  <div className="share-track">
                    <span
                      style={{
                        width:
                          new Decimal(r.ratio)
                            .mul(100)
                            .clamp(0, 100)
                            .toString() + "%",
                      }}
                    />
                  </div>
                  <small>
                    {d.breakdown.unit === "USD"
                      ? "$" + decimal(r.value, 4)
                      : decimal(new Decimal(r.value).div(1e6).toString(), 3) +
                        "M"}
                  </small>
                </div>
              ))}
            </div>
          ) : (
            <Notice>当前范围没有可计算的占比</Notice>
          )}
        </section>
      </div>
      <section className="cost-nodes">
        <h2>逐轮模型用量</h2>
        {d.rows.length ? (
          <div className="domain-table-scroll">
            <table className="domain-table cost-table cost-report-table">
              <thead>
                <tr>
                  {[
                    "节点 / 模型",
                    "有效 / 全部请求",
                    "输入 Token",
                    "缓存读取 / 命中率",
                    "缓存创建",
                    "输出 Token",
                    "已知成本",
                  ].map((h) => (
                    <th key={h}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {d.rows.map((r) => {
                  const x = r.totals;
                  return (
                    <tr key={r.key}>
                      <td>
                        <strong>
                          {r.node}
                          {["W1", "W2"].includes(r.node) &&
                          r.round !== "UNKNOWN_ROUND"
                            ? r.round
                            : ""}
                        </strong>
                        <span className="model-name">{r.model}</span>
                        <small>
                          {r.provider}
                          {r.round === "UNKNOWN_ROUND" ? " · 轮次未知" : ""}
                        </small>
                      </td>
                      <td>
                        {x.usage_known_attempts} / {x.observed_attempts}
                      </td>
                      <td>
                        {tokens(x.input_tokens.current)}
                        <small>{money(x.input_cost_usd.current)}</small>
                      </td>
                      <td>
                        {tokens(x.cached_input_tokens.current)}
                        <small>
                          {ratio(x.cache_hit_ratio.current)} ·{" "}
                          {x.cache_ratio_known_attempts} 样本
                        </small>
                      </td>
                      <td>
                        {tokens(x.cache_write_input_tokens.current)}
                        <details>
                          <summary>分项</summary>
                          <small>
                            5m {tokens(x.cache_write_5m_input_tokens.current)} /
                            1h {tokens(x.cache_write_1h_input_tokens.current)}
                          </small>
                          <small>{money(x.cache_write_cost_usd.current)}</small>
                        </details>
                      </td>
                      <td>
                        {tokens(x.output_tokens.current)}
                        <small>{money(x.output_cost_usd.current)}</small>
                      </td>
                      <td>
                        {scope === "CODEX"
                          ? "不适用"
                          : money(x.total_cost_usd.current)}
                        <MetricNote metric={x.total_cost_usd} />
                        <details>
                          <summary>依据</summary>
                          <small>
                            已报 {x.reported_cost_attempts} / 估价{" "}
                            {x.estimated_cost_attempts} / 未计价{" "}
                            {x.unpriced_attempts}
                          </small>
                          <small>
                            USD {valueText(x.cost_native_usd)} / CNY{" "}
                            {valueText(x.cost_native_cny)}
                          </small>
                        </details>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <Notice>当前范围没有调用记录</Notice>
        )}
        {d.row_count > d.limit && (
          <div className="audit-pagination">
            <Button
              variant="outline"
              disabled={!d.offset}
              onClick={() =>
                set("offset", String(Math.max(0, d.offset - d.limit)))
              }
            >
              上一页
            </Button>
            <span>
              {d.offset + 1}–{Math.min(d.offset + d.limit, d.row_count)} /{" "}
              {d.row_count}
            </span>
            <Button
              variant="outline"
              disabled={d.offset + d.limit >= d.row_count}
              onClick={() => set("offset", String(d.offset + d.limit))}
            >
              下一页
            </Button>
          </div>
        )}
      </section>
      {scope === "API" && (
        <p className="coverage-note">
          公开价格估算与供应商已报费用合计；CNY 按固定 6.8 折算
          USD。缺失费用未充零。
        </p>
      )}
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
