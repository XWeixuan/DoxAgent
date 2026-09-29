import type {
  CaseDetail,
  Candidate,
  ContentChunk,
  ContentRef,
  ExecutionSummary,
  Fill,
  MessageSummary,
  ModelAttempt,
  OrderSummary,
  Page,
  Period,
  Resource,
} from "@contract";
import type { ApiClient, Endpoints } from "./api";
import { queryString } from "./api";
import { appendContent, readContentChunk } from "./content";
import { id, tickerPath } from "./page-query";

export interface ExportSelection {
  ticker: string;
  viewId: string;
  period: Period;
  tradingDays: string[] | null;
  result: string | null;
  sourceId: string | null;
  caseIds: string[];
}

export interface RuntimeCasesExport {
  format: "doxagent.runtime-cases.v1";
  exported_at: string;
  ticker: string;
  selection: {
    scope: "LOADED_SELECTED";
    period: Period;
    trading_days: string[] | null;
    result: string | null;
    source_id: string | null;
    case_count: number;
  };
  cases: unknown[];
}

function limiter(max: number) {
  let active = 0;
  const waiting: Array<() => void> = [];
  return async <T>(work: () => Promise<T>): Promise<T> => {
    if (active >= max)
      await new Promise<void>((resolve) => waiting.push(resolve));
    active++;
    try {
      return await work();
    } finally {
      active--;
      waiting.shift()?.();
    }
  };
}

function required<T>(resource: Resource<T>, label: string): T {
  if (!resource.data) throw new Error(`${label}读取不完整，请重试。`);
  return resource.data;
}

function businessError(error: ModelAttempt["error"]) {
  return error ? { code: error.code, message: error.message } : null;
}

function businessAttempt(attempt: ModelAttempt) {
  const {
    attempt_id,
    node_id,
    round,
    ordinal,
    attempt_number,
    status,
    timing,
  } = attempt;
  return {
    attempt_id,
    node_id,
    round,
    ordinal,
    attempt_number,
    status,
    timing,
    error: businessError(attempt.error),
  };
}

