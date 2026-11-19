import { useId, useMemo, useState } from "react";
import { Link, useLocation, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  ArrowLeft,
  ChevronDown,
  ExternalLink,
  RefreshCw,
  SlidersHorizontal,
  CheckCircle2,
  CircleAlert,
  CircleDashed,
  ListFilter,
  FileText,
  Network,
  Globe2,
  Layers3,
  Timer,
  Users,
  ArrowDown,
} from "lucide-react";
import type {
  GlobalSourceCard,
  SourceCatalog,
  SourceMethod,
  SourceMethods,
} from "@contract";
import { useRuntime } from "@/core/runtime";
import { ApiFailure } from "@/core/api";
import { formatInstant, valueText } from "@/core/format";
import { Notice } from "@/components/state";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Checkbox } from "@/components/ui/checkbox";
import { Skeleton } from "@/components/ui/skeleton";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import {
  Field,
  FieldGroup,
  FieldLabel,
  FieldLegend,
  FieldSet,
} from "@/components/ui/field";
import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyTitle,
} from "@/components/ui/empty";
import {
  Card,
  CardHeader,
  CardTitle,
  CardDescription,
  CardContent,
  CardFooter,
} from "@/components/ui/card";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import "./source-catalog.css";

export const methodNames: Record<SourceMethod, string> = {
  RSS: "RSS",
  API: "API",
  HTTP: "HTTP",
  PLAYWRIGHT: "Playwright",
  CHROME_CDP: "Chrome/CDP",
  SOURCE_BODY: "随源正文",
  PUBLISHER_ROUTED: "原文站点策略",
  NOT_APPLICABLE: "不适用",
};
const modes = [
  "by_ticker",
  "by_search",
  "by_distribution",
  "massive_distribution",
];
const modeName = (mode: string) => mode.replaceAll("_", " ");
const seconds = (v: number | null) =>
  v == null
    ? "—"
    : `${v.toLocaleString("zh-CN", { maximumFractionDigits: 2 })}s`;
const sampleNote = "各订阅近24h最多20篇；不足5篇取最近5篇后汇总";

function MultiFilter({
  label,
  values,
  options,
  change,
}: {
  label: string;
  values: string[];
  options: { value: string; label: string }[];
  change: (v: string[]) => void;
}) {
  const id = useId();
  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button variant="outline" size="sm">
          {label}
          {values.length > 0 && (
            <span className="catalog-filter-count">{values.length}</span>
          )}
          <ChevronDown data-icon="inline-end" />
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" className="catalog-filter-options">
        <FieldSet>
          <FieldLegend variant="label">{label}</FieldLegend>
          <FieldGroup>
            {options.map((o, i) => (
              <Field orientation="horizontal" key={o.value}>
                <Checkbox
                  id={`${id}-${i}`}
                  checked={values.includes(o.value)}
                  onCheckedChange={(checked) =>
                    change(
                      checked
                        ? [...values, o.value]
                        : values.filter((v) => v !== o.value),
                    )
                  }
                />
                <FieldLabel htmlFor={`${id}-${i}`}>{o.label}</FieldLabel>
              </Field>
            ))}
          </FieldGroup>
        </FieldSet>
        {values.length > 0 && (
          <Button variant="link" size="sm" onClick={() => change([])}>
            清除此项
          </Button>
        )}
      </PopoverContent>
    </Popover>
  );
}

function SingleFilter({
  label,
  value,
  options,
  change,
}: {
  label: string;
  value: string;
  options: [string, string][];
  change: (v: string) => void;
}) {
  return (
    <Select value={value} onValueChange={change}>
      <SelectTrigger size="sm" aria-label={label}>
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        <SelectGroup>
          {options.map(([v, text]) => (
            <SelectItem key={v} value={v}>
              {text}
            </SelectItem>
          ))}
        </SelectGroup>
      </SelectContent>
    </Select>
  );
}

