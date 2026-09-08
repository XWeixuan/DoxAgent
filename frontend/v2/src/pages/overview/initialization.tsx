import { useEffect, useState } from "react";
import { Check, Layers3, RotateCcw } from "lucide-react";
import type { InitializationProgress, InitializationStep } from "@contract";
import { Button } from "@/components/ui/button";
import { StatusBadge } from "@/components/state";
import { duration } from "@/core/format";
import { operationsFor, type PendingCommand } from "@/core/operations";
import { useRuntime } from "@/core/runtime";
import { OperationNotice } from "./operation-notice";

const stepNames = {
  RESEARCH: "基础投研",
  EVENT_LIBRARY: "事件库初始化",
  EXPECTATIONS: "预期研究",
  POLICIES: "Policy 初始化",
  SOURCES_ACTIVATION: "消息源准备与激活",
  START_RUNTIME: "启动运行",
};
function Elapsed({ step }: { step: InitializationStep }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (step.status !== "RUNNING") return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [step.status]);
  if (step.first_started_at.state !== "AVAILABLE") return null;
  const seconds =
    step.status === "RUNNING"
      ? Math.max(0, (now - Date.parse(step.first_started_at.value)) / 1000)
      : step.duration_seconds.state === "AVAILABLE"
        ? step.duration_seconds.value
        : null;
  return (
    <span className="step-time">
      {seconds === null ? "耗时未记录" : duration(seconds)}
    </span>
  );
}
export function Initialization({
  progress,
  ticker,
  busy,
  canOperate,
  pending,
}: {
  progress: InitializationProgress | null;
  ticker: string;
  busy: boolean;
  canOperate: boolean;
  pending?: PendingCommand;
}) {
  const operations = operationsFor(useRuntime());
  if (!progress)
    return (
      <div className="initialization">
        <p className="muted">初始化进度尚未取得，使用页面刷新查看。</p>
      </div>
    );
  if (progress.status === "SUCCEEDED") return null;
  return (
    <div
      className={`initialization ${progress.status === "FAILED" ? "initialization-failed" : ""}`}
      aria-label={`${ticker} 初始化进度`}
    >
      <div className="init-heading">
        <div>
          <Layers3 aria-hidden="true" />
          <strong>初始化进度</strong>
        </div>
        {progress.status === "FAILED" && progress.manual_resume_allowed && (
          <Button
            size="sm"
            variant="outline"
            disabled={busy || !canOperate}
            onClick={() =>
              void operations.submit({
                ticker,
                kind: "RESUME_INITIALIZATION",
                progress,
              })
            }
          >
            <RotateCcw data-icon="inline-start" />
            重试失败步骤
          </Button>
        )}
      </div>
      <ol className="steps">
        {progress.steps.map((step, i) => (
          <li key={step.step_key} data-state={step.status}>
            <div className="step-top">
              <span className="step-number">
                {step.status === "SUCCEEDED" ? (
                  <Check aria-hidden="true" />
                ) : (
                  String(i + 1).padStart(2, "0")
                )}
              </span>
              <Elapsed step={step} />
            </div>
            <h4>{stepNames[step.step_key]}</h4>
            <StatusBadge dimension="step" value={step.status} />
            {step.quality_annotations.length > 0 && (
              <span
                className="quality-note"
                title={step.quality_annotations.join(" · ")}
              >
                质量注解
              </span>
            )}
          </li>
        ))}
      </ol>
      {progress.failure && (
        <div className="init-error">{progress.failure.message}</div>
      )}
      {pending && pending.phase !== "done" && (
        <OperationNotice entry={pending} />
      )}
    </div>
  );
}
