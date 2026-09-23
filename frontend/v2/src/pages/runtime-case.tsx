import { LoadMore } from "@/components/load-more";
import { useState, type ReactNode } from "react";
import type {
  ContentRef,
  Resource,
  ModelAttempt,
  Page,
  MessageSummary,
  Candidate,
  ExecutionSummary,
  EventLink,
  PolicyLink,
  RuntimeRound,
  FactAttribution,
} from "@contract";
import { useRead, tickerPath, id } from "@/core/page-query";
import { usePages } from "@/core/paged-query";
import { queryString } from "@/core/api";
import { formatInstant, valueText, decimal } from "@/core/format";
import { Module, Notice } from "@/components/state";
import { ContentReader } from "@/components/page-kit";
import { Button } from "@/components/ui/button";
export default function CaseDetails({
  ticker,
  caseId,
  view,
}: {
  ticker: string;
  caseId: string;
  view: string;
}) {
  const q = useRead(
    "Case",
    tickerPath(ticker) +
      `/runtime/cases/${id(caseId)}` +
      queryString({ view_id: view }),
    view,
  );
  return (
    <Module query={q} label="Case 研判明细">
      {(d) => (
        <div className="case-details">
          <h2>{valueText(d.summary.title)}</h2>
          <p className="case-source">
            {d.summary.source.name} ·{" "}
            {d.summary.source.kind === "api" ? "API" : "爬虫"}
          </p>
          {d.failures.map((f) => (
            <Notice key={f.failure_id} danger>
              {f.stage} · {f.status} · {f.error.message}
            </Notice>
          ))}
          <section>
            <h3>W1 · 新旧判定</h3>
            {d.w1.data ? (
              <>
                <div className="verdict">
                  <strong>{valueText(d.w1.data.novelty)}</strong>
                  <span>置信度 {valueText(d.w1.data.confidence)}</span>
                  {d.w1.data.references.map((r) => (
                    <span key={r.event_id}>
                      事件 {r.event_id} ·{" "}
                      {r.fact_ids.length
                        ? "事实 " + r.fact_ids.join("、")
                        : "Fact 归因未提供"}
                    </span>
                  ))}
                </div>
                <Attempts
                  ticker={ticker}
                  caseId={caseId}
                  node="W1"
                  view={view}
                  rounds={d.w1.data.rounds}
                  first={d.w1.data.attempts}
                />
                {d.w1.data.unresolved_reference_ids?.length ? (
                  <Notice>
                    判定引用（快照详情未解析）：
                    {d.w1.data.unresolved_reference_ids.join("、")}
                  </Notice>
                ) : null}
                {d.w1.data.novelty.state === "AVAILABLE" &&
                  d.w1.data.novelty.value === "OLD" &&
                  !d.w1.data.references.length &&
                  !d.w1.data.unresolved_reference_ids?.length && (
                    <Notice>该旧信息判定未提供 Event / Fact 归因。</Notice>
                  )}
                <Reason ticker={ticker} content={d.w1.data.reasoning}>
                  <EventEvidence
                    ticker={ticker}
                    references={d.w1.data.references}
                    unresolved={d.w1.data.unresolved_reference_ids}
                    attributions={d.w1.data.fact_attributions}
                  />
                </Reason>
              </>
            ) : (
              <p>尚未形成 W1 判定</p>
            )}
          </section>
          <section>
            <h3>W2 · Policy 判定</h3>
            {d.w2.data ? (
              <>
                <div className="verdict">
                  <strong>
                    {d.w2.data.skipped
                      ? "已跳过"
                      : valueText(d.w2.data.policy_hit, (v) =>
                          v ? "命中" : "未命中",
                        )}
                  </strong>
                  <span>置信度 {valueText(d.w2.data.confidence)}</span>
                  {d.w2.data.policies.map((p) => (
                    <span key={p.policy_id}>
                      策略 {p.policy_id} ·{" "}
                      {p.condition_ids.length
                        ? "命中条件 " + p.condition_ids.join("、")
                        : "命中条件未提供"}
                    </span>
                  ))}
                </div>
                <Attempts
                  ticker={ticker}
                  caseId={caseId}
                  node="W2"
                  view={view}
                  rounds={d.w2.data.rounds}
                  first={d.w2.data.attempts}
                />
                {d.w2.data.unresolved_policy_ids?.length ? (
                  <Notice>
                    命中策略（快照详情未解析）：
                    {d.w2.data.unresolved_policy_ids.join("、")}
                  </Notice>
                ) : null}
                {d.w2.data.policy_hit.state === "AVAILABLE" &&
                  d.w2.data.policy_hit.value &&
                  !d.w2.data.policies.length &&
                  !d.w2.data.unresolved_policy_ids?.length && (
                    <Notice>该命中判定未提供策略归因。</Notice>
                  )}
                <Reason ticker={ticker} content={d.w2.data.reasoning}>
                  <PolicyEvidence
                    ticker={ticker}
                    activationId={
                      d.runtime_activation_id.state === "AVAILABLE"
                        ? d.runtime_activation_id.value
                        : undefined
                    }
                    label="R1 召回候选 Policy"
                    policies={d.w2.data.candidate_policies}
                    unresolved={d.w2.data.unresolved_candidate_policy_ids}
                  />
                  <PolicyEvidence
                    ticker={ticker}
                    activationId={
                      d.runtime_activation_id.state === "AVAILABLE"
                        ? d.runtime_activation_id.value
                        : undefined
                    }
                    label="最终命中 Policy"
                    filter="HIT"
                    policies={d.w2.data.policies}
                    unresolved={d.w2.data.unresolved_policy_ids}
                    recorded={d.w2.data.policy_hit.state === "AVAILABLE"}
                  />
                </Reason>
              </>
            ) : (
              <p>尚未形成 W2 判定</p>
            )}
          </section>
          {d.w3.data && (
            <section>
              <h3>W3 · 二轮研判</h3>
              <div className="verdict">
                <strong>{valueText(d.w3.data.novelty)}</strong>
                <span>{d.w3.data.status}</span>
                <span>
                  {valueText(d.w3.data.policy_hit, (v) =>
                    v ? "Policy 命中" : "Policy 未命中",
                  )}
                </span>
                {d.w3.data.policies.map((p) => (
                  <span key={p.policy_id}>{p.policy_id}</span>
                ))}
              </div>
              {d.w3.data.references.map((r) => (
                <span className="domain-tag" key={r.event_id}>
                  事件 {r.event_id} ·{" "}
                  {r.fact_ids.length
                    ? "事实 " + r.fact_ids.join("、")
                    : "Fact 归因未提供"}
                </span>
              ))}
              <dl>
                <dt>交易判定</dt>
                <dd>
                  {valueText(d.w3.data.expert_trade, (v) =>
                    v ? "交易" : "不交易",
                  )}{" "}
                  · {valueText(d.w3.data.direction)}
                </dd>
                <dt>原有预期</dt>
                <dd>{valueText(d.w3.data.prior_expectation)}</dd>
                <dt>预期变化</dt>
                <dd>{valueText(d.w3.data.expectation_delta)}</dd>
              </dl>
              <Attempts
                ticker={ticker}
                caseId={caseId}
                node="W3"
                view={view}
                first={d.w3.data.attempts}
              />
              <Reason
                ticker={ticker}
                content={d.w3.data.reasoning.novelty}
                label="新旧判断依据"
              />
              <Reason
                ticker={ticker}
                content={d.w3.data.reasoning.policy}
                label="Policy 判断依据"
              />
              <Reason
                ticker={ticker}
                content={d.w3.data.reasoning.expert_trade}
                label="交易判断依据"
              />
            </section>
          )}
          {d.candidate_count.state === "AVAILABLE" &&
            d.candidate_count.value > 0 && (
              <Candidates ticker={ticker} caseId={caseId} />
            )}
          {d.execution_count.state === "AVAILABLE" &&
            d.execution_count.value > 0 && (
              <Executions ticker={ticker} caseId={caseId} />
            )}
          <section>
            <h3>消息正文</h3>
            {d.messages.items.map((m) => (
              <CaseMessage
                ticker={ticker}
                message={m}
                key={m.standard_message_id + ":" + m.revision}
              />
            ))}
            {d.messages.has_more && (
              <MoreMessages
                ticker={ticker}
                caseId={caseId}
                view={view}
                first={d.messages}
              />
            )}
          </section>
        </div>
      )}
    </Module>
  );
}
function Reason({
  ticker,
  content,
  label = "判断依据",
  children,
}: {
  ticker: string;
  content: Resource<ContentRef>;
  label?: string;
  children?: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className="reason-section">
      <Button
        variant="ghost"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
      >
        {open ? "收起" : "展开"}
        {label}
      </Button>
      {open && (
        <div className="grid gap-3">
          <div>
            {children && <h4>判断说明</h4>}
            {content.data ? (
              <ContentReader ticker={ticker} content={content.data} loadFully />
            ) : (
              <Notice>判断依据未记录</Notice>
            )}
          </div>
          {children}
        </div>
      )}
    </div>
  );
}
function Attempts({
  ticker,
  caseId,
  node,
  first,
  view,
  rounds,
}: {
  ticker: string;
  caseId: string;
  node: string;
  first: Page<ModelAttempt>;
  view: string;
  rounds?: RuntimeRound[];
}) {
  const [more, setMore] = useState(false);
  const q = usePages<ModelAttempt, "Attempts">(
    "Attempts",
    more
      ? tickerPath(ticker) +
          `/runtime/cases/${id(caseId)}/attempts` +
          queryString({ node, limit: "20", view_id: view })
      : null,
    (r) => r.data,
    view,
    first,
  );
  const rows = q.data?.data.data?.items ?? first.items;
  const roundNames = rounds?.map((r) => r.round) ?? [
    ...new Set(rows.map((a) => a.round)),
  ];
  return (
    <>
      <table className="domain-table attempts">
        <thead>
          <tr>
            <th>节点 / 轮次</th>
            <th>尝试</th>
            <th>状态</th>
            <th>耗时（秒）</th>
          </tr>
        </thead>
        <tbody>
          {roundNames.map((round) => {
            const attempts = rows.filter((a) => a.round === round);
            const summary = rounds?.find((r) => r.round === round);
            return (
              <RoundRows
                key={round}
                node={node}
                round={round}
                summary={summary}
                attempts={attempts}
              />
            );
          })}
        </tbody>
      </table>
      {q.error && <Notice danger>尝试记录加载失败，请重试。</Notice>}
      {(q.hasNextPage || (!more && first.has_more)) && (
        <LoadMore
          variant="outline"
          onClick={() => (more ? void q.fetchNextPage() : setMore(true))}
        >
          更多尝试
        </LoadMore>
      )}
    </>
  );
}
export function RoundRows({
  node,
  round,
  summary,
  attempts,
}: {
  node: string;
  round: string;
  summary?: RuntimeRound;
  attempts: ModelAttempt[];
}) {
  return (
    <>
      <tr>
        <th colSpan={4}>
          {node} · {round}
          {summary ? ` · ${summary.attempt_count} 次尝试` : ""}
        </th>
      </tr>
      {!attempts.length && (
        <tr>
          <td>
            {node} · {round}
          </td>
          <td>—</td>
          <td colSpan={2}>
            {summary?.attempt_count
              ? "本轮尝试不在当前页，请加载更多尝试"
              : summary?.not_executed_reason === "NO_POLICY_CANDIDATE"
                ? "未执行：未召回候选 Policy"
                : summary?.not_executed_reason === "W2_SKIPPED"
                  ? "未执行：W2 已跳过"
                  : "暂无执行记录"}
          </td>
        </tr>
      )}
      {attempts.map((a) => (
        <tr key={a.attempt_id}>
          <td>
            {a.node_id} · {a.round}
          </td>
          <td>{a.attempt_number}</td>
          <td>{a.status}</td>
          <td>{valueText(a.timing.wall_seconds, (v) => v.toFixed(2))}</td>
        </tr>
      ))}
    </>
  );
}
export function EventEvidence({
  ticker,
  references,
  unresolved,
  attributions,
}: {
  ticker: string;
  references: EventLink[];
  unresolved?: string[];
  attributions?: FactAttribution[] | null;
}) {
  return (
    <div className="grid gap-2">
      <h4>引用 Event / Fact</h4>
      {!references.length && <p>未记录可解析的 Event / Fact 引用</p>}
      {references.map((r) => (
        <div className="grid gap-1" key={r.event_id}>
          <p>
            {r.kind === "PROVISIONAL" ? "临时事件" : "事件"} {r.event_id} ·{" "}
            {r.kind === "CANONICAL" && r.event_key ? (
              <a
                className="case-reference-link"
                href={eventHref(ticker, r)}
                target="_blank"
                rel="noopener noreferrer"
              >
                {r.title ? valueText(r.title) : r.event_id}
              </a>
            ) : r.title ? (
              valueText(r.title)
            ) : (
              "名称未记录"
            )}
          </p>
          {r.kind === "PROVISIONAL" ? (
            <p>
              {r.provisional_proposition
                ? valueText(r.provisional_proposition)
                : "临时事实未解析"}
            </p>
          ) : r.fact_ids.length ? (
            r.fact_ids.map((factId) => {
              const fact = r.facts?.find((f) => f.fact_id === factId);
              return (
                <p key={factId}>
                  事实 {factId} ·{" "}
                  {r.kind === "CANONICAL" && r.event_key ? (
                    <a
                      className="case-reference-link"
                      href={eventHref(ticker, r, factId)}
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      {fact ? valueText(fact.proposition) : factId}
                    </a>
                  ) : fact ? (
                    valueText(fact.proposition)
                  ) : (
                    "事实内容未解析"
                  )}
                </p>
              );
            })
          ) : (
            <p>Fact 归因未提供</p>
          )}
        </div>
      ))}
      {unresolved?.map((eventId) => (
        <div key={eventId}>
          <p>事件 {eventId} · 固定快照名称未解析</p>
          {attributions
            ?.find((a) => a.event_id === eventId)
            ?.fact_ids.map((factId) => (
              <p key={factId}>事实 {factId} · 固定快照内容未解析</p>
            ))}
        </div>
      ))}
    </div>
  );
}
export function PolicyEvidence({
  ticker,
  activationId,
  label,
  policies,
  unresolved,
  recorded = true,
  filter = "ACTIVE",
}: {
  ticker: string;
  activationId?: string;
  label: string;
  policies?: PolicyLink[];
  unresolved?: string[];
  recorded?: boolean;
  filter?: "ACTIVE" | "HIT";
}) {
  return (
    <div className="grid gap-2">
      <h4>{label}</h4>
      {!recorded ? (
        <p>尚未形成最终判定</p>
      ) : policies === undefined ? (
        <p>候选信息未记录</p>
      ) : !policies.length && !unresolved?.length ? (
        <p>无</p>
      ) : null}
      {policies?.map((p) => (
        <p key={p.policy_id}>
          策略 {p.policy_id} ·{" "}
          <a
            className="case-reference-link"
            href={policyHref(ticker, p, filter, activationId)}
            target="_blank"
            rel="noopener noreferrer"
          >
            {p.title ? valueText(p.title) : p.policy_id}
          </a>
          {p.condition_ids.length
            ? ` · 命中条件 ${p.condition_ids.join("、")}`
            : ""}
        </p>
      ))}
      {unresolved?.map((policyId) => (
        <p key={policyId}>策略 {policyId} · 固定快照名称未解析</p>
      ))}
    </div>
  );
}
function eventHref(ticker: string, event: EventLink, factId?: string) {
  return `/ticker/${id(ticker)}/events?${new URLSearchParams({
    mode: "FULL",
    snapshot: event.library_snapshot_id,
    event: event.event_id,
    ...(factId ? { fact: factId } : {}),
  })}`;
}
function policyHref(
  ticker: string,
  policy: PolicyLink,
  filter: "ACTIVE" | "HIT",
  activationId?: string,
) {
  return `/ticker/${id(ticker)}/strategy?${new URLSearchParams({
    period: "ALL",
    shell: "ALL",
    filter,
    policy: policy.policy_id,
    policy_set_version: String(policy.policy_set_version),
    ...(activationId ? { activation: activationId } : {}),
  })}`;
}
function CaseMessage({
  ticker,
  message,
}: {
  ticker: string;
  message: MessageSummary;
}) {
  return (
    <article className="case-message">
      <h4>{valueText(message.title)}</h4>
      <ContentReader ticker={ticker} content={message.body} />
    </article>
  );
}
function MoreMessages({
  ticker,
  caseId,
  view,
  first,
}: {
  ticker: string;
  caseId: string;
  view: string;
  first: Page<MessageSummary>;
}) {
  const [open, setOpen] = useState(false);
  const q = usePages<MessageSummary, "CaseMessages">(
    "CaseMessages",
    open
      ? tickerPath(ticker) +
          `/runtime/cases/${id(caseId)}/messages` +
          queryString({ view_id: view, limit: "20" })
      : null,
    (r) => r.data,
    view,
    first,
  );
  return (
    <>
      {(!open || q.hasNextPage) && (
        <LoadMore
          variant="outline"
          onClick={() => (open ? void q.fetchNextPage() : setOpen(true))}
        >
          更多消息
        </LoadMore>
      )}
      {open && (
        <Module query={q} label="Case 消息">
          {(d) =>
            d.items
              .slice(first.items.length)
              .map((m) => (
                <CaseMessage
                  key={m.standard_message_id}
                  ticker={ticker}
                  message={m}
                />
              ))
          }
        </Module>
      )}
    </>
  );
}
function Candidates({ ticker, caseId }: { ticker: string; caseId: string }) {
  const q = usePages<Candidate, "Candidates">(
    "Candidates",
    tickerPath(ticker) + `/runtime/cases/${id(caseId)}/candidates?limit=20`,
    (r) => r.data,
  );
  return (
    <section>
      <h3>事件发现</h3>
      <Module query={q} label="事件候选">
        {(d) =>
          d.items.map((c) => (
            <article className="candidate" key={c.candidate_id}>
              <span className="domain-tag">{c.originating_node}</span>
              <p>{c.proposition}</p>
              <small>
                {c.assertion_state} ·{" "}
                {c.subject_time ?? c.occurrence_date ?? "时间未记录"}
              </small>
            </article>
          ))
        }
      </Module>
      {q.hasNextPage && (
        <LoadMore onClick={() => void q.fetchNextPage()}>更多候选</LoadMore>
      )}
    </section>
  );
}
function Executions({ ticker, caseId }: { ticker: string; caseId: string }) {
  const q = usePages<ExecutionSummary, "Executions">(
    "Executions",
    tickerPath(ticker) + `/runtime/cases/${id(caseId)}/executions?limit=20`,
    (r) => r.data,
  );
  return (
    <section>
      <h3>交易执行</h3>
      <Module query={q} label="交易执行">
        {(d) =>
          d.items.map((e) => (
            <article className="execution-card" key={e.execution_id}>
              <header>
                <strong>{e.direction}</strong>
                <span>{e.has_actual_fill ? "已实际成交" : "未成交"}</span>
                <span>{e.entry_result ?? e.intake_status}</span>
              </header>
              <dl>
                <dt>成交数量</dt>
                <dd>{valueText(e.filled_quantity, (v) => decimal(v, 4))}</dd>
                <dt>成交金额</dt>
                <dd>
                  {valueText(e.filled_notional_usd, (v) => "$" + decimal(v, 2))}
                </dd>
                <dt>首次成交</dt>
                <dd>{valueText(e.first_fill_at, formatInstant)}</dd>
              </dl>
              {e.entry_reason && <p>{e.entry_reason}</p>}
            </article>
          ))
        }
      </Module>
      {q.hasNextPage && (
        <LoadMore onClick={() => void q.fetchNextPage()}>更多执行</LoadMore>
      )}
    </section>
  );
}
