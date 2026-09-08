import {
  Activity,
  Plus,
  Crosshair,
  PencilLine,
  CircleOff,
  ArrowLeftRight,
  ChartNoAxesCombined,
  Clock3,
  Radio,
  CircleCheck,
  MessagesSquare,
  Coins,
} from "lucide-react";
import type { Metric, Period, PeriodOption } from "@contract";
import { Choices } from "./page-kit";
import { Comparison, MetricNote } from "@/pages/overview/metrics";
import { metricText, valueText, decimal } from "@/core/format";
import Decimal from "decimal.js";
export const periods = [
  ["PREVIOUS_TRADING_DAY", "前一天"],
  ["CURRENT_TRADING_DAY", "当天"],
  ["TRADING_DAYS_7", "7天"],
  ["TRADING_DAYS_30", "30天"],
  ["ALL", "全部"],
] as const;
export function PeriodPicker({
  value,
  onChange,
  options,
}: {
  value: Period;
  onChange: (v: Period) => void;
  options?: PeriodOption[];
}) {
  return (
    <Choices
      value={value}
      onChange={(v) => onChange(v as Period)}
      items={periods}
      label="统计周期"
      disabled={options?.filter((x) => !x.selectable).map((x) => x.period)}
    />
  );
}
const metricIcons: Record<string, import("lucide-react").LucideIcon> = {
  生效数量: Activity,
  "LONG / SHORT": ArrowLeftRight,
  周期新增: Plus,
  周期命中: Crosshair,
  周期修改: PencilLine,
  周期失效: CircleOff,
  周期交易执行: ChartNoAxesCombined,
  连续运行时长: Clock3,
  正文补全成功率: CircleCheck,
  数据源状态: Radio,
  平均轮询延迟: Clock3,
  周期处理: MessagesSquare,
  总成本: Coins,
};
export function MetricStrip({
  items,
  period,
}: {
  items: {
    label: string;
    metric: Metric;
    current?: boolean;
    money?: boolean;
    secondary?: Metric;
  }[];
  period: Period;
}) {
  return (
    <div className="business-metrics">
      {items.map(({ label, metric, current, money, secondary }) => (
        <article key={label} data-current={current || undefined}>
          <span>
            {(() => {
              const Icon = metricIcons[label] ?? ChartNoAxesCombined;
              return <Icon aria-hidden="true" />;
            })()}
            {label === "LONG / SHORT" ? "方向占比" : label}
          </span>
          {label === "LONG / SHORT" && secondary ? (
            <div className="direction-split">
              <span className="long">
                <small>LONG</small>
                <b>
                  {valueText(
                    metric.current,
                    (v) => decimal(new Decimal(v).mul(100).toString(), 1) + "%",
                  )}
                </b>
              </span>
              <span className="short">
                <small>SHORT</small>
                <b>
                  {valueText(
                    secondary.current,
                    (v) => decimal(new Decimal(v).mul(100).toString(), 1) + "%",
                  )}
                </b>
              </span>
            </div>
          ) : (
            <>
              {" "}
              <strong
                data-ratio-pair={
                  (metric.unit === "RATIO" && secondary?.unit === "RATIO") ||
                  undefined
                }
                data-unavailable={
                  metric.current.state !== "AVAILABLE" ||
                  (secondary && secondary.current.state !== "AVAILABLE") ||
                  undefined
                }
              >
                {metric.unit === "RATIO"
                  ? valueText(
                      metric.current,
                      (v) =>
                        decimal(new Decimal(v).mul(100).toString(), 1) + "%",
                    )
                  : metricText(metric, money)}
                {secondary && (
                  <>
                    {" "}
                    /{" "}
                    {secondary.unit === "RATIO"
                      ? valueText(
                          secondary.current,
                          (v) =>
                            decimal(new Decimal(v).mul(100).toString(), 1) +
                            "%",
                        )
                      : metricText(secondary)}
                  </>
                )}
              </strong>
            </>
          )}
          <MetricNote metric={metric} />
          <div className="metric-comparisons">
            {!current && period !== "ALL" && (
              <Comparison
                label={secondary ? label.split(" / ")[0] : undefined}
                metric={metric}
              />
            )}
            {secondary && !current && period !== "ALL" && (
              <Comparison label={label.split(" / ")[1]} metric={secondary} />
            )}
          </div>
        </article>
      ))}
    </div>
  );
}