function dedupeBusinessItems<T>(items: T[]): T[] {
  const seen = new Set<string>();
  return items.filter((item) => {
    const value = item as Record<string, unknown>;
    const identity = [
      "attempt_id",
      "standard_message_id",
      "candidate_id",
      "fill_id",
      "order_attempt_id",
      "execution_id",
    ]
      .map((key) => value[key])
      .find((part) => typeof part === "string");
    if (!identity) return true;
    const key = `${identity}:${value.revision ?? ""}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

/** Collect every continuation under one frozen read view; reject a repeated cursor. */
async function fullPage<T, K extends keyof Endpoints>(
  api: ApiClient,
  limited: <R>(work: () => Promise<R>) => Promise<R>,
  name: K,
  path: string,
  view: string,
  first: Page<T>,
  signal: AbortSignal,
): Promise<T[]> {
  const items = [...first.items];
  let cursor = first.next_cursor;
  const visited = new Set<string>();
  while (cursor) {
    if (visited.has(cursor)) throw new Error("分页游标重复，导出已停止。");
    visited.add(cursor);
    const response = await limited(() =>
      api.request(name, path + `&cursor=${encodeURIComponent(cursor!)}`, {
        signal,
        view,
      }),
    );
    const page = required(response.data as Resource<Page<T>>, "分页");
    if (page.snapshot_id !== first.snapshot_id)
      throw new Error("分页快照变化，导出已停止。");
    items.push(...page.items);
    cursor = page.next_cursor;
    if (page.has_more && !cursor)
      throw new Error("分页缺少后续游标，导出已停止。");
  }
  if (first.has_more && !first.next_cursor)
    throw new Error("分页缺少后续游标，导出已停止。");
  return dedupeBusinessItems(items);
}

function summaryFields(detail: CaseDetail) {
  const s = detail.summary;
  return {
    case_id: s.case_id,
    title: s.title,
    source: s.source,
    semantic_day: s.semantic_day,
    runtime_mode: s.runtime_mode,
    status: s.status,
    technical_status: s.technical_status,
    received_at: s.received_at,
    completed_at: s.completed_at,
    duration_seconds: s.duration_seconds,
    initial_route: s.initial_route,
    resolved_route: s.resolved_route,
    final_novelty: s.final_novelty,
    final_policy_hit: s.final_policy_hit,
    results: s.results,
    trade: s.trade,
    trade_disposition: s.trade_disposition,
  };
}

function businessMessage(message: MessageSummary, body: unknown) {
  return {
    standard_message_id: message.standard_message_id,
    title: message.title,
    source: message.source,
    url: message.url,
    source_published_at: message.source_published_at,
    collected_at: message.collected_at,
    normalized_at: message.normalized_at,
    stream_published_at: message.stream_published_at,
    body,
  };
}

function businessExecution(
  summary: ExecutionSummary,
  orders: OrderSummary[],
  fills: Fill[],
) {
  const {
    execution_id,
    intent_id,
    direction,
    environment,
    profile_revision,
    intent_status,
    intake_status,
    execution_state,
    execution_reason_codes,
    entry_result,
    entry_reason,
    triggered_at,
    accepted_at,
    first_fill_at,
    filled_quantity,
    filled_notional_usd,
  } = summary;
  return {
    execution_id,
    intent_id,
    direction,
    environment,
    profile_revision,
    intent_status,
    intake_status,
    execution_state,
    execution_reason_codes,
    entry_result,
    entry_reason,
    triggered_at,
    accepted_at,
    first_fill_at,
    filled_quantity,
    filled_notional_usd,
    orders,
    fills,
  };
}

export async function collectRuntimeCases(
  api: ApiClient,
  selection: ExportSelection,
  signal: AbortSignal,
  onProgress?: (complete: number, total: number) => void,
  completedCases?: Map<string, unknown>,
): Promise<RuntimeCasesExport> {
  const caseIds = [...new Set(selection.caseIds)];
  const limited = limiter(3);
  const root = tickerPath(selection.ticker);
  const view = selection.viewId;
  const contentCache = new Map<
    string,
    Promise<{
      state: string;
      reason: string | null;
      content_type: string;
      text: string | null;
    }>
  >();

  const content = async (resource: Resource<ContentRef>) => {
    if (!resource.data)
      return {
        state: resource.state,
        reason: resource.reason,
        content_type: null,
        text: null,
      };
    const ref = resource.data;
    if (!contentCache.has(ref.content_id)) {
      const pending = (async () => {
        let chunks: ContentChunk[] = [],
          cursor: string | undefined;
        const visited = new Set<string>();
        do {
          if (cursor) {
            if (visited.has(cursor))
              throw new Error("正文游标重复，导出已停止。");
            visited.add(cursor);
          }
          const response = await limited(() =>
            readContentChunk(api, selection.ticker, ref, cursor, signal),
          );
          const chunk = required(response.data, "正文");
          chunks = appendContent(chunks, chunk, ref);
          cursor = chunk.next_cursor ?? undefined;
        } while (cursor);
        if (!chunks.at(-1)?.complete)
          throw new Error("正文未读取完整，导出已停止。");
        const text = chunks.map((chunk) => chunk.text).join("");
        const digest = await crypto.subtle.digest(
          "SHA-256",
          new TextEncoder().encode(text),
        );
        const hash = Array.from(new Uint8Array(digest), (byte) =>
          byte.toString(16).padStart(2, "0"),
        ).join("");
        if (hash !== ref.sha256) throw new Error("正文校验失败，导出已停止。");
        return {
          state: resource.state,
          reason: resource.reason,
          content_type: ref.content_type,
          text,
        };
      })();
      contentCache.set(ref.content_id, pending);
      void pending.catch(() => contentCache.delete(ref.content_id));
    }
    return contentCache.get(ref.content_id)!;
  };

  const collectOne = async (caseId: string) => {
    const casePath = `${root}/runtime/cases/${id(caseId)}`;
    const params = queryString({ view_id: view, limit: "20" });
    const response = await limited(() =>
      api.request("Case", casePath + queryString({ view_id: view }), {
        signal,
        view,
      }),
    );
    const detail = required(response.data, "研判明细");
    const attempts = async (
      node: "W1" | "W2" | "W3",
      first: Page<ModelAttempt>,
    ) =>
      (
        await fullPage(
          api,
          limited,
          "Attempts",
          casePath +
            "/attempts" +
            queryString({ node, view_id: view, limit: "20" }),
          view,
          first,
          signal,
        )
      ).map(businessAttempt);
    const w1 = detail.w1.data
      ? {
          novelty: detail.w1.data.novelty,
          confidence: detail.w1.data.confidence,
          rounds: detail.w1.data.rounds,
          references: detail.w1.data.references,
          fact_attributions: detail.w1.data.fact_attributions,
          unresolved_reference_ids: detail.w1.data.unresolved_reference_ids,
          timing: detail.w1.data.timing,
          attempts: await attempts("W1", detail.w1.data.attempts),
          reasoning: await content(detail.w1.data.reasoning),
        }
      : { state: detail.w1.state, reason: detail.w1.reason };
    const w2 = detail.w2.data
      ? {
          skipped: detail.w2.data.skipped,
          reasoning_stage: detail.w2.data.reasoning_stage,
          policy_hit: detail.w2.data.policy_hit,
          confidence: detail.w2.data.confidence,
          rounds: detail.w2.data.rounds,
          policies: detail.w2.data.policies,
          candidate_policies: detail.w2.data.candidate_policies,
          unresolved_policy_ids: detail.w2.data.unresolved_policy_ids,
          unresolved_candidate_policy_ids:
            detail.w2.data.unresolved_candidate_policy_ids,
          timing: detail.w2.data.timing,
          attempts: await attempts("W2", detail.w2.data.attempts),
          reasoning: await content(detail.w2.data.reasoning),
        }
      : { state: detail.w2.state, reason: detail.w2.reason };
    const w3 = detail.w3.data
      ? {
          status: detail.w3.data.status,
          mode: detail.w3.data.mode,
          novelty: detail.w3.data.novelty,
          policy_hit: detail.w3.data.policy_hit,
          expert_trade_evaluated: detail.w3.data.expert_trade_evaluated,
          expert_trade: detail.w3.data.expert_trade,
          direction: detail.w3.data.direction,
          prior_expectation: detail.w3.data.prior_expectation,
          expectation_delta: detail.w3.data.expectation_delta,
          references: detail.w3.data.references,
          policies: detail.w3.data.policies,
          unresolved_reference_ids: detail.w3.data.unresolved_reference_ids,
          unresolved_policy_ids: detail.w3.data.unresolved_policy_ids,
          timing: detail.w3.data.timing,
          attempts: await attempts("W3", detail.w3.data.attempts),
          reasoning: {
            novelty: await content(detail.w3.data.reasoning.novelty),
            policy: await content(detail.w3.data.reasoning.policy),
            expert_trade: await content(detail.w3.data.reasoning.expert_trade),
          },
        }
      : { state: detail.w3.state, reason: detail.w3.reason };
    const messages = await fullPage(
      api,
      limited,
      "CaseMessages",
      casePath + "/messages" + params,
      view,
      detail.messages,
      signal,
    );
    const candidatesResponse = await limited(() =>
      api.request("Candidates", casePath + "/candidates" + params, {
        signal,
        view,
      }),
    );
    const candidatePage = required(candidatesResponse.data, "事件候选");
    const candidates = (
      await fullPage<Candidate, "Candidates">(
        api,
        limited,
        "Candidates",
        casePath + "/candidates" + params,
        view,
        candidatePage,
        signal,
      )
    ).map((c) => ({
      candidate_id: c.candidate_id,
      dedupe_key: c.dedupe_key,
      originating_node: c.originating_node,
      proposition: c.proposition,
      assertion_state: c.assertion_state,
      subject_time: c.subject_time,
      occurrence_date: c.occurrence_date,
      entities: c.entities,
      provisional_event_id: c.provisional_event_id,
      created_at: c.created_at,
    }));
    const executionsResponse = await limited(() =>
      api.request("Executions", casePath + "/executions" + params, {
        signal,
        view,
      }),
    );
    const executionPage = required(executionsResponse.data, "交易记录");
    const executions = await fullPage<ExecutionSummary, "Executions">(
      api,
      limited,
      "Executions",
      casePath + "/executions" + params,
      view,
      executionPage,
      signal,
    );
    const executionDetails = [];
    for (const execution of executions) {
      const executionPath = `${root}/executions/${id(execution.execution_id)}`;
      const response = await limited(() =>
        api.request("Execution", executionPath + params, { signal, view }),
      );
      const data = required(response.data, "交易详情");
      const orders = await fullPage<OrderSummary, "Orders">(
        api,
        limited,
        "Orders",
        executionPath + "/orders" + params,
        view,
        data.orders,
        signal,
      );
      const fills = await fullPage<Fill, "Fills">(
        api,
        limited,
        "Fills",
        executionPath + "/fills" + params,
        view,
        data.fills,
        signal,
      );
      executionDetails.push(
        businessExecution(
          data.execution,
          orders.map((o) => ({
            order_attempt_id: o.order_attempt_id,
            execution_id: o.execution_id,
            leg: o.leg,
            side: o.side,
            order_type: o.order_type,
            quantity: o.quantity,
            limit_price: o.limit_price,
            state: o.state,
            broker_status: o.broker_status,
            sent_at: o.sent_at,
            settled_at: o.settled_at,
          })),
          fills.map((f) => ({
            fill_id: f.fill_id,
            execution_id: f.execution_id,
            order_attempt_id: f.order_attempt_id,
            correction_revision: f.correction_revision,
            leg: f.leg,
            side: f.side,
            executed_at: f.executed_at,
            quantity: f.quantity,
            price: f.price,
            commission_usd: f.commission_usd,
          })),
        ),
      );
    }
    const messageDetails = [];
    for (const message of messages) {
      messageDetails.push(
        businessMessage(
          message,
          await content({
            state: "AVAILABLE",
            data: message.body,
            reason: null,
            coverage: detail.w1.coverage,
          }),
        ),
      );
    }
    const failures: Array<{
      stage: string;
      status: string;
      error: ReturnType<typeof businessError>;
      occurred_at: string | null;
    }> = detail.failures.map((failure) => ({
      stage: failure.stage,
      status: failure.status,
      error: businessError(failure.error),
      occurred_at: failure.occurred_at,
    }));
    for (const attempt of [
      ...(w1 && "attempts" in w1 ? (w1.attempts ?? []) : []),
      ...(w2 && "attempts" in w2 ? (w2.attempts ?? []) : []),
      ...(w3 && "attempts" in w3 ? (w3.attempts ?? []) : []),
    ]) {
      if (!attempt.error) continue;
      if (
        failures.some(
          (failure) =>
            failure.stage === attempt.node_id &&
            failure.occurred_at === attempt.timing.completed_at.value &&
            failure.error?.code === attempt.error?.code,
        )
      )
        continue;
      failures.push({
        stage: attempt.node_id,
        status: attempt.status,
        error: attempt.error,
        occurred_at: attempt.timing.completed_at.value ?? null,
      });
    }
    return {
      summary: summaryFields(detail),
      w1,
      w2,
      w3,
      messages: messageDetails,
      candidates,
      executions: executionDetails,
      failures,
      references: {
        runtime_activation_id: detail.runtime_activation_id,
        library_snapshot_id: detail.library.library_snapshot_id,
        library_version: detail.library.library_version,
        policy_set_version: detail.policy_set.policy_set_version,
        provisional_snapshot_version: detail.provisional_snapshot_version,
      },
      hot_path: detail.hot_path,
    };
  };

  const cases: unknown[] = [];
  for (const caseId of caseIds) {
    if (signal.aborted) throw new DOMException("导出已取消", "AbortError");
    let collected = completedCases?.get(caseId);
    if (!collected) {
      collected = await collectOne(caseId);
      completedCases?.set(caseId, collected);
    }
    cases.push(collected);
    onProgress?.(cases.length, caseIds.length);
  }
  return {
    format: "doxagent.runtime-cases.v1",
    exported_at: new Date().toISOString(),
    ticker: selection.ticker,
    selection: {
      scope: "LOADED_SELECTED",
      period: selection.period,
      trading_days: selection.tradingDays,
      result: selection.result,
      source_id: selection.sourceId,
      case_count: cases.length,
    },
    cases,
  };
}

export function downloadRuntimeCases(result: RuntimeCasesExport) {
  const filename = `doxagent-${result.ticker.replace(/[^a-zA-Z0-9_-]/g, "_")}-runtime-cases-${result.exported_at.replace(/[-:]/g, "").replace(/\.\d+Z$/, "Z")}.json`;
  const blob = new Blob([JSON.stringify(result, null, 2) + "\n"], {
    type: "application/json;charset=utf-8",
  });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
