import { useRef, useState } from "react";
import Ajv from "ajv";
import { toast } from "sonner";
import type {
  SourceStatus,
  AvailableSource,
  BindingConfig,
  BindingEditable,
  JsonObject,
  AvailableSourceDetail,
} from "@contract";
import { usePages } from "@/core/paged-query";
import { useRead, tickerPath, id } from "@/core/page-query";
import { useRuntime } from "@/core/runtime";
import { queryString } from "@/core/api";
import { Module, Notice } from "@/components/state";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Field, FieldGroup, FieldLabel } from "@/components/ui/field";
import { Choices } from "@/components/page-kit";
import { formatInstant, valueText } from "@/core/format";
export default function BindingSettings({
  ticker,
  view,
  refresh,
}: {
  ticker: string;
  view: string;
  refresh: () => Promise<unknown>;
}) {
  const [selected, setSelected] = useState<string>(),
    [available, setAvailable] = useState<string>();
  const sources = usePages<SourceStatus, "Sources">(
    "Sources",
    tickerPath(ticker) +
      "/message-bus/sources" +
      queryString({ view_id: view, limit: "20" }),
    (r) => r.data,
    view,
  );
  const options = usePages<AvailableSource, "AvailableSources">(
    "AvailableSources",
    tickerPath(ticker) + "/available-api-sources?limit=20",
    (r) => r.data,
  );
  return (
    <>
      <Module query={sources} label="消息渠道">
        {(d) => {
          const current =
            d.items.find((s) => s.source.binding_id === selected) ?? d.items[0];
          return current ? (
            <div className="binding-workspace">
              <nav className="binding-channels" aria-label="消息渠道配置">
                {d.items.map((s) => (
                  <button
                    key={s.source.binding_id}
                    aria-pressed={s === current}
                    onClick={() => setSelected(s.source.binding_id)}
                  >
                    <strong>{s.source.name}</strong>
                    <span>
                      {s.source.kind === "api" ? "API" : "爬虫"} ·{" "}
                      {s.target_interval_seconds}s / 次
                    </span>
                    <small>{s.enabled ? "已启用" : "已停用"}</small>
                  </button>
                ))}
              </nav>
              <section className="binding-config-panel">
                <header>
                  <div>
                    <h2>{current.source.name}</h2>
                    <p>
                      {current.source.source_id} ·{" "}
                      {current.source.kind === "api" ? "API" : "爬虫"} ·{" "}
                      {current.publication_mode === "immediate"
                        ? "单条发布"
                        : "分批发布"}
                    </p>
                  </div>
                  <span
                    className={
                      "poll-state " + current.health_group.toLowerCase()
                    }
                  >
                    {
                      {
                        succeeded: "正常",
                        partial: "部分成功",
                        failed: "失败",
                        disabled: "停用",
                        never_polled: "尚未轮询",
                      }[current.poll_status]
                    }
                  </span>
                </header>
                <dl className="binding-facts">
                  <div>
                    <dt>最近成功</dt>
                    <dd>{valueText(current.last_success_at, formatInstant)}</dd>
                  </div>
                  <div>
                    <dt>轮询间隔</dt>
                    <dd>{current.target_interval_seconds}s</dd>
                  </div>
                  <div>
                    <dt>最近延迟</dt>
                    <dd>
                      {valueText(
                        current.last_poll_latency_seconds,
                        (v) => v.toFixed(2) + "s",
                      )}
                    </dd>
                  </div>
                  <div>
                    <dt>最近新增</dt>
                    <dd>{valueText(current.last_published_count)}</dd>
                  </div>
                </dl>
                {current.current_error && (
                  <Notice danger>{current.current_error.message}</Notice>
                )}
                <ExistingBinding
                  key={current.source.binding_id}
                  ticker={ticker}
                  binding={current.source.binding_id}
                  refreshed={refresh}
                />
              </section>
            </div>
          ) : (
            <Notice>没有已绑定消息源</Notice>
          );
        }}
      </Module>
      {sources.hasNextPage && (
        <Button variant="outline" onClick={() => void sources.fetchNextPage()}>
          更多渠道
        </Button>
      )}
      <Module query={options} label="可用 API">
        {(d) =>
          d.items.length ? (
            <>
              <h2 className="settings-heading">可绑定 API</h2>
              <div className="binding-grid">
                {d.items.map((s) => (
                  <section className="binding-card" key={s.source_id}>
                    <h3>{s.name}</h3>
                    <p>{s.source_id} · API</p>
                    <Button
                      variant="outline"
                      onClick={() =>
                        setAvailable(
                          available === s.source_id ? undefined : s.source_id,
                        )
                      }
                    >
                      启用并配置
                    </Button>
                    {available === s.source_id && (
                      <NewBinding
                        ticker={ticker}
                        source={s.source_id}
                        done={() => {
                          setAvailable(undefined);
                          void options.refetch();
                          void sources.refetch();
                        }}
                      />
                    )}
                  </section>
                ))}
              </div>
            </>
          ) : null
        }
      </Module>
      {options.hasNextPage && (
        <Button onClick={() => void options.fetchNextPage()}>更多 API</Button>
      )}
    </>
  );
}
function ExistingBinding({
  ticker,
  binding,
  refreshed,
}: {
  ticker: string;
  binding: string;
  refreshed: () => Promise<unknown>;
}) {
  const q = useRead("Binding", tickerPath(ticker) + `/bindings/${id(binding)}`);
  return (
    <Module query={q} label="Binding 配置">
      {(d) => (
        <Editor
          key={d.control_etag}
          ticker={ticker}
          config={d}
          done={() => {
            void q.refetch();
            void refreshed();
          }}
        />
      )}
    </Module>
  );
}
function NewBinding({
  ticker,
  source,
  done,
}: {
  ticker: string;
  source: string;
  done: () => void;
}) {
  const q = useRead(
    "AvailableSource",
    tickerPath(ticker) + `/available-api-sources/${id(source)}`,
  );
  return (
    <Module query={q} label="API 默认配置">
      {(d) => <Editor ticker={ticker} candidate={d} done={done} />}
    </Module>
  );
}
const parameterLabels: Record<string, string> = {
  search_terms: "搜索关键词",
  usernames: "用户名",
  rss_urls: "RSS 地址",
};
function Editor({
  ticker,
  config,
  candidate,
  done,
}: {
  ticker: string;
  config?: BindingConfig;
  candidate?: AvailableSourceDetail;
  done: () => void;
}) {
  const { api, query, scope } = useRuntime();
  const original = config?.effective ?? candidate!.defaults;
  const [enabled, setEnabled] = useState(original.enabled),
    [interval, setInterval] = useState(
      String(original.polling.target_interval_seconds),
    ),
    [text, setText] = useState(
      JSON.stringify(original.source_parameters, null, 2),
    ),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [saved, setSaved] = useState(false);
  const schema = config?.parameter_schema ?? candidate!.parameter_schema;
  const hasParameters =
    Object.keys((schema.properties ?? {}) as object).length > 0 ||
    Object.keys(original.source_parameters).length > 0 ||
    schema.additionalProperties === true;
  const known = [
    ...new Set([
      ...Object.keys(original.source_parameters),
      ...Object.keys((schema.properties ?? {}) as object),
    ]),
  ].filter((k) => parameterLabels[k]);
  const [mode, setMode] = useState(
    config?.source.kind === "crawler" || !known.length ? "JSON" : "FORM",
  );
  const retry = useRef<{ signature: string; key: string } | undefined>(
    undefined,
  );
  const save = async () => {
    setError("");
    setSaved(false);
    try {
      const parameters = JSON.parse(text) as JsonObject;
      if (
        !parameters ||
        typeof parameters !== "object" ||
        Array.isArray(parameters)
      )
        throw new Error("参数必须为 JSON 对象");
      const validate = new Ajv({ strict: false }).compile(schema);
      if (!validate(parameters))
        throw new Error(
          "参数校验失败：" +
            (validate.errors ?? [])
              .map((e) => e.instancePath + " " + e.message)
              .join("；"),
        );
      const seconds = Number(interval);
      if (!Number.isInteger(seconds) || seconds < 1)
        throw new Error("轮询间隔必须为正整数秒");
      const effective: BindingEditable = {
        ...original,
        enabled,
        source_parameters: parameters,
        polling: { ...original.polling, target_interval_seconds: seconds },
      };
      const body = config
        ? {
            enabled,
            source_parameters: parameters,
            polling: { target_interval_seconds: seconds },
          }
        : {
            source_id: candidate!.source_id,
            source_version: candidate!.source_version,
            configuration: effective,
          };
      const signature = JSON.stringify(body);
      if (retry.current?.signature !== signature)
        retry.current = { signature, key: crypto.randomUUID() };
      setBusy(true);
      await api.request(
        "BindingWrite",
        tickerPath(ticker) +
          (config ? `/bindings/${id(config.binding_id)}` : "/bindings"),
        {
          method: config ? "PATCH" : "POST",
          body,
          key: retry.current.key,
          etag: config?.control_etag,
        },
      );
      retry.current = undefined;
      setSaved(true);
      toast.success("配置已保存");
      await query.invalidateQueries({
        predicate: (q) =>
          q.queryKey[0] === scope &&
          ["Sources", "BusStatus", "AvailableSources", "Binding"].includes(
            String(q.queryKey[2]),
          ),
      });
      done();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const changeParameter = (key: string, value: string) => {
    try {
      const v = JSON.parse(text) as JsonObject;
      v[key] = value
        .split("\n")
        .map((x) => x.trim())
        .filter(Boolean);
      setText(JSON.stringify(v, null, 2));
      setError("");
    } catch {
      setError("请先修复 JSON 格式再切换表单");
    }
  };
  let parameters: JsonObject = {};
  try {
    parameters = JSON.parse(text);
  } catch {
    /* Invalid draft stays editable. */
  }
  return (
    <form
      className="binding-editor"
      onSubmit={(e) => {
        e.preventDefault();
        void save();
      }}
    >
      <FieldGroup>
        <h3>运行设置</h3>
        <Field orientation="horizontal">
          <input
            id={"enable-" + (config?.binding_id ?? candidate!.source_id)}
            type="checkbox"
            checked={enabled}
            onChange={(e) => setEnabled(e.target.checked)}
          />
          <FieldLabel
            htmlFor={"enable-" + (config?.binding_id ?? candidate!.source_id)}
          >
            启用消息源
          </FieldLabel>
        </Field>
        <Field>
          <FieldLabel>轮询间隔（秒）</FieldLabel>
          <Input
            aria-label="轮询间隔（秒）"
            type="number"
            min="1"
            step="1"
            value={interval}
            onChange={(e) => setInterval(e.target.value)}
          />
        </Field>
        <div className="binding-schedule">
          <span>运行时段</span>
          <div>
            {original.polling.active_windows.length
              ? original.polling.active_windows.map((w, i) => (
                  <p key={i}>
                    {w.timezone} ·{" "}
                    {w.weekdays
                      .map(
                        (n) =>
                          [
                            "周一",
                            "周二",
                            "周三",
                            "周四",
                            "周五",
                            "周六",
                            "周日",
                          ][n],
                      )
                      .join("、")}{" "}
                    · {w.start_time}–{w.end_time}
                  </p>
                ))
              : "全天"}
          </div>
        </div>
        <div className="binding-parameter-heading">
          <h3>参数配置</h3>
        </div>
        {known.length > 0 && config?.source.kind !== "crawler" && (
          <Choices
            value={mode}
            onChange={setMode}
            items={[
              ["FORM", "表单"],
              ["JSON", "JSON"],
            ]}
            label="参数编辑方式"
          />
        )}
        {!hasParameters ? (
          <p>无需额外参数</p>
        ) : mode === "FORM" ? (
          known.map((key) => (
            <Field key={key}>
              <FieldLabel>{parameterLabels[key]}</FieldLabel>
              <textarea
                aria-label={parameterLabels[key]}
                rows={4}
                value={
                  Array.isArray(parameters[key])
                    ? (parameters[key] as string[]).join("\n")
                    : ""
                }
                onChange={(e) => changeParameter(key, e.target.value)}
              />
            </Field>
          ))
        ) : (
          <Field>
            <FieldLabel>消息源参数</FieldLabel>
            <textarea
              aria-label="Source parameters JSON"
              spellCheck={false}
              rows={8}
              value={text}
              onChange={(e) => setText(e.target.value)}
            />
          </Field>
        )}
        {config?.redacted_parameter_paths.length ? (
          <Notice>受保护参数保持服务端原值。</Notice>
        ) : null}
        {error && <Notice danger>{error}</Notice>}
        {saved && <p role="status">已保存</p>}
        <Button type="submit" disabled={busy}>
          {busy ? "保存中" : "保存配置"}
        </Button>
      </FieldGroup>
    </form>
  );
}
