import { LoadMore } from "@/components/load-more";
import {
  Crosshair,
  GitPullRequestArrow,
  Link2,
  History as HistoryIcon,
} from "lucide-react";
import { useState, useEffect } from "react";
import { useParams, useSearchParams, Link } from "react-router-dom";
import type {
  Period,
  PolicyContext,
  PolicySummary,
  PolicyFilter,
  ChangeEvent,
  PolicyShellSummary,
} from "@contract";
import { usePageContext, useRead, tickerPath, id } from "@/core/page-query";
import { usePages } from "@/core/paged-query";
import { queryString } from "@/core/api";
import { formatInstant } from "@/core/format";
import { PageTitle, DownloadButton, DetailPanel } from "@/components/page-kit";
import {
  PeriodPicker,
  MetricStrip,
  periods,
} from "@/components/business-metrics";
import { Module, Notice } from "@/components/state";
import { EventRail } from "@/components/event-rail";
import { Button } from "@/components/ui/button";
const filters = [
  ["ACTIVE", "当前生效"],
  ["ADDED", "周期新增"],
  ["HIT", "周期命中"],
  ["MODIFIED", "周期修改"],
  ["RETIRED", "周期失效"],
  ["EXECUTED", "周期交易执行"],
] as const;
export default function Strategy() {
  const { ticker = "" } = useParams();
  const [p, setP] = useSearchParams();
  const period = (periods.find((x) => x[0] === p.get("period"))?.[0] ??
    "PREVIOUS_TRADING_DAY") as Period;
  const [timeline, setTimeline] = useState(false);
  const context = usePageContext(ticker, "POLICIES", period);
  const view = context.data?.data.view_id;
  const data = useRead(
    "PolicyContext",
    view
      ? tickerPath(ticker) +
          "/policies/context" +
          queryString({ view_id: view, limit: "20" })
      : null,
    view,
  );
  return (
    <>
      <PageTitle title={`${ticker} 交易策略`} refresh={context.refetch}>
        <PeriodPicker
          value={period}
          options={context.data?.data.period_options}
          onChange={(v) => {
            const n = new URLSearchParams(p);
            n.set("period", v);
            setP(n);
          }}
        />
        <Button variant="outline" onClick={() => setTimeline(true)}>
          <HistoryIcon />
          变更时间线
        </Button>
        {data.data?.data.data && (
          <DownloadButton
            path={
              tickerPath(ticker) +
              `/policy-sets/${data.data.data.data.policy_set.policy_set_version}/download` +
              queryString({
                runtime_activation_id:
                  data.data.data.data.runtime_activation_id,
              })
            }
          />
        )}
      </PageTitle>
      {timeline && (
        <ChangeTimeline ticker={ticker} close={() => setTimeline(false)} />
      )}
      {context.error && <Notice danger>{context.error.message}</Notice>}
      <Module query={data} label="策略目录">
        {(d) => (
          <Policies ticker={ticker} data={d} view={view!} period={period} />
        )}
      </Module>
    </>
  );
}
function Policies({
  ticker,
  data,
  view,
  period,
}: {
  ticker: string;
  data: PolicyContext;
  view: string;
  period: Period;
}) {
  const [p, setP] = useSearchParams();
  const [allShells, setAllShells] = useState(false);
  const shells = usePages<PolicyShellSummary, "PolicyShells">(
    "PolicyShells",
    allShells
      ? tickerPath(ticker) +
          "/policies/shells" +
          queryString({ view_id: view, limit: "20" })
      : null,
    (r) => r.data,
    view,
    data.shells,
  );
  const items = shells.data?.data.data?.items ?? data.shells.items;
  const shell =
    p.get("shell") === "ALL"
      ? "ALL"
      : (items.find((s) => s.shell_id === p.get("shell"))?.shell_id ??
        items[0]?.shell_id ??
        "ALL");
  const filter = (filters.find((x) => x[0] === p.get("filter"))?.[0] ??
    "ACTIVE") as PolicyFilter;
  const qs = queryString({ view_id: view, shell_id: shell });
  const metrics = useRead(
    "PolicyMetrics",
    tickerPath(ticker) + "/policies/metrics" + qs,
    view,
  );
  const policies = usePages<PolicySummary, "Policies">(
    "Policies",
    tickerPath(ticker) +
      "/policies" +
      queryString({ view_id: view, shell_id: shell, filter, limit: "20" }),
    (r) => r.data,
    view,
  );
  const list = policies.data?.data.data?.items ?? [];
  const set = (key: string, value: string) => {
    const n = new URLSearchParams(p);
    n.set(key, value);
    if (key !== "policy") n.delete("policy");
    setP(n);
  };
  return (
    <>
      <Module query={metrics} label="策略指标">
        {(m) => (
          <MetricStrip
            period={period}
            items={[
              { label: "生效数量", metric: m.active, current: true },
              {
                label: "LONG / SHORT",
                metric: m.long_ratio,
                secondary: m.short_ratio,
                current: true,
              },
              ...(
                ["added", "hit", "modified", "retired", "executed"] as const
              ).map((k, i) => ({ label: filters[i + 1][1], metric: m[k] })),
            ]}
          />
        )}
      </Module>
      <div className="strategy-filter-bar">
        <label className="policy-filter">
          策略状态
          <select
            aria-label="策略状态"
            value={filter}
            onChange={(e) => set("filter", e.target.value)}
          >
            {filters.map(([key, label]) => (
              <option key={key} value={key}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <div className="shell-scroll">
          <div className="shell-tabs">
            {items.map((s) => (
              <button
                key={s.shell_id}
                title={s.core_question}
                aria-pressed={shell === s.shell_id}
                onClick={() => set("shell", s.shell_id)}
              >
                {s.core_question}
              </button>
            ))}
            {(shells.hasNextPage || (!allShells && data.shells.has_more)) && (
              <LoadMore
                variant="outline"
                onClick={() =>
                  allShells ? void shells.fetchNextPage() : setAllShells(true)
                }
              >
                更多 Shell
              </LoadMore>
            )}
            <button
              aria-pressed={shell === "ALL"}
              onClick={() => set("shell", "ALL")}
            >
              全部 Shell
            </button>
          </div>
        </div>
      </div>
      {data.source_context_consistent.state === "AVAILABLE" &&
        !data.source_context_consistent.value && (
          <Notice>
            策略引用的 D2 与现行 D2 不同，无法关联的策略保留在全部 Shell。
          </Notice>
        )}
      <Module query={policies} label="策略列表">
        {() =>
          list.length ? (
            <div className="policy-reading-layout">
              <div className="policy-document-list">
                {list.map((selected) => (
                  <PolicyBody
                    key={selected.policy_revision_id}
                    ticker={ticker}
                    selected={selected}
                    focus={p.get("policy") === selected.policy_id}
                    view={view}
                  />
                ))}
              </div>
              <EventRail
                label="策略索引"
                items={list.map((s) => ({
                  key: s.policy_id,
                  target: "policy-" + s.policy_id,
                  id: s.decision,
                  title: s.title,
                }))}
                onSelect={(key) => set("policy", key)}
              />
              {policies.hasNextPage && (
                <LoadMore
                  variant="outline"
                  disabled={policies.isFetchingNextPage}
                  onClick={() => void policies.fetchNextPage()}
                >
                  更多策略
                </LoadMore>
              )}
            </div>
          ) : (
            <Notice>当前筛选没有匹配的 Policy</Notice>
          )
        }
      </Module>
    </>
  );
}
function PolicyBody({
  ticker,
  selected,
  view,
  focus,
}: {
  ticker: string;
  selected: PolicySummary;
  focus: boolean;
  view: string;
}) {
  const detail = useRead(
    "Policy",
    tickerPath(ticker) +
      `/policies/${id(selected.policy_id)}/revisions/${id(selected.policy_revision_id)}` +
      queryString({
        view_id: view,
        policy_set_version: String(selected.policy_set_version),
      }),
    view,
  );
  useEffect(() => {
    if (focus && detail.data?.data.data)
      document
        .getElementById("policy-" + selected.policy_id)
        ?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [focus, detail.data, selected.policy_id]);
  return (
    <div id={"policy-" + selected.policy_id} className="policy-anchor">
      <Module query={detail} label="策略正文">
        {(d) => (
          <article className="policy-document">
            <header>
              <span
                className={`decision decision-${d.policy.decision.toLowerCase()}`}
              >
                {d.policy.decision}
              </span>
              <h2>{d.policy.title}</h2>
            </header>
            <section>
              <h3>
                <Crosshair aria-hidden="true" />
                匹配范围
              </h3>
              <p>{d.policy.match_scope}</p>
            </section>
            <section>
              <h3>
                <GitPullRequestArrow aria-hidden="true" />
                激活条件 <span className="domain-tag">任一满足 · OR</span>
              </h3>
              {d.policy.activation_conditions.map((c, i) => (
                <article className="condition" key={c.condition_id}>
                  <span className="condition-number">{i + 1}</span>
                  <div>
                    <h4>{c.criterion}</h4>
                    <dl>
                      <dt>参考状态</dt>
                      <dd>{c.calibration.reference_state}</dd>
                      <dt>触发边界</dt>
                      <dd>{c.calibration.trigger_boundary}</dd>
                    </dl>
                  </div>
                </article>
              ))}
            </section>
            <section>
              <h3>
                <Link2 aria-hidden="true" />
                关联预期
              </h3>
              <div className="source-links">
                {d.policy.source_refs.map((s, i) => (
                  <Link
                    key={i}
                    to={`/ticker/${id(ticker)}/expectations?run=${id(d.source_document2.run_id)}&shell=${id(s.shell_id)}&unit=${id(s.expectation_id)}&content=GAPS&item=${id(s.gap_id)}`}
                  >
                    {s.shell_id} / {s.expectation_id} / {s.gap_id}
                  </Link>
                ))}
              </div>
            </section>
          </article>
        )}
      </Module>
    </div>
  );
}
function ChangeTimeline({
  ticker,
  close,
}: {
  ticker: string;
  close: () => void;
}) {
  const context = usePageContext(ticker, "POLICIES", "ALL");
  const view = context.data?.data.view_id;
  const q = usePages<ChangeEvent, "Changes">(
    "Changes",
    view
      ? tickerPath(ticker) +
          "/policies/changes" +
          queryString({
            view_id: view,
            shell_id: "ALL",
            limit: "20",
          })
      : null,
    (r) => r.data,
    view,
  );
  return (
    <DetailPanel label="变更时间线" className="timeline-panel" close={close}>
      <header>
        <h2>变更时间线</h2>
        <Button variant="outline" onClick={close}>
          关闭
        </Button>
      </header>
      <Module query={q} label="变更时间线">
        {(d) =>
          !d.items.filter((e) => e.type !== "RESTORE").length ? (
            <Notice>尚无策略定义变更记录</Notice>
          ) : (
            d.items
              .filter((e) => e.type !== "RESTORE")
              .map((e) => (
                <article key={e.change_id}>
                  <time>{formatInstant(e.occurred_at)}</time>
                  <span className="domain-tag">
                    {
                      {
                        ADD: "新增",
                        MODIFY: "修改",
                        RETIRE: "失效",
                        RESTORE: "恢复",
                      }[e.type]
                    }
                  </span>
                  <h3>{e.title}</h3>
                  <p>{e.summary}</p>
                  <small>
                    版本 {e.from_version ?? "—"} → {e.to_version}
                  </small>
                </article>
              ))
          )
        }
      </Module>
      {q.hasNextPage && (
        <LoadMore onClick={() => void q.fetchNextPage()}>更多变更</LoadMore>
      )}
    </DetailPanel>
  );
}