export function catalogMatches(source: GlobalSourceCard, p: URLSearchParams) {
  const c = source.classification,
    scope = p.get("scope"),
    q = (p.get("q") ?? "").trim().toLocaleLowerCase();
  if (scope && scope !== "ALL" && c?.applicability !== scope) return false;
  const sectors = p.getAll("sector"),
    distribution = p.getAll("distribution"),
    methods = p.getAll("method");
  if (
    sectors.length &&
    c?.applicability !== "GLOBAL" &&
    !c?.sector_ids.some((id) => sectors.includes(id))
  )
    return false;
  if (distribution.length && !distribution.includes(source.acquisition_mode))
    return false;
  if (methods.length) {
    const paths = !c
      ? []
      : p.get("stage") === "listing"
        ? [c.listing]
        : p.get("stage") === "body"
          ? [c.body]
          : [c.listing, c.body];
    if (
      !paths.some((path) =>
        [
          ...path.primary,
          ...(p.get("fallback") === "1" ? path.fallback : []),
        ].some((m) => methods.includes(m)),
      )
    )
      return false;
  }
  return (
    !q ||
    [
      source.name,
      source.source_id,
      source.description,
      ...source.entry_points.map((e) => e.url),
    ]
      .join(" ")
      .toLocaleLowerCase()
      .includes(q)
  );
}

export default function SourceCatalogPage() {
  const { api, scope } = useRuntime();
  const query = useQuery({
    queryKey: [scope, "source-catalog"],
    queryFn: ({ signal }) =>
      api.request("SourceCatalog", "/message-bus/source-catalog", { signal }),
    gcTime: 0,
    staleTime: Infinity,
    retry: false,
    refetchInterval: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });
  const data = query.data?.data.data;
  const error = query.error;
  return (
    <CatalogView
      data={data ?? undefined}
      busy={query.isFetching}
      error={
        error
          ? `${error.message}${error instanceof ApiFailure && error.requestId ? ` · 请求编号：${error.requestId}` : ""}`
          : undefined
      }
      refresh={() => void query.refetch()}
    />
  );
}

