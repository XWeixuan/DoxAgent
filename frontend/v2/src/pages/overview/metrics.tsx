import {
  ArrowDown,
  ArrowUp,
  Crosshair,
  ArrowLeftRight,
  MessagesSquare,
  Coins,
  Wrench,
  ChartNoAxesCombined,
} from "lucide-react";
import type { Coverage, Metric, OverviewMetrics } from "@contract";
import { Badge } from "@/components/ui/badge";
import { changeText, metricText } from "@/core/format";

export function Comparison({
  metric,
  label,
}: {
  metric: Metric;
  label?: string;
}) {
  const text = changeText(metric);
  const Icon = text.startsWith("+")
    ? ArrowUp
    : text.startsWith("-")
      ? ArrowDown
      : null;
  return (
    <span
      className="comparison"
      data-direction={
        text.startsWith("+") ? "up" : text.startsWith("-") ? "down" : "flat"
      }
    >
      {label && <span>{label}</span>}
      {Icon && <Icon aria-hidden="true" />}
      {text.replace(/^[+-]/, "")}
      {metric.change_pct.state === "AVAILABLE" && (
        <span className="sr-only">
          {text.startsWith("-")
            ? "下降"
            : text.startsWith("+")
              ? "上升"
              : "持平"}
        </span>
      )}
    </span>
  );
}
export function MetricNote({ metric }: { metric: Metric }) {
  return (
    <>
      <CoverageNote coverage={metric.current_coverage} />
      {metric.provisional && (
        <Badge className="coverage-badge tone-warning" variant="outline">
          暂估
        </Badge>
      )}
    </>
  );
}
export function CoverageNote({ coverage }: { coverage: Coverage }) {
  return coverage.state !== "COMPLETE" ? (
    <Badge
      className="coverage-badge tone-warning"
      variant="outline"
      title={coverage.reasons.join(" · ")}
    >
      {coverage.state === "UNKNOWN" ? "覆盖未知" : "部分覆盖"}
    </Badge>
  ) : null;
}
export function Metrics({
  data,
  prefix,
}: {
  data: OverviewMetrics;
  prefix: string;
}) {
  const cards: {
    title: string;
    content: React.ReactNode;
    foot: React.ReactNode;
    note?: Metric;
  }[] = [
    {
      title: `${prefix} Policy 命中`,
      content: (
        <>
          {metricText(data.policy_hits)}
          <small>条</small>
        </>
      ),
      foot: <Comparison metric={data.policy_hits} />,
      note: data.policy_hits,
    },
    {
      title: `${prefix}交易`,
      content: (
        <>
          {metricText(data.trade_executed)}
          <span className="metric-divider">/</span>
          {metricText(data.trade_triggered)}
        </>
      ),
      foot: (
        <>
          <Comparison label="执行" metric={data.trade_executed} />
          <Comparison label="触发" metric={data.trade_triggered} />
        </>
      ),
      note: data.trade_executed,
    },
    {
      title: `${prefix}消息`,
      content: (
        <>
          {metricText(data.messages)}
          <small>条</small>
        </>
      ),
      foot: <Comparison metric={data.messages} />,
      note: data.messages,
    },
    {
      title: `${prefix} token 成本`,
      content: metricText(data.api_token_cost, true),
      foot: <Comparison metric={data.api_token_cost} />,
      note: data.api_token_cost,
    },
    {
      title: "非例行维护触发数",
      content: (
        <>
          {metricText(data.nonroutine_repairs)}
          <small>次</small>
        </>
      ),
      foot: null,
    },
    {
      title: `${prefix}收益变化`,
      content: (
        <div className="pnl-values">
          <div>
            <small>Paper</small>
            <span>{metricText(data.paper_realized_net_pnl, true)}</span>
            <MetricNote metric={data.paper_realized_net_pnl} />
          </div>
          <div>
            <small>Live</small>
            <span>{metricText(data.live_realized_net_pnl, true)}</span>
            <MetricNote metric={data.live_realized_net_pnl} />
          </div>
        </div>
      ),
      foot: (
        <>
          <Comparison label="P" metric={data.paper_realized_net_pnl} />
          <Comparison label="L" metric={data.live_realized_net_pnl} />
        </>
      ),
    },
  ];
  return (
    <div className="metrics-grid">
      {cards.map((card, i) => (
        <article className="metric-card" key={card.title}>
          <h3>
            {(() => {
              const Icon = [
                Crosshair,
                ArrowLeftRight,
                MessagesSquare,
                Coins,
                Wrench,
                ChartNoAxesCombined,
              ][i];
              return <Icon aria-hidden="true" />;
            })()}
            {card.title}
          </h3>
          <div className="metric-value">{card.content}</div>
          {card.note && <MetricNote metric={card.note} />}
          <div className="metric-foot">{card.foot}</div>
        </article>
      ))}
    </div>
  );
}
