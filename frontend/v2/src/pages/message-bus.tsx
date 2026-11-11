import { LoadMore } from "@/components/load-more";
import { Badge } from "@/components/ui/badge";
import {
  Radio,
  Settings2,
  MessagesSquare,
  Timer,
  Plus,
  CircleCheck,
  Search,
  ChevronDown,
  Clock3,
} from "lucide-react";
import { useEffect, useState, useRef } from "react";
import { useQuery } from "@tanstack/react-query";
import { useParams, useSearchParams } from "react-router-dom";
import type { Period, MessageSummary, SourceStatus, Response } from "@contract";
import { useRuntime } from "@/core/runtime";
import { usePageContext, tickerPath } from "@/core/page-query";
import { useBusFeed } from "./use-bus-feed";
import { useMinuteReads } from "@/core/live-query";
import { queryString } from "@/core/api";
import {
  formatInstant,
  formatSourceSuccess,
  valueText,
  metricText,
} from "@/core/format";
import { PageTitle, Choices, ContentReader } from "@/components/page-kit";
import { PeriodPicker, periods } from "@/components/business-metrics";
import { Comparison } from "./overview/metrics";
import { Module, Notice } from "@/components/state";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import BindingSettings from "./source-settings";
const routes = [
  ["ALL", "全部路由"],
  ["ARCHIVE", "ARCHIVE"],
  ["TRADE", "TRADE"],
  ["ADD_TO_DELTA", "ADD_TO_DELTA"],
  ["W3_PENDING", "W3"],
  ["FAILED", "失败"],
  ["NOT_PROCESSED", "待处理"],
];
const seconds = (value: number | null | undefined) =>
  value == null ? "—" : value.toFixed(2) + "s";
const ratio = (value: number | null | undefined) =>
  value == null ? "无样本" : (value * 100).toFixed(1) + "%";
