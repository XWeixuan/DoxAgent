import { createContext, useContext, type ReactNode } from "react";
import {
  AlertCircle,
  Circle,
  CircleCheck,
  CirclePause,
  CircleX,
  LoaderCircle,
  OctagonAlert,
  TriangleAlert,
} from "lucide-react";
import type {
  Health,
  RunState,
  InitializationStep,
  Resource,
  Response,
} from "@contract";
import { Badge } from "./ui/badge";
import { Alert, AlertDescription } from "./ui/alert";
import { Empty, EmptyDescription, EmptyHeader, EmptyTitle } from "./ui/empty";
import { Skeleton } from "./ui/skeleton";
import { Button } from "./ui/button";
import { staleMessage } from "@/core/api";
type Tone = "neutral" | "info" | "success" | "warning" | "danger";
export const ModuleFreshness = createContext(true);
const lifecycle: Record<RunState, [string, Tone, typeof Circle]> = {
  INITIALIZING: ["初始化", "info", LoaderCircle],
  RUNNING: ["运行中", "info", Circle],
  PAUSED: ["已暂停", "neutral", CirclePause],
  STOPPED: ["已停止", "neutral", Circle],
};
const health: Record<Health, [string, Tone, typeof Circle]> = {
  NORMAL: ["正常", "success", CircleCheck],
  DEGRADED: ["降级", "warning", TriangleAlert],
  BLOCKED: ["阻塞", "danger", OctagonAlert],
  UNKNOWN: ["健康未知", "neutral", Circle],
};
const step: Record<
  InitializationStep["status"],
  [string, Tone, typeof Circle]
> = {
  PENDING: ["待开始", "neutral", Circle],
  RUNNING: ["进行中", "info", LoaderCircle],
  SUCCEEDED: ["已完成", "success", CircleCheck],
  FAILED: ["失败", "danger", CircleX],
};
export function StatusBadge({
  dimension,
  value,
  title,
}: {
  dimension: "run" | "health" | "step";
  value: string;
  title?: string;
}) {
  const mapping: Record<string, [string, Tone, typeof Circle]> =
    dimension === "run" ? lifecycle : dimension === "health" ? health : step;
  const [label, tone, Icon] = mapping[value] ?? ["未知状态", "neutral", Circle];
  return (
    <Badge
      variant="outline"
      className={`status-badge tone-${tone}`}
      title={title}
    >
      <Icon aria-hidden="true" />
      {label}
    </Badge>
  );
}
export function Notice({
  children,
  danger = false,
}: {
  children: ReactNode;
  danger?: boolean;
}) {
  return (
    <Alert variant={danger ? "destructive" : "default"} className="notice">
      <AlertCircle />
      <AlertDescription>{children}</AlertDescription>
    </Alert>
  );
}
export function Module<T>({
  query,
  children,
  label,
}: {
  query: {
    data?: Response<Resource<T>>;
    isPending: boolean;
    error: Error | null;
    refetch: () => unknown;
  };
  children: (data: T) => ReactNode;
  label: string;
}) {
  const resource = query.data?.data;
  const showFreshness = useContext(ModuleFreshness);
  const warning =
    query.error?.message ??
    (showFreshness ? staleMessage(query.data?.meta) : null);
  if (query.isPending)
    return (
      <div
        className="module-loading"
        role="status"
        aria-label={`正在加载${label}`}
      >
        <Skeleton className="h-5 w-24" />
        <Skeleton className="h-9 w-3/4" />
        <Skeleton className="h-3 w-1/2" />
      </div>
    );
  return (
    <>
      {warning && (
        <Notice danger>
          {warning}{" "}
          <Button variant="link" size="sm" onClick={() => void query.refetch()}>
            重试
          </Button>
        </Notice>
      )}
      {resource?.data != null ? (
        <>
          {children(resource.data)}
          {resource.state === "PARTIAL" && (
            <p className="coverage-note">
              部分数据可用 ·{" "}
              {resource.coverage.reasons.join(" · ") || "统计覆盖不完整"}
            </p>
          )}
        </>
      ) : (
        !warning && (
          <Empty className="module-empty">
            <EmptyHeader>
              <EmptyTitle>
                {
                  {
                    EMPTY: "暂无记录",
                    NOT_PRODUCED: "尚未生成",
                    UNAVAILABLE: "暂不可用",
                    FORBIDDEN: "暂无访问权限",
                    ERROR: "读取失败",
                    AVAILABLE: "暂无记录",
                    PARTIAL: "部分数据暂不可用",
                  }[resource?.state ?? "UNAVAILABLE"]
                }
              </EmptyTitle>
              <EmptyDescription>
                {label}
                {resource?.reason ? ` · ${resource.reason}` : ""}
              </EmptyDescription>
            </EmptyHeader>
          </Empty>
        )
      )}
    </>
  );
}