export function CatalogView({
  data,
  busy,
  error,
  refresh,
}: {
  data?: SourceCatalog;
  busy: boolean;
  error?: string;
  refresh: () => void;
}) {
  const [p, setP] = useSearchParams();
  const location = useLocation();
  const from = (location.state as { from?: string } | null)?.from;
  const set = (key: string, values: string[]) => {
    const next = new URLSearchParams(p);
    next.delete(key);
    values.forEach((v) => next.append(key, v));
    setP(next, { replace: true });
  };
  const groups = useMemo(() => {
    if (!data) return [];
    const selected = p.getAll("sector");
    const visible = data.sources
      .filter((s) => catalogMatches(s, p))
      .sort(
        (a, b) =>
          Number(!a.enabled) - Number(!b.enabled) ||
          Number(a.delivery_mode === "test") -
            Number(b.delivery_mode === "test") ||
          Number(!a.subscriptions.length) - Number(!b.subscriptions.length) ||
          a.name.localeCompare(b.name),
      );
    return [
      { id: "GLOBAL", name: "全局通用消息源" },
      ...data.sectors,
      { id: "UNKNOWN", name: "未标注消息源" },
      { id: "UNUSED", name: "已停用或未使用" },
      { id: "TEST", name: "测试消息源" },
    ]
      .map((sector) => ({
        ...sector,
        sources: visible.filter((s) => {
          const c = s.classification;
          const group =
            s.delivery_mode === "test"
              ? "TEST"
              : !s.enabled || !s.subscriptions.some((b) => b.enabled)
                ? "UNUSED"
                : !c
                  ? "UNKNOWN"
                  : c.applicability === "GLOBAL"
                    ? "GLOBAL"
                    : (c.sector_ids.find((id) => selected.includes(id)) ??
                      c.sector_ids[0]);
          return group === sector.id;
        }),
      }))
      .filter((g) => g.sources.length);
  }, [data, p]);
  return (
    <div className="source-catalog">
      <div className="catalog-heading">
        <div>
          <h1>全部消息源</h1>
          {data && (
            <span className="catalog-count">
              {groups.reduce((n, g) => n + g.sources.length, 0)} /{" "}
              {data.sources.length} 个源
            </span>
          )}
        </div>
        <div className="catalog-heading-actions">
          {data && (
            <time dateTime={data.snapshot_at} title="取数时刻">
              {formatInstant(data.snapshot_at)}
            </time>
          )}
          <Button variant="outline" size="sm" disabled={busy} onClick={refresh}>
            <RefreshCw data-icon="inline-start" />
            {busy ? "读取中…" : "刷新"}
          </Button>
        </div>
      </div>
      {from?.startsWith("/ticker/") && (
        <Link className="catalog-back" to={from}>
          <ArrowLeft aria-hidden="true" />
          返回消息总线
        </Link>
      )}
      <div className="catalog-filters" aria-label="消息源筛选">
        <SlidersHorizontal aria-hidden="true" />
        <Field className="catalog-search">
          <FieldLabel htmlFor="source-search" className="sr-only">
            搜索消息源
          </FieldLabel>
          <Input
            id="source-search"
            placeholder="搜索名称或入口"
            value={p.get("q") ?? ""}
            onChange={(e) => set("q", e.target.value ? [e.target.value] : [])}
          />
        </Field>
        <SingleFilter
          label="抓取阶段"
          value={p.get("stage") ?? "ALL"}
          options={[
            ["ALL", "任一阶段"],
            ["listing", "列表抓取"],
            ["body", "正文补全"],
          ]}
          change={(v) => set("stage", v === "ALL" ? [] : [v])}
        />
        <MultiFilter
          label="抓取方式"
          values={p.getAll("method")}
          options={Object.entries(methodNames).map(([value, label]) => ({
            value,
            label,
          }))}
          change={(v) => set("method", v)}
        />
        <MultiFilter
          label="分发方式"
          values={p.getAll("distribution")}
          options={modes.map((value) => ({ value, label: modeName(value) }))}
          change={(v) => set("distribution", v)}
        />
        <MultiFilter
          label="适用行业"
          values={p.getAll("sector")}
          options={(data?.sectors ?? []).map((s) => ({
            value: s.id,
            label: s.name,
          }))}
          change={(v) => set("sector", v)}
        />
        <SingleFilter
          label="适用属性"
          value={p.get("scope") ?? "ALL"}
          options={[
            ["ALL", "全部适用属性"],
            ["GLOBAL", "全局通用"],
            ["INDUSTRY", "行业专用"],
          ]}
          change={(v) => set("scope", v === "ALL" ? [] : [v])}
        />
        <label className="catalog-fallback">
          <Checkbox
            checked={p.get("fallback") === "1"}
            onCheckedChange={(v) => set("fallback", v ? ["1"] : [])}
          />
          含备用方式
        </label>
        {p.size > 0 && (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setP({}, { replace: true })}
          >
            清除筛选
          </Button>
        )}
      </div>
      {error && (
        <Notice danger>
          消息源读取失败{data ? "，已保留上次结果" : ""}：{error}
        </Notice>
      )}
      {!data && busy && (
        <div className="catalog-grid">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-96" />
          ))}
        </div>
      )}
      {data && groups.length === 0 && (
        <Empty>
          <EmptyHeader>
            <EmptyTitle>没有符合条件的消息源</EmptyTitle>
            <EmptyDescription>调整筛选条件后查看。</EmptyDescription>
          </EmptyHeader>
        </Empty>
      )}
      {groups.map((group) => (
        <section
          className="catalog-section"
          key={group.id}
          aria-labelledby={`section-${group.id}`}
        >
          <h2 id={`section-${group.id}`}>
            {group.name}
            <span>{group.sources.length}</span>
          </h2>
          <div className="catalog-grid">
            {group.sources.map((source) => (
              <DirectoryCard
                key={source.source_id}
                source={source}
                sectors={data!.sectors}
              />
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}

function MethodTags({
  label,
  methods,
}: {
  label: string;
  methods: SourceMethods | undefined;
}) {
  return (
    <section className="catalog-methods" aria-label={label}>
      <h4>
        {label === "列表抓取" ? (
          <ListFilter aria-hidden="true" />
        ) : (
          <FileText aria-hidden="true" />
        )}
        {label}
      </h4>
      {methods ? (
        <>
          <div
            className="catalog-primary-methods"
            title={methods.primary.length > 1 ? "并行主路径" : "主路径"}
          >
            {methods.primary.map((m) => (
              <Badge variant="outline" key={m}>
                {methodNames[m]}
              </Badge>
            ))}
          </div>
          {methods.fallback.length > 0 && (
            <small className="catalog-fallback-methods">
              <ArrowDown aria-hidden="true" />
              备用 {methods.fallback.map((m) => methodNames[m]).join(" → ")}
            </small>
          )}
        </>
      ) : (
        <small>未标注</small>
      )}
    </section>
  );
}

function DirectoryCard({
  source: s,
  sectors,
}: {
  source: GlobalSourceCard;
  sectors: SourceCatalog["sectors"];
}) {
  const [allSubscriptions, setAllSubscriptions] = useState(false);
  const c = s.classification,
    m = s.metrics,
    counts = s.state_counts;
  const pc = s.poll_counts;
  const outcome =
    !pc || !(pc.succeeded + pc.partial + pc.failed)
      ? "暂无记录"
      : pc.partial || (pc.succeeded && (pc.failed || pc.unknown))
        ? "部分成功"
        : pc.succeeded
          ? "成功"
          : "失败";
  const state = !s.enabled
    ? "已停用"
    : !s.subscriptions.length
      ? "未订阅"
      : outcome;
  const stateSummary = [
    ["运行", counts.running],
    ["休眠", counts.sleeping],
    ["异常", counts.abnormal],
    ["禁用", counts.disabled],
    ["未知", counts.unknown],
  ]
    .filter(([, n]) => Number(n) > 0)
    .map(([name, n]) => `${name}${n}`)
    .join(" ");
  const intervals = s.configured_intervals_seconds;
  const intervalText =
    intervals.length > 1
      ? `${intervals[0]}–${intervals.at(-1)}s`
      : intervals.length
        ? `${intervals[0]}s`
        : "—";
  const StatusIcon =
    state === "成功"
      ? CheckCircle2
      : state === "失败" || state === "部分成功"
        ? CircleAlert
        : CircleDashed;
  const industryNames =
    c?.sector_ids.map((id) => sectors.find((v) => v.id === id)?.name ?? id) ??
    [];
  const metrics = [
    [
      "最近成功",
      valueText(m.last_success_at, formatInstant),
      "最近任一订阅成功，不代表全部订阅正常",
    ],
    [
      "当天新增",
      valueText(m.published_today),
      "全部Ticker正式发布条目，同一文章分发多个Ticker分别计数",
    ],
    [
      "平均端到端耗时",
      seconds(m.mean_e2e_seconds),
      `${sampleNote} · ${m.e2e_samples}条消息`,
    ],
    [
      "真实抓取延时",
      seconds(m.mean_capture_seconds),
      `原文发布时间至Bus发布 · ${m.capture_samples}条有效消息`,
    ],
    [
      "P95端到端耗时",
      seconds(m.p95_e2e_seconds),
      `${m.e2e_samples}条消息的P95`,
    ],
    [
      "实际刷新间隔",
      seconds(m.refresh_seconds),
      `各订阅近20逻辑轮次汇总 · ${m.refresh_samples}个间隔`,
    ],
    [
      "正文补全成功率",
      !m.body_applicable
        ? "不适用"
        : m.body_ratio == null
          ? "—"
          : `${(m.body_ratio * 100).toFixed(1)}%`,
      `共享文章去重 · 完整 ${m.body_full} / ${m.body_total}篇`,
    ],
  ];
  const entry = (e: GlobalSourceCard["entry_points"][number], i: number) => (
    <span key={`${e.url}-${i}`}>
      {e.ticker && <b>{e.ticker} </b>}
      {e.url.includes("{") ? (
        <span title={e.url}>{e.url}</span>
      ) : (
        <a href={e.url} target="_blank" rel="noreferrer">
          {e.url}
          <ExternalLink aria-hidden="true" />
        </a>
      )}
    </span>
  );
  return (
    <Card
      className="catalog-card"
      role="article"
      aria-labelledby={`source-${s.source_id}`}
      data-source-id={s.source_id}
    >
      <CardHeader>
        <div className="catalog-card-heading">
          <CardTitle title={s.name}>
            <h3 id={`source-${s.source_id}`}>{s.short_name || s.name}</h3>
          </CardTitle>
          <div
            className="catalog-health"
            data-outcome={state}
            title="最近轮询结果；休眠时保留最后结果，下方汇总各订阅当前状态"
          >
            <strong>
              <StatusIcon aria-hidden="true" />
              {state}
            </strong>
            <span title={`配置目标间隔：${intervals.join(" / ")} 秒`}>
              <Timer aria-hidden="true" />
              目标 {intervalText}
            </span>
            {stateSummary && <small title={stateSummary}>{stateSummary}</small>}
          </div>
        </div>
        <CardDescription title={s.description}>{s.description}</CardDescription>
      </CardHeader>
      <CardContent>
        <div className="catalog-entries">
          {s.entry_points.length === 0 ? (
            <span>入口未标注</span>
          ) : (
            <>
              <div className="catalog-entry-main">
                {s.entry_points[0].url.includes("{") ? (
                  <span title={s.entry_points[0].url}>
                    {s.entry_points[0].label} · 入口模板
                  </span>
                ) : (
                  <a
                    href={s.entry_points[0].url}
                    target="_blank"
                    rel="noreferrer"
                  >
                    {s.entry_points[0].label}
                    <ExternalLink aria-hidden="true" />
                  </a>
                )}
              </div>
              <details>
                <summary>查看 {s.entry_points.length} 个入口</summary>
                <div>{s.entry_points.map(entry)}</div>
              </details>
            </>
          )}
        </div>
        <div className="catalog-attributes">
          <MethodTags label="列表抓取" methods={c?.listing} />
          <MethodTags label="正文补全" methods={c?.body} />
        </div>
        <div className="catalog-subscriptions">
          <h4>
            <Users aria-hidden="true" />
            Ticker 订阅 <span>{s.subscriptions.length}</span>
          </h4>
          {!s.subscriptions.length ? (
            <small>未订阅</small>
          ) : (
            <div>
              {(allSubscriptions
                ? s.subscriptions
                : s.subscriptions.slice(0, 10)
              ).map((b) => (
                <Badge key={b.binding_id} variant="outline" asChild>
                  <Link
                    aria-label={`${b.ticker}${b.enabled ? "" : "（已禁用）"}消息总线`}
                    title={b.enabled ? b.ticker : `${b.ticker} · 已禁用`}
                    to={`/ticker/${encodeURIComponent(b.ticker)}/message-bus`}
                    className={
                      !b.enabled ? "catalog-disabled-ticker" : undefined
                    }
                  >
                    {b.ticker}
                    {!b.enabled && " · 停"}
                  </Link>
                </Badge>
              ))}
              {s.subscriptions.length > 10 && (
                <Button
                  variant="link"
                  size="sm"
                  onClick={() => setAllSubscriptions((v) => !v)}
                >
                  {allSubscriptions
                    ? "收起"
                    : `+${s.subscriptions.length - 10}`}
                </Button>
              )}
            </div>
          )}
        </div>
      </CardContent>
      <CardFooter>
        <dl className="catalog-metrics">
          {metrics.map(([name, value, title]) => (
            <div key={name} title={title}>
              <dt>{name}</dt>
              <dd>{value}</dd>
            </div>
          ))}
        </dl>
        <div className="catalog-classification">
          <div>
            <Network aria-hidden="true" />
            <span>
              <small>分发方式</small>
              <strong>{modeName(s.acquisition_mode)}</strong>
            </span>
          </div>
          <div title={industryNames.join(" / ") || "全局通用"}>
            {c?.applicability === "GLOBAL" ? (
              <Globe2 aria-hidden="true" />
            ) : (
              <Layers3 aria-hidden="true" />
            )}
            <span>
              <small>适用范围</small>
              <strong>
                {c?.applicability === "GLOBAL"
                  ? "GLOBAL · 全局通用"
                  : industryNames.join(" / ") || "未标注"}
              </strong>
            </span>
          </div>
        </div>
        {s.errors.length > 0 && (
          <Alert variant="destructive" className="catalog-errors">
            <CircleAlert aria-hidden="true" />
            <AlertTitle>采集异常 · {s.errors.length} 项</AlertTitle>
            <AlertDescription>
              <details>
                <summary>查看异常详情</summary>
                <ul>
                  {s.errors.map((e) => (
                    <li key={`${e.ticker}-${e.code}`}>
                      <b>{e.ticker}</b> · {e.message}
                      <code>{e.code}</code>
                    </li>
                  ))}
                </ul>
              </details>
            </AlertDescription>
          </Alert>
        )}
      </CardFooter>
    </Card>
  );
}
