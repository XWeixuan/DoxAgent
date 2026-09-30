import type {
  CaseDetail,
  Candidate,
  ContentChunk,
  ContentRef,
  ExecutionSummary,
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

const resultLabels: Record<string, string> = {
  ARCHIVE: "归档",
  EVENT_DISCOVERY: "事件发现",
  BADCASE: "Badcase",
  TRADE_INTENT: "交易意图产生",
  TRADE_EXECUTION: "交易执行",
  TRADE_NOT_EXECUTED: "交易未执行",
  FAILURE: "失败",
};
function references(
  items: NonNullable<CaseDetail["w1"]["data"]>["references"],
) {
  return items.map((r) =>
    r.kind === "PROVISIONAL"
      ? {
          事件编号: r.event_id,
          类型: "临时事实",
          事实内容: r.provisional_proposition?.value ?? null,
        }
      : {
          事件编号: r.event_id,
          事件名称: r.title?.value ?? null,
          ...(r.facts?.length
            ? {
                引用事实: r.facts.map((f) => ({
                  编号: f.fact_id,
                  内容: f.proposition.value ?? null,
                })),
              }
            : {}),
        },
  );
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
    const policyCache = new Map<string, Promise<unknown>>();
    const policy = (policyId: string) => {
      if (!policyCache.has(policyId))
        policyCache.set(
          policyId,
          (async () => {
            const response = await limited(() =>
              api.request(
                "Policy",
                casePath +
                  `/policies/${id(policyId)}` +
                  queryString({ view_id: view }),
                { signal, view },
              ),
            );
            const p = required(response.data, "固定版本策略").policy;
            return {
              "Policy ID": p.policy_id,
              名称: p.title,
              方向: p.decision,
              匹配范围: p.match_scope,
              激活条件: p.activation_conditions.map((c) => ({
                编号: c.condition_id,
                条件内容: c.criterion,
                参考状态: c.calibration.reference_state,
                触发边界: c.calibration.trigger_boundary,
              })),
              关联预期: p.source_refs.map((r) => ({
                Shell: r.shell_id,
                Unit: r.expectation_id,
                Gap: r.gap_id,
              })),
            };
          })(),
        );
      return policyCache.get(policyId)!;
    };
    const policyList = (links: Array<{ policy_id: string }> = []) =>
      Promise.all(links.map((p) => policy(p.policy_id)));
    const text = async (ref: Resource<ContentRef>) => (await content(ref)).text;
    const first = detail.w1.data,
      second = detail.w2.data,
      third = detail.w3.data;
    if (
      second?.unresolved_candidate_policy_ids?.length ||
      second?.unresolved_policy_ids?.length
    )
      throw new Error("Case 固定版本的 Policy 缺失，导出已停止。");
    const w1 = {
      结论: first?.novelty.value ?? null,
      置信度: first?.confidence.value ?? null,
      判断理由: first ? await text(first.reasoning) : null,
      引用依据: [
        ...references(first?.references ?? []),
        ...(first?.unresolved_reference_ids ?? []).map((eventId) => ({
          事件编号: eventId,
          事件名称: null,
        })),
      ],
    };
    const w2 = {
      是否跳过: second?.skipped ?? null,
      是否命中: second?.policy_hit.value ?? null,
      置信度: second?.confidence.value ?? null,
      召回策略: await policyList(second?.candidate_policies),
      命中策略: await policyList(second?.policies),
      判断理由: second ? await text(second.reasoning) : null,
    };
    const w3 = {
      研判模式: third?.mode ?? null,
      新旧复判: {
        结论: third?.novelty.value ?? null,
        判断理由: third ? await text(third.reasoning.novelty) : null,
      },
      "Policy 复判": {
        是否命中: third?.policy_hit.value ?? null,
        判断理由: third ? await text(third.reasoning.policy) : null,
      },
      专家交易判断: {
        建议交易: third?.expert_trade.value ?? null,
        方向: third?.direction.value ?? null,
        原有预期: third?.prior_expectation.value ?? null,
        预期变化: third?.expectation_delta.value ?? null,
        判断理由: third ? await text(third.reasoning.expert_trade) : null,
      },
      引用依据: references(third?.references ?? []),
      未能解析的事件引用: third?.unresolved_reference_ids ?? [],
    };
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
      内容: c.proposition,
      断言状态: c.assertion_state,
      所属时期: c.subject_time,
      发生日期: c.occurrence_date,
      产生节点: c.originating_node.split("_")[0],
      临时事件编号: c.provisional_event_id,
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
    const executionDetails = executions.map((e) => ({
      方向: e.direction,
      环境: e.environment.value ?? null,
      意图产生时间: e.triggered_at.value ?? null,
      执行结果: e.execution_state,
      原因: e.entry_reason ?? (e.execution_reason_codes.join(", ") || null),
      成交数量: e.filled_quantity.value ?? null,
      成交金额USD: e.filled_notional_usd.value ?? null,
    }));
    const messageDetails = [];
    for (const message of messages)
      messageDetails.push({
        发布时间: message.source_published_at,
        正文: await text({
          state: "AVAILABLE",
          data: message.body,
          reason: null,
          coverage: detail.w1.coverage,
        }),
      });
    const s = detail.summary;
    return {
      基本信息: {
        "Case ID": s.case_id,
        标题: s.title.value ?? null,
        来源: s.source.name,
        语义交易日: s.semantic_day,
        接收时间: s.received_at,
        完成时间: s.completed_at.value ?? null,
        处理状态: s.status,
        处理结果: s.results.map((r) => resultLabels[r] ?? r),
      },
      消息原文: messageDetails,
      "W1 新旧判断": w1,
      "W2 Policy 判断": w2,
      "W3 二轮研判": w3,
      发现的新事实: candidates,
      交易意图与执行: executionDetails,
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
  const blob = new Blob(
    [
      JSON.stringify(
        result.cases.length === 1 ? result.cases[0] : result.cases,
        null,
        2,
      ) + "\n",
    ],
    {
      type: "application/json;charset=utf-8",
    },
  );
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