export default function MessageBus() {
  const { ticker = "" } = useParams();
  const [p, setP] = useSearchParams();
  const config = p.get("mode") === "CONFIG";
  const period = (periods.find((x) => x[0] === p.get("period"))?.[0] ??
    "CURRENT_TRADING_DAY") as Period;
  const ctx = usePageContext(ticker, "MESSAGE_BUS", period, { renew: false });
  const [refresh, setRefresh] = useState(0);
  return (
    <>
      <PageTitle
        title={`${ticker} 消息总线`}
        refresh={async () => {
          await ctx.refetch();
          setRefresh((n) => n + 1);
        }}
      >
        {!config && (
          <PeriodPicker
            value={period}
            options={ctx.data?.data.period_options}
            onChange={(v) => {
              const n = new URLSearchParams(p);
              n.set("period", v);
              setP(n);
            }}
          />
        )}
        <Choices
          value={config ? "CONFIG" : "STREAM"}
          onChange={(v) => {
            const n = new URLSearchParams(p);
            n.set("mode", v);
            setP(n);
          }}
          items={[
            ["STREAM", "消息流"],
            ["CONFIG", "配置"],
          ]}
          label="消息总线模式"
          icons={{ STREAM: MessagesSquare, CONFIG: Settings2 }}
        />
      </PageTitle>
      {ctx.error && <Notice danger>{ctx.error.message}</Notice>}
      {config ? (
        <BindingSettings
          refresh={ctx.refetch}
          ticker={ticker}
          view={ctx.data?.data.view_id}
        />
      ) : ctx.data ? (
        <MessageStream
          key={[
            ticker,
            p.get("kind"),
            p.get("source"),
            p.get("route"),
            p.get("q"),
          ].join(":")}
          ticker={ticker}
          period={period}
          context={ctx.data}
          refresh={refresh}
        />
      ) : null}
    </>
  );
}
function MessageStream({
  ticker,
  period,
  context,
  refresh,
}: {
  ticker: string;
  period: Period;
  context: Response<import("@contract").ReadContext>;
  refresh: number;
}) {
  const { api, scope } = useRuntime();
  const [p, setP] = useSearchParams();
  const [search, setSearch] = useState(p.get("q") ?? ""),
    [searchOpen, setSearchOpen] = useState(!!p.get("q"));
  const scroll = useRef<HTMLDivElement>(null);
  const filters = {
    source_kind: p.get("kind") ?? undefined,
    source_id: p.get("source") ?? undefined,
    route: p.get("route") ?? "ALL",
  };
  const set = (key: string, v: string) => {
    const n = new URLSearchParams(p);
    if (v) n.set(key, v);
    else n.delete(key);
    if (key === "kind") n.delete("source");
    setP(n);
  };
  const dashboard = useQuery({
    queryKey: [
      scope,
      "bus-dashboard",
      ticker,
      period,
      filters.source_kind,
      filters.source_id,
    ],
    queryFn: async ({ signal }) => {
      const current = await api.request(
        "ReadContext",
        "/read-context" +
          queryString({
            ticker,
            page: "MESSAGE_BUS",
            period,
            refresh: "MINUTE",
          }),
        { signal },
      );
      const view = current.data.view_id;
      const response = await api.request(
        "BusDashboard",
        tickerPath(ticker) +
          "/message-bus/dashboard" +
          queryString({
            view_id: view,
            source_id: filters.source_id,
            source_kind: filters.source_kind,
          }),
        { signal, view },
      );
      return { current, response };
    },
    staleTime: 60_000,
    gcTime: 120_000,
    refetchOnMount: true,
    refetchOnWindowFocus: false,
  });
  const minuteError = useMinuteReads(context, async () => {
    await dashboard.refetch();
  });
  useEffect(() => {
    if (refresh) void dashboard.refetch();
  }, [refresh]);
  const messages = useBusFeed(
    ticker,
    { ...filters, q: p.get("q") ?? undefined },
    scroll,
  );
  const data = dashboard.data?.response.data.data,
    metrics = data?.metrics,
    sources = data?.sources ?? [];
  return (
    <>
      <div className="business-metrics bus-metrics" aria-label="消息统计">
        <article>
          <span>
            <Clock3 aria-hidden="true" />
            平均真实抓取延时
          </span>
          <strong>{seconds(data?.mean_capture_seconds)}</strong>
          <small>{data?.capture_samples ?? 0} 条消息</small>
        </article>
        <article>
          <span>
            <Plus aria-hidden="true" />
            周期新增
          </span>
          <strong>
            {metrics ? metricText(metrics.published_revisions) : "—"}
          </strong>
          {metrics && period !== "ALL" && (
            <Comparison metric={metrics.published_revisions} />
          )}
        </article>
        <article>
          <span>
            <Radio aria-hidden="true" />
            消息源状态
          </span>
          <strong>{data ? `已绑定 ${data.counts.total}` : "—"}</strong>
          {data && (
            <small>
              运行 {data.counts.running} · 休眠 {data.counts.sleeping} · 异常{" "}
              {data.counts.abnormal}
              {data.counts.disabled > 0 && ` · 停用 ${data.counts.disabled}`}
              {data.counts.unknown > 0 && ` · 未知 ${data.counts.unknown}`}
            </small>
          )}
        </article>
        <article>
          <span>
            <CircleCheck aria-hidden="true" />
            正文补全成功率
          </span>
          <strong>
            {data ? ratio(metrics?.new_article_body?.ratio) : "—"}
          </strong>
          {metrics?.new_article_body && (
            <small>
              完整 {metrics.new_article_body.full} /{" "}
              {metrics.new_article_body.total} · 待完成{" "}
              {metrics.new_article_body.pending}
            </small>
          )}
        </article>
        <article>
          <span>
            <Clock3 aria-hidden="true" />
            平均端到端耗时
          </span>
          <strong title="首次发现至首次发布；近24h最多20篇，不足5篇取最近5篇">
            {seconds(data?.mean_e2e_seconds)}
          </strong>
          {data && <small>{data.e2e_samples} 条新消息</small>}
        </article>
        <article>
          <span>
            <Timer aria-hidden="true" />
            周期轮询覆盖率
          </span>
          <strong title="每源近20个已结算逻辑轮次；替代路径任一成功，互补单元共同满足">
            {data ? ratio(data.coverage.ratio) : "—"}
          </strong>
          {data && (
            <small>
              近20轮 / 源 · 成功 {data.coverage.succeeded} / 预期{" "}
              {data.coverage.expected}
            </small>
          )}
        </article>
      </div>
      <div className="bus-refresh-feedback" role="status" aria-live="polite">
        {dashboard.error ? (
          <span title={dashboard.error.message}>
            消息统计读取失败{data ? "，已保留上次结果。" : "。"}
            {"requestId" in dashboard.error &&
              Boolean(dashboard.error.requestId) &&
              ` 请求编号：${String(dashboard.error.requestId)}`}
          </span>
        ) : (
          messages.streamError || minuteError
        )}
      </div>
      <div className="bus-layout">
        <section className="stream-panel">
          <div className="stream-toolbar">
            <h2>
              <MessagesSquare aria-hidden="true" />
              消息流
            </h2>
            <div className="business-filters">
              <label>
                <span className="sr-only">类型</span>
                <select
                  value={p.get("kind") ?? ""}
                  onChange={(e) => set("kind", e.target.value)}
                >
                  <option value="">全部类型</option>
                  <option value="api">API</option>
                  <option value="crawler">爬虫</option>
                </select>
              </label>
              <label>
                <span className="sr-only">来源</span>
                <select
                  value={p.get("source") ?? ""}
                  onChange={(e) => set("source", e.target.value)}
                >
                  <option value="">全部来源</option>
                  {sources
                    .filter(
                      (s) =>
                        !filters.source_kind ||
                        s.source.kind === filters.source_kind,
                    )
                    .map((s) => (
                      <option
                        key={s.source.source_id}
                        value={s.source.source_id}
                      >
                        {s.source.name}
                      </option>
                    ))}
                </select>
              </label>
              <label>
                <span className="sr-only">路由</span>
                <select
                  value={filters.route}
                  onChange={(e) => set("route", e.target.value)}
                >
                  {routes.map(([v, l]) => (
                    <option key={v} value={v}>
                      {l}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label="搜索消息"
              aria-expanded={searchOpen}
              onClick={() => setSearchOpen(!searchOpen)}
            >
              <Search />
            </Button>
          </div>
          {searchOpen && (
            <form
              className="message-search"
              onSubmit={(e) => {
                e.preventDefault();
                set("q", search.trim());
              }}
            >
              <Input
                aria-label="搜索标题或正文"
                value={search}
                maxLength={200}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="搜索标题或正文"
                autoFocus
              />
              <Button
                variant="ghost"
                type="submit"
                size="icon-sm"
                aria-label="执行搜索"
              >
                <Search />
              </Button>
              {p.get("q") && (
                <Button
                  variant="ghost"
                  onClick={() => {
                    setSearch("");
                    set("q", "");
                  }}
                >
                  清除
                </Button>
              )}
            </form>
          )}
          <div
            ref={scroll}
            className="message-scroll"
            role="region"
            aria-label="消息列表"
            tabIndex={0}
          >
            <Module query={messages} label="消息流">
              {(d) =>
                d.items.length ? (
                  d.items.map((m) => (
                    <MessageCard
                      key={m.standard_message_id}
                      ticker={ticker}
                      message={m}
                    />
                  ))
                ) : (
                  <Notice>当前范围没有消息</Notice>
                )
              }
            </Module>
            {messages.hasNextPage && (
              <LoadMore
                disabled={messages.isFetchingNextPage}
                onClick={() => void messages.fetchNextPage()}
              >
                展开更多消息
              </LoadMore>
            )}
          </div>
        </section>
        <aside className="source-status-list">
          <h2>
            <Radio aria-hidden="true" />
            消息源状态
          </h2>
          {sources.map((s) => (
            <SourceCard
              key={s.source.binding_id}
              status={s}
              today={s.published_today}
              semanticDay={
                dashboard.data?.current.data.clock.semantic_day ??
                context.data.clock.semantic_day
              }
            />
          ))}
          {!sources.length && data && <p>没有已绑定消息源</p>}
        </aside>
      </div>
    </>
  );
}
export function SourceCard({
  status: s,
  semanticDay,
  today,
}: {
  status: SourceStatus;
  semanticDay?: string;
  today?: import("@contract").Value<import("@contract").Count>;
}) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (s.countdown_state !== "SCHEDULED") return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [s.countdown_state]);
  const remaining =
    s.next_target_at.state === "AVAILABLE"
      ? Math.max(0, (Date.parse(s.next_target_at.value) - now) / 1000)
      : 0;
  const scheduled =
    s.countdown_state === "SCHEDULED" && s.next_target_at.state === "AVAILABLE";
  const progress = scheduled
    ? Math.max(
        0,
        Math.min(1, 1 - remaining / Math.max(1, s.target_interval_seconds)),
      )
    : 0;
  const stateLabel = scheduled
    ? remaining > 0
      ? `${Math.ceil(remaining)}s`
      : "待轮询"
    : {
        PAUSED: "已暂停",
        DISABLED: "已禁用",
        WINDOW_CLOSED: "窗口外",
        CLOSED_SLEEP: "休眠",
        UNKNOWN: "未调度",
        SCHEDULED: "未调度",
      }[s.countdown_state];
  return (
    <article className="source-status">
      <header>
        <div>
          <h3>{s.source.name}</h3>{" "}
          <div className="source-kind">
            <span>{s.source.kind === "api" ? "API" : "爬虫"}</span>
            <span>
              {s.publication_mode === "immediate" ? "单条发布" : "分批发布"}
            </span>
          </div>
        </div>
        <Badge variant={s.current_error ? "destructive" : "secondary"}>
          {s.current_error?.code.startsWith("SOURCE_DEFERRED:")
            ? "采集延期"
            : "上次结果：" +
              {
                succeeded: "正常",
                partial: "部分失败",
                failed: "失败",
                disabled: "禁用",
                never_polled: "未轮询",
              }[s.poll_status]}
        </Badge>
      </header>
      <div className="source-status-body">
        <div
          className="poll-dial"
          role="img"
          aria-label={`轮询周期 ${s.target_interval_seconds} 秒，${stateLabel}`}
        >
          <svg viewBox="0 0 100 100">
            <circle className="poll-track" cx="50" cy="50" r="43" />
            <circle
              key={
                s.next_target_at.state === "AVAILABLE"
                  ? s.next_target_at.value
                  : s.countdown_state
              }
              className="poll-progress"
              cx="50"
              cy="50"
              r="43"
              pathLength="100"
              strokeDasharray="100"
              strokeDashoffset={100 - progress * 100}
              transform="rotate(-90 50 50)"
            />
          </svg>
          <div>
            <strong>{stateLabel}</strong>
            <span>{s.target_interval_seconds}s / 次</span>
          </div>
        </div>
        <dl>
          <dt>最近成功</dt>
          <dd>
            {valueText(s.last_success_at, (v) =>
              formatSourceSuccess(v, semanticDay),
            )}
          </dd>
          <dt>当天新增</dt>
          <dd>{today ? valueText(today) : "未记录"}</dd>
          <dt>平均端到端耗时</dt>
          <dd title={`文章取样 · ${s.recent?.e2e_samples ?? 0}条新消息`}>
            {seconds(s.recent?.mean_e2e_seconds)}
          </dd>
          <dt>真实抓取延时</dt>
          <dd>{seconds(s.recent?.mean_capture_seconds)}</dd>
          <dt>P95端到端耗时</dt>
          <dd>{seconds(s.recent?.p95_e2e_seconds)}</dd>
          <dt>实际刷新间隔</dt>
          <dd>{seconds(s.recent?.refresh_seconds)}</dd>
          <dt>正文补全成功率</dt>
          <dd
            title={`文章取样 · 完整 ${s.recent?.body_full ?? 0} / ${s.recent?.body_total ?? 0}`}
          >
            {ratio(s.recent?.body_ratio)}
          </dd>
        </dl>
      </div>

      {s.current_error && (
        <p className="tone-danger">{s.current_error.message}</p>
      )}
    </article>
  );
}

function MessageCard({
  ticker,
  message: m,
}: {
  ticker: string;
  message: MessageSummary;
}) {
  const [open, setOpen] = useState(false);
  const [visible, setVisible] = useState(false);
  const card = useRef<HTMLElement>(null);
  useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          setVisible(true);
          observer.disconnect();
        }
      },
      {
        root: card.current?.closest(".message-scroll") ?? null,
        rootMargin: "0px",
      },
    );
    if (card.current) observer.observe(card.current);
    return () => observer.disconnect();
  }, []);
  const route =
    m.case.resolved_route ??
    m.case.initial_route ??
    (
      {
        CREATED: "待处理",
        RUNNING: "处理中",
        FAILED: "失败",
        SUCCEEDED: "已完成",
        COMPLETED: "已完成",
        RETRY_PENDING: "等待重试",
        PENDING_RETRY: "等待重试",
        CANCELLED: "已取消",
      } as Record<string, string>
    )[m.case.status ?? ""] ??
    m.case.status ??
    "尚未路由";
  return (
    <article
      className="message-card"
      ref={card}
      data-message-id={m.standard_message_id}
      onClick={(event) => {
        if (
          (event.target as HTMLElement).closest(
            "a, button, input, select, textarea, summary, [role='button']",
          ) ||
          window.getSelection()?.toString()
        )
          return;
        setOpen((value) => !value);
      }}
    >
      <button
        className="message-heading"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        <div>
          <h2>{valueText(m.title, (v) => v || "无标题消息")}</h2>
          <div className="message-meta">
            <span>{m.source_name ?? m.source.name}</span>
            <span>{m.source.kind === "api" ? "API" : "爬虫"}</span>
            <span>
              发布{m.publication_time_basis === "RELATIVE" ? "（估算）" : ""}{" "}
              {formatInstant(m.source_published_at)}
            </span>
            <span>抓取 {formatInstant(m.collected_at)}</span>
            {m.content_update && m.content_update.revision > 1 && (
              <span>已更新 · 正文修订 {m.content_update.revision}</span>
            )}
            {m.content_update?.pending && <span>待核查更新</span>}
            {m.content_update?.last_error && (
              <span title={m.content_update.last_error}>最近正文检查失败</span>
            )}
          </div>
        </div>
        <span className={"domain-tag route-tag route-" + route}>{route}</span>
        <ChevronDown className="message-chevron" aria-hidden="true" />
      </button>
      <div className={"message-body" + (open ? " expanded" : " collapsed")}>
        {visible || open ? (
          <ContentReader
            ticker={ticker}
            content={m.body}
            loadFully={open}
            presentation="message"
            collapsed={!open}
          />
        ) : (
          <div className="body-placeholder" />
        )}
      </div>
      {open && /^https?:\/\//i.test(m.url) && (
        <a
          className="message-source-link"
          href={m.url}
          target="_blank"
          rel="noreferrer"
        >
          原始信源 ↗
        </a>
      )}
    </article>
  );
}
