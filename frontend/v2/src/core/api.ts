import Ajv from "ajv";
import type * as S from "./schema-source";
import type { Meta } from "@contract";
import schema from "./wire-schema.json";

export const API_PREFIX = "/api/doxagent/v2";
export type Endpoints = {
  Research: S.WireResearch;
  Runs: S.WireRuns;
  FutureNodes: S.WireFutureNodes;
  Expectations: S.WireExpectations;
  Shells: S.WireShells;
  ShellContent: S.WireShellContent;
  Unit: S.WireUnit;
  PolicyContext: S.WirePolicyContext;
  PolicyShells: S.WirePolicyShells;
  PolicyMetrics: S.WirePolicyMetrics;
  Policies: S.WirePolicies;
  Policy: S.WirePolicy;
  Changes: S.WireChanges;
  Library: S.WireLibrary;
  EventMetrics: S.WireEventMetrics;
  Events: S.WireEvents;
  Event: S.WireEvent;
  Facts: S.WireFacts;
  DeltaDays: S.WireDeltaDays;
  Delta: S.WireDelta;
  BusStatus: S.WireBusStatus;
  BusMetrics: S.WireBusMetrics;
  Sources: S.WireSources;
  Messages: S.WireMessages;
  Message: S.WireMessage;
  Binding: S.WireBinding;
  AvailableSources: S.WireAvailableSources;
  AvailableSource: S.WireAvailableSource;
  RuntimeMetrics: S.WireRuntimeMetrics;
  Graph: S.WireGraph;
  Node: S.WireNode;
  Cases: S.WireCases;
  Case: S.WireCase;
  Attempts: S.WireAttempts;
  CaseMessages: S.WireCaseMessages;
  Candidates: S.WireCandidates;
  Executions: S.WireExecutions;
  Execution: S.WireExecution;
  Orders: S.WireOrders;
  Fills: S.WireFills;
  CostFilters: S.WireCostFilters;
  CostSummary: S.WireCostSummary;
  CostTrend: S.WireCostTrend;
  CostBreakdown: S.WireCostBreakdown;
  CostNodes: S.WireCostNodes;
  BindingWrite: S.WireBindingWrite;
  BindingReceipt: S.WireBindingReceipt;
  AuthConfig: S.WireAuthConfig;
  Principal: S.WirePrincipal;
  Capabilities: S.WireCapabilities;
  ReadContext: S.WireReadContext;
  Status: S.WireStatus;
  Metrics: S.WireMetrics;
  Tickers: S.WireTickers;
  Navigation: S.WireNavigation;
  Ticker: S.WireTicker;
  Initialization: S.WireInitialization;
  Operation: S.WireOperation;
  Content: S.WireContent;
  Citations: S.WireCitations;
};
const ajv = new Ajv({ strict: false, allErrors: false });
ajv.addSchema(schema, "wire");
export function validateStream(
  name: "MessageStream" | "GraphStream",
  body: unknown,
) {
  if (!ajv.getSchema(`wire#/definitions/Wire${name}`)?.(body))
    throw new Error("实时数据不符合 V2 契约");
}
export class ApiFailure extends Error {
  constructor(
    public code: string,
    message: string,
    public status = 0,
    public requestId?: string,
  ) {
    super(message);
  }
}
export function validateWire<K extends keyof Endpoints>(
  name: K,
  body: unknown,
): Endpoints[K] {
  const validate = ajv.getSchema(`wire#/definitions/Wire${name}`);
  if (!validate?.(body))
    throw new ApiFailure(
      "INVALID_RESPONSE",
      "返回数据不符合 V2 契约，无法显示该模块。",
    );
  return body as Endpoints[K];
}
export interface AuthDriver {
  token(): string | null;
  refresh(): Promise<boolean>;
  invalidate(): void;
}
export class ApiClient {
  private epoch = 0;
  private controllers = new Set<AbortController>();
  private refreshPromise: Promise<boolean> | null = null;
  constructor(
    private auth: AuthDriver,
    private fetcher: typeof fetch = (...args) => fetch(...args),
  ) {}
  reset() {
    this.epoch++;
    this.controllers.forEach((c) => c.abort());
    this.controllers.clear();
  }
  async download(
    path: string,
    signal?: AbortSignal,
  ): Promise<{ blob: Blob; filename: string }> {
    if (!path.startsWith("/tickers/") || !path.includes("/download"))
      throw new ApiFailure("INVALID_DOWNLOAD", "下载目标无效。");
    const epoch = this.epoch,
      controller = new AbortController();
    const abort = () => controller.abort();
    signal?.addEventListener("abort", abort, { once: true });
    if (signal?.aborted) controller.abort();
    this.controllers.add(controller);
    try {
      const send = () =>
        this.fetcher(API_PREFIX + path, {
          headers: { Authorization: `Bearer ${this.auth.token() ?? ""}` },
          signal: controller.signal,
          cache: "no-store",
          redirect: "error",
        });
      let response = await send();
      if (response.status === 401) {
        this.refreshPromise ??= this.auth.refresh().finally(() => {
          this.refreshPromise = null;
        });
        if ((await this.refreshPromise) && epoch === this.epoch)
          response = await send();
        else this.auth.invalidate();
      }
      if (!response.ok) {
        if (response.status === 401) this.auth.invalidate();
        throw new ApiFailure(
          "DOWNLOAD_FAILED",
          "下载失败，请重试。",
          response.status,
        );
      }
      const blob = await response.blob();
      if (epoch !== this.epoch || controller.signal.aborted)
        throw new DOMException("Session changed", "AbortError");
      const disposition = response.headers.get("content-disposition") || "";
      const encoded = /filename\*=UTF-8''([^;]+)/i.exec(disposition)?.[1];
      const plain = /filename="?([^";]+)"?/i.exec(disposition)?.[1];
      const filename = (
        encoded ? decodeURIComponent(encoded) : plain || "doxagent-artifact"
      ).replace(/[\\/:*?"<>|\x00-\x1f]/g, "_");
      return { blob, filename };
    } finally {
      this.controllers.delete(controller);
      signal?.removeEventListener("abort", abort);
    }
  }
  async request<K extends keyof Endpoints>(
    name: K,
    path: string,
    options: {
      signal?: AbortSignal;
      method?: string;
      body?: unknown;
      key?: string;
      etag?: string;
      view?: string;
      public?: boolean;
    } = {},
  ): Promise<Endpoints[K]> {
    const epoch = this.epoch;
    const controller = new AbortController();
    const abort = () => controller.abort();
    options.signal?.addEventListener("abort", abort, { once: true });
    if (options.signal?.aborted) controller.abort();
    this.controllers.add(controller);
    const headers = new Headers({ Accept: "application/json" });
    if (options.body !== undefined)
      headers.set("Content-Type", "application/json");
    if (options.key) headers.set("Idempotency-Key", options.key);
    if (options.etag)
      headers.set(
        "If-Match",
        options.etag.startsWith('"') ? options.etag : `"${options.etag}"`,
      );
    const send = () => {
      const token = this.auth.token();
      if (!options.public && !token)
        throw new ApiFailure("UNAUTHORIZED", "请重新登录。", 401);
      if (token && !options.public)
        headers.set("Authorization", `Bearer ${token}`);
      return this.fetcher(API_PREFIX + path, {
        method: options.method || "GET",
        headers,
        body:
          options.body === undefined ? undefined : JSON.stringify(options.body),
        signal: controller.signal,
        cache: "no-store",
        redirect: "error",
      });
    };
    try {
      let res = await send();
      if (res.status === 401 && !options.public) {
        this.refreshPromise ??= this.auth.refresh().finally(() => {
          this.refreshPromise = null;
        });
        if ((await this.refreshPromise) && epoch === this.epoch)
          res = await send();
        else this.auth.invalidate();
      }
      if (epoch !== this.epoch || controller.signal.aborted)
        throw new DOMException("Session changed", "AbortError");
      const body = await res.json().catch(() => null);
      if (!res.ok) {
        if (res.status === 401) this.auth.invalidate();
        throw new ApiFailure(
          body?.error?.code || "HTTP_ERROR",
          body?.error?.message || `读取失败（${res.status}），请稍后重试。`,
          res.status,
          body?.error?.request_id,
        );
      }
      const result = validateWire(name, body);
      if (options.view && result.meta.view_id !== options.view)
        throw new ApiFailure(
          "SCOPE_MISMATCH",
          "数据读取范围已变化，请刷新该页面。",
        );
      if (epoch !== this.epoch)
        throw new DOMException("Session changed", "AbortError");
      return result;
    } finally {
      options.signal?.removeEventListener("abort", abort);
      this.controllers.delete(controller);
    }
  }
}
export const queryString = (values: Record<string, string | undefined>) => {
  const params = new URLSearchParams();
  Object.entries(values).forEach(([key, value]) => {
    if (value !== undefined) params.set(key, value);
  });
  return `?${params}`;
};
export const staleMessage = (meta?: Meta) =>
  meta?.freshness === "STALE"
    ? meta.refresh_error?.message || "数据源同步延迟，显示已保存的内容。"
    : null;
