import { LoadMore } from "@/components/load-more";
import {
  Library,
  Plus,
  PencilLine,
  CircleOff,
  ChevronDown,
  Layers3,
} from "lucide-react";
import { EventRail } from "@/components/event-rail";
import { useState, useEffect, type ReactNode } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import type {
  Period,
  EventSummary,
  EventDetail,
  FactRow,
  DeltaDay,
  ReferenceDeltaItem,
} from "@contract";
import { usePageContext, useRead, tickerPath, id } from "@/core/page-query";
import { usePages } from "@/core/paged-query";
import { queryString } from "@/core/api";
import { metricText } from "@/core/format";
import { PageTitle, Choices } from "@/components/page-kit";
import { PeriodPicker, periods } from "@/components/business-metrics";
import { Comparison, MetricNote } from "./overview/metrics";
import { Module, Notice } from "@/components/state";
import { Button } from "@/components/ui/button";
const filters = [
  ["ACTIVE", "当前生效"],
  ["ADDED", "周期新增"],
  ["MODIFIED", "周期修改"],
  ["RETIRED", "周期失效"],
] as const;
export default function Events() {
  const { ticker = "" } = useParams();
  const [p, setP] = useSearchParams();
  const period = (periods.find((x) => x[0] === p.get("period"))?.[0] ??
    "PREVIOUS_TRADING_DAY") as Period;
  const mode = p.get("mode") === "DELTA" ? "DELTA" : "FULL";
  const context = usePageContext(ticker, "EVENTS", period),
    view = context.data?.data.view_id;
  const metrics = useRead(
    "EventMetrics",
    view
      ? tickerPath(ticker) +
          "/event-library/metrics" +
          queryString({ view_id: view })
      : null,
    view,
  );
  const set = (key: string, v: string) => {
    const n = new URLSearchParams(p);
    n.set(key, v);
    setP(n);
  };
  const controls = (
    <Choices
      value={mode}
      onChange={(v) => set("mode", v)}
      items={[
        ["FULL", "完整事件"],
        ["DELTA", "新增事件"],
      ]}
      label="事件模式"
    />
  );
  return (
    <>
      <PageTitle title={`${ticker} 事件库`} refresh={context.refetch}>
        <PeriodPicker
          value={period}
          options={context.data?.data.period_options}
          onChange={(v) => set("period", v)}
        />
      </PageTitle>
      {context.error && <Notice danger>{context.error.message}</Notice>}
      <Module query={metrics} label="事件指标">
        {(m) => (
          <div className="business-metrics paired-metrics">
            {(["active", "added", "modified", "retired"] as const).map(
              (k, i) => {
                const event = m[`${k}_events`],
                  fact = m[`${k}_facts`];
                return (
                  <article key={k}>
                    <span>
                      {(() => {
                        const Icon = [Library, Plus, PencilLine, CircleOff][i];
                        return <Icon aria-hidden="true" />;
                      })()}
                      {["生效数量", "周期新增", "周期修改", "周期失效"][i]}
                    </span>
                    <div className="event-kpi-pair">
                      {[
                        ["Event", event],
                        ["Fact", fact],
                      ].map(([label, value]) => {
                        const metric = value as typeof event;
                        return (
                          <div key={String(label)}>
                            <span>{String(label)}</span>
                            <strong
                              data-unavailable={
                                metric.current.state !== "AVAILABLE" ||
                                undefined
                              }
                            >
                              {metricText(metric)}
                            </strong>
                            <MetricNote metric={metric} />
                            {i > 0 && period !== "ALL" && (
                              <Comparison metric={metric} />
                            )}
                          </div>
                        );
                      })}
                    </div>
                  </article>
                );
              },
            )}
          </div>
        )}
      </Module>
      {view &&
        (mode === "FULL" ? (
          <FullEvents ticker={ticker} view={view} controls={controls} />
        ) : (
          <DeltaEvents
            controls={controls}
            ticker={ticker}
            defaultDay={
              context.data?.data.clock.previous_trading_day.state ===
              "AVAILABLE"
                ? context.data.data.clock.previous_trading_day.value
                : ""
            }
          />
        ))}
    </>
  );
}
function FullEvents({
  ticker,
  view,
  controls,
}: {
  ticker: string;
  view: string;
  controls: ReactNode;
}) {
  const [p, setP] = useSearchParams();
  const [opened, setOpened] = useState<Set<string>>(new Set());
  const filter = filters.find((x) => x[0] === p.get("filter"))?.[0] ?? "ACTIVE";
  const q = usePages<EventSummary, "Events">(
    "Events",
    tickerPath(ticker) +
      "/event-library/events" +
      queryString({ view_id: view, filter, limit: "20" }),
    (r) => r.data,
    view,
  );
  const list = q.data?.data.data?.items ?? [];
  return (
    <>
      <div className="filter-bar event-filter-bar">
        {controls}
        <div className="event-secondary-filter">
          <Choices
            value={filter}
            onChange={(v) => {
              const n = new URLSearchParams(p);
              n.set("filter", v);
              setP(n);
            }}
            items={filters}
            label="事件状态"
          />
        </div>
      </div>
      <Module query={q} label="事件列表">
        {() =>
          list.length ? (
            <div className="event-list">
              {list.map((s) => (
                <EventCard
                  key={s.event_key + ":" + s.event_revision_id}
                  ticker={ticker}
                  summary={s}
                  open={opened.has(s.event_key)}
                  onOpen={() =>
                    setOpened((previous) => {
                      const next = new Set(previous);
                      if (next.has(s.event_key)) next.delete(s.event_key);
                      else next.add(s.event_key);
                      return next;
                    })
                  }
                />
              ))}
            </div>
          ) : (
            <Notice>当前筛选没有匹配事件</Notice>
          )
        }
      </Module>
      {q.hasNextPage && (
        <LoadMore
          variant="outline"
          disabled={q.isFetchingNextPage}
          onClick={() => void q.fetchNextPage()}
        >
          更多事件
        </LoadMore>
      )}
      <EventRail
        items={list.map((s) => ({
          key: s.event_key,
          target: "event-" + s.event_key,
          id: s.event_id,
          title: s.title,
        }))}
        onSelect={(key) => setOpened(new Set([...opened, key]))}
      />
    </>
  );
}
export function EventCard({
  ticker,
  summary,
  open,
  onOpen,
}: {
  ticker: string;
  summary: EventSummary;
  open: boolean;
  onOpen: () => void;
}) {
  const path =
    tickerPath(ticker) +
    `/event-library/snapshots/${id(summary.library_snapshot_id)}/events/${id(summary.event_id)}?limit=20`;
  const details = useRead("Event", path);
  const important = details.data?.data.data?.event.is_important;
  return (
    <article className="event-card" id={"event-" + summary.event_key}>
      <button
        className="event-card-heading"
        aria-expanded={open}
        onClick={() => {
          onOpen();
        }}
      >
        <span className="event-id">{summary.event_id}</span>
        <h2>
          {summary.title}
          {important && <span className="event-important">重要</span>}
        </h2>
        <span>
          {summary.occurrence_time_precision === "UNKNOWN"
            ? "时间未记录"
            : summary.occurred_at}
        </span>
        <span className="fact-count">
          <Layers3 aria-hidden="true" />
          {summary.active_fact_count} Facts
        </span>
        <ChevronDown className="event-chevron" aria-hidden="true" />
      </button>
      <div hidden={!open}>
        <Module query={details} label="事件正文">
          {(data) => (
            <EventContent
              ticker={ticker}
              data={data}
              path={path}
              delta={false}
              loadFully
            />
          )}
        </Module>
      </div>
    </article>
  );
}
function EventBody({
  ticker,
  path,
  delta = false,
}: {
  ticker: string;
  path: string;
  delta?: boolean;
}) {
  const q = useRead("Event", path);
  return (
    <Module query={q} label="事件正文">
      {(d) => (
        <EventContent ticker={ticker} data={d} path={path} delta={delta} />
      )}
    </Module>
  );
}
function EventContent({
  ticker,
  data,
  path,
  delta,
  loadFully = false,
}: {
  ticker: string;
  data: EventDetail;
  path: string;
  delta: boolean;
  loadFully?: boolean;
}) {
  const [more, setMore] = useState(false);
  const q = usePages<FactRow, "Facts">(
    "Facts",
    (loadFully || more) && !delta
      ? tickerPath(ticker) +
          `/event-library/snapshots/${id(data.library.library_snapshot_id)}/events/${id(data.event.event_id)}/facts?limit=20`
      : null,
    (r) => r.data,
    undefined,
    data.facts,
  );
  const dq = usePages<FactRow, "Event">(
    "Event",
    more && delta ? path : null,
    (r) => r.data?.facts ?? null,
    undefined,
    data.facts,
  );
  const pages = delta ? dq : q;
  const { hasNextPage, isFetching, error, fetchNextPage } = pages;
  useEffect(() => {
    if (loadFully && hasNextPage && !isFetching && !error) void fetchNextPage();
  }, [loadFully, hasNextPage, isFetching, error, fetchNextPage]);
  const facts = pages.data?.data.data?.items ?? data.facts.items,
    e = data.event;
  return (
    <div className="event-content">
      <p className="event-summary">{e.canonical_summary}</p>
      <div className="fact-list">
        {facts.map(({ fact: f, ...row }) => (
          <article key={row.fact_key}>
            <header>
              <strong>{f.fact_id}</strong>
              <span className="domain-tag">{f.assertion_state}</span>
              {(!row.member_of_event || row.lifecycle === "RETIRED") && (
                <span className="tone-warning">已移出 / 失效</span>
              )}
            </header>
            <p>{f.proposition}</p>
            <div className="fact-times">
              {f.subject_time && <span>所述时间 {f.subject_time}</span>}
              {f.fact_occurred_at && <span>发生时间 {f.fact_occurred_at}</span>}
            </div>
          </article>
        ))}
      </div>
      {!loadFully && (pages.hasNextPage || (!more && data.facts.has_more)) && (
        <LoadMore
          variant="outline"
          onClick={() => (more ? void pages.fetchNextPage() : setMore(true))}
        >
          更多事实
        </LoadMore>
      )}
      {pages.error && (
        <Notice danger>
          {pages.error.message}
          <Button variant="link" onClick={() => void pages.fetchNextPage()}>
            重试加载事实
          </Button>
        </Notice>
      )}
      {(e.related_event_ids.length > 0 ||
        e.derived_from_event_ids.length > 0 ||
        e.supersedes_event_id) && (
        <div className="event-relations">
          {e.related_event_ids.length > 0 && (
            <span>关联 {e.related_event_ids.join("、")}</span>
          )}
          {e.derived_from_event_ids.length > 0 && (
            <span>衍生自 {e.derived_from_event_ids.join("、")}</span>
          )}
          {e.supersedes_event_id && <span>替代 {e.supersedes_event_id}</span>}
        </div>
      )}
    </div>
  );
}
function DeltaEvents({
  ticker,
  defaultDay,
  controls,
}: {
  controls: ReactNode;
  ticker: string;
  defaultDay: string;
}) {
  const [p, setP] = useSearchParams();
  const ctx = usePageContext(ticker, "EVENTS", "ALL"),
    view = ctx.data?.data.view_id;
  const days = usePages<DeltaDay, "DeltaDays">(
    "DeltaDays",
    view
      ? tickerPath(ticker) +
          "/reference-deltas/days" +
          queryString({ view_id: view, limit: "20" })
      : null,
    (r) => r.data,
    view,
  );
  const day = p.get("day") ?? defaultDay;
  const selected = days.data?.data.data?.items.find(
    (x) => x.semantic_day === day,
  );
  return (
    <>
      <div className="filter-bar event-filter-bar">
        {controls}
        <label className="date-field event-secondary-filter">
          语义交易日{" "}
          <input
            type="date"
            value={day}
            onChange={(e) => {
              const n = new URLSearchParams(p);
              n.set("day", e.target.value);
              setP(n);
            }}
          />
        </label>
        <div className="day-options">
          {days.data?.data.data?.items.map((d) => (
            <Button
              key={d.semantic_day}
              variant={day === d.semantic_day ? "secondary" : "outline"}
              onClick={() => {
                const n = new URLSearchParams(p);
                n.set("day", d.semantic_day);
                setP(n);
              }}
            >
              {d.semantic_day}
            </Button>
          ))}
          {days.hasNextPage && (
            <LoadMore
              variant="outline"
              onClick={() => void days.fetchNextPage()}
            >
              更早日期
            </LoadMore>
          )}
        </div>
      </div>
      {days.error ? (
        <Notice danger>{days.error.message}</Notice>
      ) : !days.data?.data.data ? (
        <Module query={days} label="可用 Delta 日期">
          {() => null}
        </Module>
      ) : !selected && !days.hasNextPage ? (
        <Notice>该日没有已发布的 Reference View Delta</Notice>
      ) : selected && selected.state !== "AVAILABLE" ? (
        <Notice>
          {
            {
              NO_CHANGE: "该日没有 Reference View 变化",
              PENDING: "该日 Delta 尚未完成",
              FAILED: "该日 Delta 生成失败",
              UNAVAILABLE: "该日数据未记录",
              AVAILABLE: "",
            }[selected.state]
          }
        </Notice>
      ) : view && day ? (
        <DeltaList ticker={ticker} view={view} day={day} />
      ) : (
        <Notice>没有可查看的日期</Notice>
      )}
    </>
  );
}
function DeltaList({
  ticker,
  view,
  day,
}: {
  ticker: string;
  view: string;
  day: string;
}) {
  const q = usePages<ReferenceDeltaItem, "Delta">(
    "Delta",
    tickerPath(ticker) +
      `/reference-deltas/days/${id(day)}` +
      queryString({ view_id: view, limit: "20" }),
    (r) => r.data?.changes ?? null,
    view,
  );
  const delta = q.source?.data;
  const [opened, setOpened] = useState<Set<string>>(new Set());
  return (
    <Module query={q} label="Reference View Delta">
      {(d) => (
        <>
          <EventRail
            items={d.items.map((c) => ({
              key: c.change_id,
              target: "delta-" + c.change_id,
              id: c.event_id,
              title: c.title,
            }))}
            onSelect={(key) => setOpened(new Set([...opened, key]))}
          />
          {!d.items.length && <Notice>该日没有 Reference View 变化</Notice>}
          {d.items.map((c) => (
            <article
              className="event-card"
              key={c.change_id}
              id={"delta-" + c.change_id}
            >
              <button
                className="event-card-heading"
                onClick={() => {
                  const next = new Set(opened);
                  if (next.has(c.change_id)) next.delete(c.change_id);
                  else next.add(c.change_id);
                  setOpened(next);
                }}
                aria-expanded={opened.has(c.change_id)}
              >
                <span className={"domain-tag delta-" + c.type}>
                  {{ add: "新增", modify: "修改", remove: "移除" }[c.type]}
                </span>
                <h2>
                  {c.event_id} · {c.title}
                </h2>
                <span>版本 {c.detail_library_version}</span>
              </button>

              {opened.has(c.change_id) && delta && (
                <EventBody
                  ticker={ticker}
                  delta
                  path={
                    tickerPath(ticker) +
                    `/reference-deltas/${id(delta.delta_id)}/changes/${id(c.change_id)}/event` +
                    queryString({
                      side: c.type === "remove" ? "before" : "after",
                      limit: "20",
                    })
                  }
                />
              )}
            </article>
          ))}
          {q.hasNextPage && (
            <LoadMore variant="outline" onClick={() => void q.fetchNextPage()}>
              更多变化
            </LoadMore>
          )}
        </>
      )}
    </Module>
  );
}
