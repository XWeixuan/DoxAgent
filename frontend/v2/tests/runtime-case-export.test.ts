import { describe, expect, it, vi } from "vitest";
import type { ApiClient } from "../src/core/api";
import {
  collectRuntimeCases,
  downloadRuntimeCases,
} from "../src/core/runtime-case-export";

const coverage = {
  state: "COMPLETE",
  reasons: [],
  known_count: null,
  excluded_count: null,
  observed_through: null,
};
const resource = (data: unknown) => ({
  state: "AVAILABLE",
  data,
  reason: null,
  coverage,
});
const page = (items: unknown[], cursor: string | null = null) => ({
  items,
  has_more: cursor !== null,
  next_cursor: cursor,
  snapshot_id: "snapshot",
  limit: 20,
});
const value = (raw: unknown) => ({
  state: "AVAILABLE",
  value: raw,
  reason: null,
});

async function fixture(failContent = false) {
  const bodyText = "中文正文 part one\nEnglish second part";
  const sha = Array.from(
    new Uint8Array(
      await crypto.subtle.digest("SHA-256", new TextEncoder().encode(bodyText)),
    ),
  )
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
  const ref = {
    content_id: "body-1",
    content_type: "text/plain",
    size_bytes: new TextEncoder().encode(bodyText).length,
    sha256: sha,
  };
  const messages = Array.from({ length: 21 }, (_, index) => ({
    standard_message_id: `message-${index}`,
    revision: 1,
    title: value(`Message ${index}`),
    source: { source_id: "news", name: "News", kind: "api" },
    url: "https://example.com/news",
    source_published_at: "2026-09-29T10:00:00Z",
    collected_at: "2026-09-29T10:01:00Z",
    normalized_at: "2026-09-29T10:02:00Z",
    stream_published_at: "2026-09-29T10:03:00Z",
    body: ref,
  }));
  const attempts = Array.from({ length: 21 }, (_, index) => ({
    attempt_id: `attempt-${index}`,
    node_id: "W1",
    round: "R1",
    ordinal: index,
    attempt_number: 1,
    status: "SUCCEEDED",
    timing: { completed_at: value("2026-09-29T10:05:00Z") },
    error: null,
  }));
  const candidates = Array.from({ length: 21 }, (_, index) => ({
    candidate_id: `candidate-${index}`,
    originating_node: "W1_R3",
    proposition: `Fact ${index}`,
    assertion_state: "CONFIRMED",
    subject_time: null,
    occurrence_date: null,
    entities: ["MU"],
    provisional_event_id: null,
    created_at: "2026-09-29T10:00:00Z",
  }));
  const executions = [
    {
      execution_id: "execution-1",
      intent_id: "intent-1",
      direction: "LONG",
      environment: value("PAPER"),
      profile_revision: value("rev-1"),
      intent_status: "READY",
      intake_status: "EXECUTION_ACCEPTED",
      execution_state: "EXECUTED",
      execution_reason_codes: [],
      entry_result: "FILLED",
      entry_reason: null,
      triggered_at: value("2026-09-29T10:00:00Z"),
      accepted_at: value("2026-09-29T10:01:00Z"),
      first_fill_at: value("2026-09-29T10:02:00Z"),
      filled_quantity: value("1.000000000000000001"),
      filled_notional_usd: value("100.000000000000000001"),
    },
  ];
  const orders = Array.from({ length: 21 }, (_, index) => ({
    order_attempt_id: `order-${index}`,
    execution_id: "execution-1",
    leg: "ENTRY",
    side: "BUY",
    order_type: "LMT",
    quantity: "1",
    limit_price: "100",
    state: "FILLED",
    broker_status: "Filled",
    sent_at: value("2026-09-29T10:01:00Z"),
    settled_at: value("2026-09-29T10:02:00Z"),
  }));
  const fills = Array.from({ length: 21 }, (_, index) => ({
    fill_id: `fill-${index}`,
    execution_id: "execution-1",
    order_attempt_id: `order-${index}`,
    correction_revision: 1,
    leg: "ENTRY",
    side: "BUY",
    executed_at: "2026-09-29T10:02:00Z",
    quantity: "1",
    price: "100",
    commission_usd: value("0.01"),
  }));
  const detail = {
    summary: {
      case_id: "case-1",
      title: value("News"),
      source: { source_id: "news", name: "News", kind: "api" },
      semantic_day: "2026-09-29",
      runtime_mode: "REALTIME",
      status: "COMPLETED",
      technical_status: "OK",
      received_at: "2026-09-29T10:00:00Z",
      completed_at: value("2026-09-29T10:05:00Z"),
      duration_seconds: value(300),
      results: ["EVENT_DISCOVERY"],
      trade: { intent_count: 0, state: "NOT_APPLICABLE", reason_codes: [] },
      trade_disposition: "NOT_EVALUATED",
    },
    w1: resource({
      novelty: value("NEW"),
      confidence: value("HIGH"),
      references: [],
      attempts: page(attempts.slice(0, 20), "next"),
      reasoning: resource(ref),
    }),
    w2: resource({
      skipped: false,
      policy_hit: value(true),
      confidence: value("HIGH"),
      policies: [
        {
          policy_id: "policy-1",
          policy_set_version: 1,
          condition_ids: ["condition-1"],
        },
      ],
      candidate_policies: [
        { policy_id: "policy-1", policy_set_version: 1, condition_ids: [] },
      ],
      attempts: page([]),
      reasoning: resource(ref),
    }),
    w3: resource({
      status: "RESOLVED",
      mode: "UNCOVERED_NEW",
      novelty: value("NEW"),
      policy_hit: value(true),
      expert_trade_evaluated: value(true),
      expert_trade: value(true),
      direction: value("LONG"),
      prior_expectation: value("Previous"),
      expectation_delta: value("Changed"),
      references: [],
      policies: [],
      attempts: page([]),
      reasoning: {
        novelty: resource(ref),
        policy: resource(ref),
        expert_trade: resource(ref),
      },
    }),
    messages: page(messages.slice(0, 20), "next"),
    failures: [],
    runtime_activation_id: value("activation"),
    library: { library_snapshot_id: "library", library_version: 1 },
    policy_set: { policy_set_version: 1 },
    provisional_snapshot_version: 0,
    hot_path: { wall_seconds: value(300) },
  };
  const calls: string[] = [];
  const client = {
    async request(name: string, path: string) {
      calls.push(`${name}:${path}`);
      if (name === "Case") return { data: resource(detail) };
      if (name === "Policy")
        return {
          data: resource({
            policy: {
              policy_id: "policy-1",
              title: "Pinned policy",
              decision: "SHORT",
              match_scope: "full scope".repeat(200),
              activation_conditions: ["C1", "C3", "C9"].map((condition_id) => ({
                condition_id,
                criterion: "criterion".repeat(200),
                calibration: {
                  reference_state: "reference".repeat(200),
                  trigger_boundary: "boundary".repeat(200),
                },
              })),
              source_refs: [
                { shell_id: "S1", expectation_id: "U2", gap_id: "G3" },
              ],
            },
          }),
        };

      if (name === "Attempts")
        return {
          data: resource(
            page(
              path.includes("cursor=next")
                ? attempts.slice(20)
                : attempts.slice(0, 20),
              path.includes("cursor=next") ? null : "next",
            ),
          ),
        };
      if (name === "CaseMessages")
        return {
          data: resource(
            page(
              path.includes("cursor=next")
                ? messages.slice(20)
                : messages.slice(0, 20),
              path.includes("cursor=next") ? null : "next",
            ),
          ),
        };
      if (name === "Candidates")
        return {
          data: resource(
            page(
              path.includes("cursor=next")
                ? candidates.slice(20)
                : candidates.slice(0, 20),
              path.includes("cursor=next") ? null : "next",
            ),
          ),
        };
      if (name === "Executions") return { data: resource(page(executions)) };
      if (name === "Execution")
        return {
          data: resource({
            execution: executions[0],
            orders: page(orders.slice(0, 20), "next"),
            fills: page(fills.slice(0, 20), "next"),
          }),
        };
      if (name === "Orders")
        return {
          data: resource(
            page(
              path.includes("cursor=next")
                ? orders.slice(20)
                : orders.slice(0, 20),
              path.includes("cursor=next") ? null : "next",
            ),
          ),
        };
      if (name === "Fills")
        return {
          data: resource(
            page(
              path.includes("cursor=next")
                ? fills.slice(20)
                : fills.slice(0, 20),
              path.includes("cursor=next") ? null : "next",
            ),
          ),
        };
      if (name === "Content") {
        if (failContent) throw new Error("network failed");
        const next = path.includes("cursor=second");
        return {
          data: resource({
            content: ref,
            chunk_index: next ? 1 : 0,
            text: next ? "English second part" : "中文正文 part one\n",
            complete: next,
            next_cursor: next ? null : "second",
          }),
        };
      }
      throw new Error(`unexpected ${name}`);
    },
  } as unknown as ApiClient;
  return { client, calls, bodyText, detail };
}

