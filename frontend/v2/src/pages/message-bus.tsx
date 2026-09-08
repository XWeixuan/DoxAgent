import { LoadMore } from "@/components/load-more";
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
import { useQuery, type InfiniteData } from "@tanstack/react-query";
import { useParams, useSearchParams } from "react-router-dom";
import type {
  Period,
  MessageSummary,
  MessageDelta,
  SourceStatus,
  Response,
  Resource,
  MessageBaseline,
} from "@contract";
import { useRuntime } from "@/core/runtime";
import { usePageContext, tickerPath } from "@/core/page-query";
import { usePages } from "@/core/paged-query";
import { useLive, useMinuteReads } from "@/core/live-query";
import { compareMessageOrder } from "@/core/updates";
import { queryString } from "@/core/api";
import {
  duration,
  formatInstant,
  formatSourceSuccess,
  valueText,
  metricText,
} from "@/core/format";
import { PageTitle, Choices, ContentReader } from "@/components/page-kit";
import { PeriodPicker, periods } from "@/components/business-metrics";
import { Comparison, MetricNote } from "./overview/metrics";
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
export default function MessageBus() {
  const { ticker = "" } = useParams();
  const [p, setP] = useSearchParams();
  const config = p.get("mode") === "CONFIG";
  const period = (periods.find((x) => x[0] === p.get("period"))?.[0] ??
    "PREVIOUS_TRADING_DAY") as Period;
  const ctx = usePageContext(ticker, "MESSAGE_BUS", period);
  const feed = usePageContext(ticker, "MESSAGE_BUS", "ALL");
  return (
    <>
      <PageTitle title={`${ticker} 消息总线`} refresh={ctx.refetch}>
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
      {ctx.data &&
        (config ? (
          <BindingSettings
            refresh={ctx.refetch}
            ticker={ticker}
            view={ctx.data.data.view_id}
          />
        ) : feed.data ? (
          <MessageStream
            ticker={ticker}
            period={period}
            context={feed.data}
            reset={() => void feed.refetch()}
          />
        ) : null)}
    </>
  );
}
function MessageStream({
  ticker,
  period,
  context,
  reset,
}: {
  ticker: string;
  period: Period;
  context: Response<import("@contract").ReadContext>;
  reset: () => void;
}) {
  const { api, query, scope } = useRuntime();
  const [p, setP] = useSearchParams();
  const [search, setSearch] = useState(p.get("q") ?? "");
  const [searchOpen, setSearchOpen] = useState(!!p.get("q"));
  const view = context.data.view_id;
  const set = (key: string, v: string) => {
    const n = new URLSearchParams(p);
    if (v) n.set(key, v);
    else n.delete(key);
    if (key === "kind") n.delete("source");
    setP(n);
  };
  const filters = {
    source_kind: p.get("kind") ?? undefined,
    source_id: p.get("source") ?? undefined,
    route: p.get("route") ?? "ALL",
  };
  // The minute snapshot is separate from the message stream's immutable scope.
  const minuteKey = [
    scope,
    "bus-minute",
    ticker,
    period,
    filters.source_kind,
    filters.source_id,
    filters.route,
  ];
  const readMinute = async () => {
    const c = await api.request(
      "ReadContext",
      "/read-context" +
        queryString({ ticker, page: "MESSAGE_BUS", period, refresh: "MINUTE" }),
    );
    const v = c.data.view_id;
    const [status, metrics, sources] = await Promise.all([
      api.request(
        "BusStatus",
        tickerPath(ticker) +
          "/message-bus/status" +
          queryString({ view_id: v }),
        { view: v },
      ),
      api.request(
        "BusMetrics",
        tickerPath(ticker) +
          "/message-bus/metrics" +
          queryString({ view_id: v, ...filters }),
        { view: v },
      ),
      api.request(
        "Sources",
        tickerPath(ticker) +
          "/message-bus/sources" +
          queryString({ view_id: v, limit: "100" }),
        { view: v },
      ),
    ]);
    return { status, metrics, sources };
  };
  const minute = useQuery({ queryKey: minuteKey, queryFn: readMinute });
  const minuteError = useMinuteReads(context, async () => {
    await minute.refetch();
  });
  const path =
    tickerPath(ticker) +
    "/messages" +
    queryString({
      view_id: view,
      ...filters,
      q: p.get("q") ?? undefined,
      limit: "20",
    });
  const messages = usePages<MessageSummary, "Messages">(
    "Messages",
    path,
    (r) => r.data?.messages ?? null,
    view,
  );
  const baseline = messages.source?.data?.stream_cursor;
  const streamError = useLive<MessageDelta>(
    tickerPath(ticker) +
      "/messages/events" +
      queryString({ view_id: view, ...filters, q: p.get("q") ?? undefined }),
    view,
    baseline,
    async (delta, streamCursor) => {
      if (delta.action === "UPSERT" && !delta.row) {
        const response = await api.request(
          "Message",
          tickerPath(ticker) +
            `/messages/${encodeURIComponent(delta.standard_message_id)}/revisions/${delta.revision}` +
            queryString({ view_id: view, stream_cursor: streamCursor }),
          { view },
        );
        delta = { ...delta, row: response.data.data };
      }
      if (
        delta.action === "UPSERT" &&
        (!delta.row ||
          delta.row.standard_message_id !== delta.standard_message_id ||
          delta.row.revision !== delta.revision ||
          delta.row.row_revision !== delta.row_revision)
      )
        throw new Error("Message revision mismatch");
      query.setQueryData<InfiniteData<Response<Resource<MessageBaseline>>>>(
        [scope, "pages", "Messages", path, view],
        (old) => {
          if (!old) return old;
          let found = false;
          const pages = old.pages.map((page) => {
            if (!page.data.data) return page;
            const items = page.data.data.messages.items.flatMap((row) => {
              if (
                row.standard_message_id !== delta.standard_message_id ||
                row.revision !== delta.revision
              )
                return [row];
              found = true;
              if (delta.action === "REMOVE") return [];
              return [
                row.row_revision >= delta.row_revision ? row : delta.row!,
              ];
            });
            return {
              ...page,
              data: {
                ...page.data,
                data: {
                  ...page.data.data,
                  messages: { ...page.data.data.messages, items },
                },
              },
            };
          });
          if (
            !found &&
            delta.action === "UPSERT" &&
            delta.matches_scope &&
            delta.row
          ) {
            const first = pages[0];
            if (first.data.data) {
              const items = [delta.row, ...first.data.data.messages.items].sort(
                compareMessageOrder,
              );
              pages[0] = {
                ...first,
                data: {
                  ...first.data,
                  data: {
                    ...first.data.data,
                    messages: { ...first.data.data.messages, items },
                  },
                },
              };
            }
          }
          return { ...old, pages };
        },
      );
    },
    reset,
  );
  const status = minute.data?.status.data.data,
    metrics = minute.data?.metrics.data.data,
    sources = minute.data?.sources.data.data?.items ?? [];
  return (
    <>
      {minute.error && <Notice danger>{minute.error.message}</Notice>}
      <div className="business-metrics bus-metrics">
        <article>
          <span>
            <Timer aria-hidden="true" />
            连续运行时长
          </span>
          <strong>
            {status
              ? status.run_state === "RUNNING"
                ? valueText(status.continuous_run_seconds, duration)
                : "未运行"
              : "—"}
          </strong>
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
          {metrics && <MetricNote metric={metrics.published_revisions} />}
        </article>
        <article>
          <span>
            <CircleCheck aria-hidden="true" />
            正文补全成功率
          </span>
          <strong>
            {metrics
              ? valueText(
                  metrics.body_completion_success_ratio.current,
                  (v) => (Number(v) * 100).toFixed(1) + "%",
                )
              : "—"}
          </strong>
          {metrics && period !== "ALL" && (
            <Comparison metric={metrics.body_completion_success_ratio} />
          )}
          {metrics && (
            <MetricNote metric={metrics.body_completion_success_ratio} />
          )}
        </article>
        <article>
          <span>
            <Radio aria-hidden="true" />
            数据源状态
          </span>
          <strong>
            {status ? (
              <>
                <span className="tone-success">
                  {valueText(status.source_counts.normal)}
                </span>{" "}
                /{" "}
                <span className="tone-danger">
                  {valueText(status.source_counts.abnormal)}
                </span>
              </>
            ) : (
              "—"
            )}
          </strong>
        </article>
        <article>
          <span>
            <Clock3 aria-hidden="true" />
            平均轮询延迟
          </span>
          <strong>
            {status
              ? valueText(
                  status.average_poll_latency_seconds,
                  (v) => v.toFixed(2) + "s",
                )
              : "—"}
          </strong>
        </article>
      </div>
      {streamError && <Notice danger>{streamError}</Notice>}
      {minuteError && <Notice danger>{minuteError}</Notice>}
      <div className="bus-layout">
        <section className="stream-panel">
          {" "}
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
                      key={m.standard_message_id + ":" + m.revision}
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
              semanticDay={context.data.clock.semantic_day}
            />
          ))}
          {!sources.length && <p>没有已绑定消息源</p>}
        </aside>
      </div>
    </>
  );
}
export function SourceCard({
  status: s,
  semanticDay,
}: {
  status: SourceStatus;
  semanticDay?: string;
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
        <span className={"poll-state poll-" + s.poll_status}>
          {
            {
              succeeded: "正常",
              partial: "部分失败",
              failed: "失败",
              disabled: "禁用",
              never_polled: "未轮询",
            }[s.poll_status]
          }
        </span>
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
          <dt>最近新增</dt>
          <dd>{valueText(s.last_published_count)}</dd>
          <dt>轮询延迟</dt>
          <dd>
            {valueText(s.last_poll_latency_seconds, (v) => v.toFixed(2) + "s")}
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
            <span>{m.source.name}</span>
            <span>{m.source.kind === "api" ? "API" : "爬虫"}</span>
            <span>发布 {formatInstant(m.source_published_at)}</span>
            <span>抓取 {formatInstant(m.collected_at)}</span>
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
