import Decimal from "decimal.js";
import type { Value, Metric } from "@contract";
export const unavailable: Record<
  Exclude<Value<unknown>["state"], "AVAILABLE">,
  string
> = {
  NOT_PRODUCED: "未生成",
  NOT_RECORDED: "未记录",
  UNAVAILABLE: "暂无数据",
  NOT_APPLICABLE: "不适用",
  FORBIDDEN: "无权限",
  ERROR: "读取失败",
};
export function valueText<T>(
  value: Value<T>,
  format: (value: T) => string = String,
): string {
  return value.state === "AVAILABLE"
    ? format(value.value)
    : unavailable[value.state];
}
export function decimal(value: string, places = 0) {
  if (!/^-?\d+(\.\d+)?$/.test(value)) return "数据异常";
  const [integer, fraction] = new Decimal(value).toFixed(places).split(".");
  return (
    integer.replace(/\B(?=(\d{3})+(?!\d))/g, ",") +
    (fraction === undefined ? "" : `.${fraction}`)
  );
}
export const metricText = (metric: Metric, money = false) =>
  valueText(
    metric.current,
    (v) =>
      `${money ? "$" : ""}${decimal(v, money || metric.unit === "SECONDS" ? 2 : 0)}`,
  );
export const changeText = (metric: Metric) =>
  valueText(
    metric.change_pct,
    (v) => `${new Decimal(v).gt(0) ? "+" : ""}${decimal(v, 1)}%`,
  );
export const formatInstant = (date: string) =>
  new Intl.DateTimeFormat("zh-CN", {
    timeZone: "America/New_York",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(date));
export function formatSourceSuccess(date: string, semanticDay?: string) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/New_York",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    hourCycle: "h23",
  }).formatToParts(new Date(date));
  const part = (type: string) => parts.find((p) => p.type === type)!.value;
  const localDay = new Date(
    `${part("year")}-${part("month")}-${part("day")}T00:00:00Z`,
  );
  if (Number(part("hour")) < 2) localDay.setUTCDate(localDay.getUTCDate() - 1);
  const same = localDay.toISOString().slice(0, 10) === semanticDay;
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "America/New_York",
    ...(same
      ? { hour: "2-digit", minute: "2-digit", hour12: false }
      : { month: "2-digit", day: "2-digit" }),
  }).format(new Date(date));
}
export const duration = (seconds: number) =>
  seconds < 60
    ? `${Math.floor(seconds)}秒`
    : seconds < 3600
      ? `${Math.floor(seconds / 60)}分钟`
      : `${Math.floor(seconds / 3600)}小时${Math.floor((seconds % 3600) / 60)}分钟`;