interface ExportedCase {
  基本信息: { 处理结果: string[] };
  消息原文: Array<{ 正文: string }>;
  "W1 新旧判断": { 判断理由: string | null };
  "W2 Policy 判断": {
    判断理由: string | null;
    召回策略: Array<{
      方向: string;
      匹配范围: string;
      激活条件: Array<{ 编号: string; 触发边界: string }>;
    }>;
  };
  "W3 二轮研判": {
    专家交易判断: { 判断理由: string | null; 建议交易: boolean | null };
  };
  发现的新事实: unknown[];
  交易意图与执行: Array<{ 成交数量: string }>;
}

const selection = {
  ticker: "MU",
  viewId: "view-1",
  period: "CURRENT_TRADING_DAY" as const,
  tradingDays: ["2026-09-29"],
  result: null,
  sourceId: null,
  caseIds: ["case-1", "case-1"],
};

describe("Runtime Case JSON export", () => {
  it("collects all pages and all immutable body chunks once under the frozen view", async () => {
    const { client, calls, bodyText } = await fixture();
    const result = await collectRuntimeCases(
      client,
      selection,
      new AbortController().signal,
    );
    expect(result.selection.case_count).toBe(1);
    const entry = result.cases[0] as ExportedCase;
    expect(Object.keys(entry)).toEqual([
      "基本信息",
      "消息原文",
      "W1 新旧判断",
      "W2 Policy 判断",
      "W3 二轮研判",
      "发现的新事实",
      "交易意图与执行",
    ]);
    expect(entry["消息原文"]).toHaveLength(21);
    expect(entry["发现的新事实"]).toHaveLength(21);
    expect(entry["W1 新旧判断"]["判断理由"]).toBe(bodyText);
    expect(entry["W2 Policy 判断"]["判断理由"]).toBe(bodyText);
    expect(entry["W3 二轮研判"]["专家交易判断"]["判断理由"]).toBe(bodyText);
    const policy = entry["W2 Policy 判断"]["召回策略"][0];
    expect(policy["方向"]).toBe("SHORT");
    expect(policy["匹配范围"]).toBe("full scope".repeat(200));
    expect(policy["激活条件"]).toHaveLength(3);
    expect(policy["激活条件"][2]["编号"]).toBe("C9");
    expect(policy["激活条件"][2]["触发边界"]).toBe("boundary".repeat(200));
    expect(entry["交易意图与执行"][0]["成交数量"]).toBe("1.000000000000000001");
    expect(entry["消息原文"][20]["正文"]).toBe(bodyText);
    expect(entry["基本信息"]["处理结果"]).toEqual(["事件发现"]);
    expect(calls.filter((call) => call.startsWith("Content:"))).toHaveLength(2);
    expect(calls.filter((call) => call.startsWith("Policy:"))).toHaveLength(1);
    expect(calls.find((call) => call.startsWith("Policy:"))).toContain(
      "/cases/case-1/policies/policy-1?view_id=view-1",
    );
    expect(
      calls.some((call) => /^(Attempts|Execution|Orders|Fills):/.test(call)),
    ).toBe(false);
    const json = JSON.stringify(entry);
    for (const field of [
      "attempt_id",
      "revision",
      "coverage",
      "profile_revision",
      "hot_path",
      "content_id",
      "sha256",
      "view_id",
      "selection",
      "state",
      "orders",
      "fills",
    ])
      expect(json).not.toContain(`"${field}"`);
  });

  it("rejects a failed content read instead of producing partial JSON", async () => {
    const { client } = await fixture(true);
    await expect(
      collectRuntimeCases(client, selection, new AbortController().signal),
    ).rejects.toThrow("network failed");
  });
  it("exports absent stages as null values and empty lists", async () => {
    const { client, detail } = await fixture();
    Object.assign(detail, {
      w1: resource(null),
      w2: resource(null),
      w3: resource(null),
    });
    const result = await collectRuntimeCases(
      client,
      selection,
      new AbortController().signal,
    );
    const entry = result.cases[0] as ExportedCase;
    expect(entry["W1 新旧判断"]).toEqual({
      结论: null,
      置信度: null,
      判断理由: null,
      引用依据: [],
    });
    expect(entry["W2 Policy 判断"]["召回策略"]).toEqual([]);
    expect(entry["W3 二轮研判"]["专家交易判断"]["建议交易"]).toBeNull();
  });

  it("stops when a pinned policy definition cannot be resolved", async () => {
    const { client, detail } = await fixture();
    Object.assign(detail.w2.data as object, {
      unresolved_candidate_policy_ids: ["missing-policy"],
    });
    await expect(
      collectRuntimeCases(client, selection, new AbortController().signal),
    ).rejects.toThrow("固定版本的 Policy 缺失");
  });

  it("downloads one template object or an array without export metadata", async () => {
    const { client } = await fixture();
    const result = await collectRuntimeCases(
      client,
      selection,
      new AbortController().signal,
    );
    let blob: Blob | undefined;
    const link = { click: vi.fn(), remove: vi.fn(), href: "", download: "" };
    vi.stubGlobal("document", {
      createElement: () => link,
      body: { appendChild: vi.fn() },
    });
    vi.spyOn(URL, "createObjectURL").mockImplementation((value) => {
      blob = value as Blob;
      return "blob:export";
    });
    vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
    vi.useFakeTimers();
    try {
      downloadRuntimeCases(result);
      expect(JSON.parse(await blob!.text())).toEqual(result.cases[0]);
      downloadRuntimeCases({
        ...result,
        cases: [result.cases[0], result.cases[0]],
      });
      expect(JSON.parse(await blob!.text())).toEqual([
        result.cases[0],
        result.cases[0],
      ]);
      expect(link.click).toHaveBeenCalledTimes(2);
      vi.runAllTimers();
    } finally {
      vi.useRealTimers();
      vi.restoreAllMocks();
      vi.unstubAllGlobals();
    }
  });
});
