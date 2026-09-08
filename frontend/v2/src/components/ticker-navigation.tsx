import {
  BookOpen,
  Telescope,
  GitBranch,
  Library,
  Radio,
  Activity,
  ChartNoAxesCombined,
  ArrowLeftRight,
} from "lucide-react";
import { useState } from "react";
import { NavLink, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { useRuntime } from "@/core/runtime";
import { queryString, type Endpoints } from "@/core/api";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "./ui/select";
import { Notice } from "./state";
export const tickerPages: Record<string, string> = {
  research: "基础投研",
  expectations: "预期研究",
  strategy: "交易策略",
  events: "事件库",
  "message-bus": "消息总线",
  runtime: "运行状态",
  audit: "收益 / 成本审计",
};
/** Loaded only on ticker routes; navigation shares a single tab-session cache. */
export function TickerSwitcher({
  ticker,
  page,
}: {
  ticker: string;
  page: string;
}) {
  const runtime = useRuntime();
  const { api, query, scope } = runtime,
    navigate = useNavigate();
  const key = [scope, "navigation"];
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const navigation = useQuery({
    queryKey: key,
    queryFn: ({ signal }) =>
      api.request("Navigation", "/tickers?visibility=NAVIGATION&limit=20", {
        signal,
      }),
  });
  const data = navigation.data?.data.data;
  async function change(value: string) {
    if (value !== "__load_more__") {
      navigate(`/ticker/${encodeURIComponent(value)}/${page}`);
      return;
    }
    if (!data?.next_cursor) return;
    setBusy(true);
    setError("");
    try {
      const response = await api.request(
        "Navigation",
        "/tickers" +
          queryString({
            visibility: "NAVIGATION",
            limit: "20",
            cursor: data.next_cursor,
          }),
      );
      const next = response.data.data;
      if (!next || next.snapshot_id !== data.snapshot_id)
        throw new Error("标的列表范围已变化，请重试。");
      if (scope !== runtime.scope) return;
      query.setQueryData<Endpoints["Navigation"]>(key, {
        ...response,
        data: {
          ...response.data,
          data: {
            ...next,
            items: [
              ...data.items,
              ...next.items.filter(
                (row) => !data.items.some((old) => old.ticker === row.ticker),
              ),
            ],
          },
        },
      });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="ticker-switcher">
      <Select
        value={ticker}
        onValueChange={(value) => void change(value)}
        disabled={busy || navigation.isPending || !data}
      >
        <SelectTrigger aria-label="切换标的">
          <ArrowLeftRight aria-hidden="true" />
          <SelectValue>{ticker}</SelectValue>
        </SelectTrigger>
        <SelectContent position="popper">
          <SelectGroup>
            {data?.items
              .filter((row) => !row.removed && !row.initialization_incomplete)
              .map((row) => (
                <SelectItem value={row.ticker} key={row.ticker}>
                  {row.ticker}
                </SelectItem>
              ))}
            {data?.has_more && (
              <SelectItem value="__load_more__">加载更多标的…</SelectItem>
            )}
          </SelectGroup>
        </SelectContent>
      </Select>
      {(error || navigation.error) && (
        <Notice danger>{error || navigation.error?.message}</Notice>
      )}
    </div>
  );
}

const pageIcons = [
  BookOpen,
  Telescope,
  GitBranch,
  Library,
  Radio,
  Activity,
  ChartNoAxesCombined,
];
export function TickerNavigation({ ticker }: { ticker: string; page: string }) {
  return (
    <div className="ticker-navigation">
      <nav aria-label="标的页面">
        {Object.entries(tickerPages).map(([path, label], index) => {
          const Icon = pageIcons[index];
          return (
            <NavLink
              key={path}
              to={`/ticker/${encodeURIComponent(ticker)}/${path}`}
            >
              <Icon aria-hidden="true" />
              <span>{label}</span>
            </NavLink>
          );
        })}
      </nav>
    </div>
  );
}
