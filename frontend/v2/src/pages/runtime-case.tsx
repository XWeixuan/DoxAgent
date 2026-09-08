import { LoadMore } from "@/components/load-more";
import { useState } from "react";
import type {
  ContentRef,
  Resource,
  ModelAttempt,
  Page,
  MessageSummary,
  Candidate,
  ExecutionSummary,
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
                      {r.event_id} {r.fact_ids.join("、")}
                    </span>
                  ))}
                </div>
                <Attempts
                  ticker={ticker}
                  caseId={caseId}
                  node="W1"
                  first={d.w1.data.attempts}
                />
                <Reason ticker={ticker} content={d.w1.data.reasoning} />
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
                    {valueText(d.w2.data.policy_hit, (v) =>
                      v ? "命中" : "未命中",
                    )}
                  </strong>
                  <span>置信度 {valueText(d.w2.data.confidence)}</span>
                  {d.w2.data.policies.map((p) => (
                    <span key={p.policy_id}>
                      {p.policy_id} · {p.condition_ids.join("、")}
                    </span>
                  ))}
                </div>
                <Attempts
                  ticker={ticker}
                  caseId={caseId}
                  node="W2"
                  first={d.w2.data.attempts}
                />
                <Reason ticker={ticker} content={d.w2.data.reasoning} />
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
                  {r.event_id} {r.fact_ids.join("、")}
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
}: {
  ticker: string;
  content: Resource<ContentRef>;
  label?: string;
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
      {open &&
        (content.data ? (
          <ContentReader ticker={ticker} content={content.data} />
        ) : (
          <Notice>判断依据未记录</Notice>
        ))}
    </div>
  );
}
function Attempts({
  ticker,
  caseId,
  node,
  first,
}: {
  ticker: string;
  caseId: string;
  node: string;
  first: Page<ModelAttempt>;
}) {
  const [more, setMore] = useState(false);
  const q = usePages<ModelAttempt, "Attempts">(
    "Attempts",
    more
      ? tickerPath(ticker) +
          `/runtime/cases/${id(caseId)}/attempts` +
          queryString({ node, limit: "20" })
      : null,
    (r) => r.data,
    undefined,
    first,
  );
  const rows = q.data?.data.data?.items ?? first.items;
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
          {rows.map((a) => (
            <tr key={a.attempt_id}>
              <td>
                {a.node_id} · {a.round}
              </td>
              <td>{a.attempt_number}</td>
              <td>{a.status}</td>
              <td>{valueText(a.timing.wall_seconds, (v) => v.toFixed(2))}</td>
            </tr>
          ))}
        </tbody>
      </table>
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
