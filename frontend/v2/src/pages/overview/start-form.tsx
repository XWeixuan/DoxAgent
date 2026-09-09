import { useEffect, useState, type FormEvent } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowRight, LoaderCircle, Plus } from "lucide-react";
import type { MonitorMode, StartTickerRequest } from "@contract";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Field,
  FieldGroup,
  FieldLabel,
  FieldDescription,
  FieldSet,
  FieldLegend,
} from "@/components/ui/field";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Notice } from "@/components/state";
import { operationsFor } from "@/core/operations";
import { useRuntime } from "@/core/runtime";
import { useOverview } from "./data";
import { OperationNotice } from "./operation-notice";
import { modes } from "./labels";

export function StartForm({
  model,
}: {
  model: ReturnType<typeof useOverview>;
}) {
  const runtime = useRuntime();
  const [ticker, setTicker] = useState("");
  const normalizedTicker = ticker.trim().toUpperCase();
  const [capabilityTicker, setCapabilityTicker] = useState("");
  useEffect(() => {
    const timer = setTimeout(
      () =>
        setCapabilityTicker(
          /^[A-Z0-9][A-Z0-9.-]{0,19}$/.test(normalizedTicker)
            ? normalizedTicker
            : "",
        ),
      250,
    );
    return () => clearTimeout(timer);
  }, [normalizedTicker]);
  const tickerCapabilities = useQuery({
    queryKey: [runtime.scope, "start-capabilities", capabilityTicker],
    enabled: !!capabilityTicker && !!model.principal.data?.data.can_operate,
    queryFn: ({ signal }) =>
      runtime.api.request(
        "Capabilities",
        "/capabilities?ticker=" + encodeURIComponent(capabilityTicker),
        { signal },
      ),
    staleTime: 10_000,
  });
  const [mode, setMode] = useState<MonitorMode>("MESSAGE_MONITORING");
  const [initialization, setInitialization] =
    useState<StartTickerRequest["initialization"]>("FORCE_INITIALIZE");
  const [error, setError] = useState("");
  const [submitted, setSubmitted] = useState("");
  const pending = model.operations.data[submitted];
  const busy =
    pending &&
    ["submitting", "tracking", "unknown", "waiting"].includes(pending.phase);
  const capabilityQuery = capabilityTicker
    ? tickerCapabilities
    : model.capabilities;
  const capabilities =
    normalizedTicker === capabilityTicker
      ? capabilityQuery.data?.data
      : undefined;
  const available = {
    MESSAGE_MONITORING:
      capabilities?.monitoring ?? model.capabilities.data?.data.monitoring,
    PAPER_TRADING: capabilities?.paper_trading,
    LIVE_TRADING: capabilities?.live_trading,
  };
  const enabled =
    !!model.principal.data?.data.can_operate && available[mode]?.available;
  function submit(event: FormEvent) {
    event.preventDefault();
    const normalized = ticker.trim().toUpperCase();
    if (!/^[A-Z0-9][A-Z0-9.-]{0,19}$/.test(normalized)) {
      setError("请输入有效的 Ticker，例如 MU。");
      return;
    }
    if (!enabled) {
      setError("当前运行模式暂不可用，请检查该标的的执行配置。");
      return;
    }
    setError("");
    setTicker(normalized);
    setSubmitted(normalized);
    void operationsFor(runtime).submit({
      ticker: normalized,
      kind: "START",
      body: { ticker: normalized, monitor_mode: mode, initialization },
    });
  }
  return (
    <section className="start-panel">
      <div className="panel-title">
        <div className="panel-icon">
          <Plus aria-hidden="true" />
        </div>
        <div>
          <h2>启动新标的监控</h2>
        </div>
      </div>
      <form onSubmit={submit}>
        <FieldGroup>
          <Field data-invalid={!!error}>
            <FieldLabel htmlFor="ticker">Ticker</FieldLabel>
            <Input
              id="ticker"
              placeholder="例如 MU"
              value={ticker}
              onChange={(e) => {
                setTicker(e.target.value);
                setError("");
              }}
              autoComplete="off"
              spellCheck={false}
              maxLength={20}
              disabled={!!busy}
              aria-invalid={!!error}
              aria-describedby={error ? "ticker-error" : undefined}
            />
            {error && (
              <FieldDescription id="ticker-error">{error}</FieldDescription>
            )}
          </Field>
          <FieldSet disabled={!!busy}>
            <FieldLegend variant="label">运行模式</FieldLegend>
            <RadioGroup
              value={mode}
              onValueChange={(value) => setMode(value as MonitorMode)}
              aria-label="运行模式"
              className="mode-options"
            >
              {(Object.entries(modes) as [MonitorMode, string][]).map(
                ([value, label]) => (
                  <Field
                    key={value}
                    orientation="horizontal"
                    className="mode-option"
                    data-selected={mode === value}
                    title={available[value]?.message ?? undefined}
                  >
                    <RadioGroupItem
                      id={`mode-${value}`}
                      value={value}
                      disabled={!available[value]?.available}
                    />
                    <FieldLabel htmlFor={`mode-${value}`}>{label}</FieldLabel>
                  </Field>
                ),
              )}
            </RadioGroup>
          </FieldSet>
          <FieldSet disabled={!!busy}>
            <FieldLegend variant="label">文档初始化方式</FieldLegend>
            <RadioGroup
              value={initialization}
              onValueChange={(value) =>
                setInitialization(value as StartTickerRequest["initialization"])
              }
              aria-label="文档初始化方式"
            >
              {(
                [
                  ["REUSE_ACTIVE", "复用当前文档"],
                  ["FORCE_INITIALIZE", "强制初始化"],
                ] as const
              ).map(([value, label]) => (
                <Field
                  key={value}
                  orientation="horizontal"
                  className="init-option"
                >
                  <RadioGroupItem id={value} value={value} />
                  <FieldLabel htmlFor={value}>{label}</FieldLabel>
                </Field>
              ))}
            </RadioGroup>
          </FieldSet>
          <Button
            type="submit"
            disabled={!enabled || !!busy}
            className="start-button"
          >
            {busy ? (
              <LoaderCircle className="spin" data-icon="inline-start" />
            ) : (
              <Plus data-icon="inline-start" />
            )}
            {busy ? "正在处理" : "启动监控"}
            {!busy && <ArrowRight data-icon="inline-end" />}
          </Button>
        </FieldGroup>
      </form>
      {pending && <OperationNotice entry={pending} />}{" "}
      {!enabled && model.principal.isSuccess && capabilityQuery.isSuccess && (
        <p className="form-note">
          {!model.principal.data?.data.can_operate
            ? "当前账户暂无该操作权限。"
            : available[mode]?.message ||
              (available[mode]?.reason === "CONTROL_WORKER_UNAVAILABLE"
                ? "启动服务未就绪。"
                : "当前运行模式暂不可用。")}
        </p>
      )}
      {model.principal.error && (
        <Notice danger>
          操作权限读取失败。
          <Button variant="link" onClick={() => void model.principal.refetch()}>
            重试权限读取
          </Button>
        </Notice>
      )}
      {capabilityQuery.error && (
        <Notice danger>
          启动能力读取失败。
          <Button variant="link" onClick={() => void capabilityQuery.refetch()}>
            重试
          </Button>
        </Notice>
      )}
    </section>
  );
}
