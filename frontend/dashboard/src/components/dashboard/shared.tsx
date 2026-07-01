import type { ReactNode } from "react"
import {
  AlertCircleIcon,
  CircleDashedIcon,
  DatabaseIcon,
  Loader2Icon,
  RefreshCwIcon,
} from "lucide-react"

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty"
import { Button } from "@/components/ui/button"
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { cn } from "@/lib/utils"
import { formatDateTime, statusTone } from "@/lib/format"

export function StatusBadge({
  status,
  label,
  className,
}: {
  status?: string | null
  label?: string
  className?: string
}) {
  const tone = statusTone(status)
  return (
    <Badge
      className={cn("status-badge gap-1.5 rounded-[4px] font-medium", `status-${tone}`, className)}
      variant="outline"
    >
      <span className={cn("status-dot", `status-${tone}`)} />
      {label ?? status ?? "暂无数据"}
    </Badge>
  )
}

export function MetricStrip({
  children,
  className,
}: {
  children: ReactNode
  className?: string
}) {
  return <div className={cn("metric-strip", className)}>{children}</div>
}

export function MetricCell({
  title,
  value,
  icon,
}: {
  title: string
  value: ReactNode
  description?: ReactNode
  status?: string | null
  icon?: ReactNode
}) {
  return (
    <div className="metric-cell flex items-start justify-between gap-3">
      <div className="min-w-0">
        <div className="text-xs font-medium text-muted-foreground">{title}</div>
        <div className="mt-1 truncate text-2xl font-light text-foreground">{value}</div>
      </div>
      {icon ? <div className="text-muted-foreground">{icon}</div> : null}
    </div>
  )
}

export function KpiCard({
  title,
  value,
  icon,
}: {
  title: string
  value: ReactNode
  description?: ReactNode
  status?: string | null
  icon?: ReactNode
}) {
  return (
    <Card className="min-h-24 overflow-hidden">
      <CardHeader>
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <CardDescription>{title}</CardDescription>
            <CardTitle className="truncate text-2xl font-light">{value}</CardTitle>
          </div>
          {icon ? <div className="text-muted-foreground">{icon}</div> : null}
        </div>
      </CardHeader>
    </Card>
  )
}

export function LoadingGrid({ rows = 6 }: { rows?: number }) {
  return (
    <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
      {Array.from({ length: rows }).map((_, index) => (
        <Card key={index}>
          <CardHeader>
            <Skeleton className="h-4 w-28" />
            <Skeleton className="h-7 w-40" />
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            <Skeleton className="h-4 w-full" />
            <Skeleton className="h-4 w-3/4" />
          </CardContent>
        </Card>
      ))}
    </div>
  )
}

export function ErrorState({
  title = "加载失败",
  message,
  onRetry,
}: {
  title?: string
  message: string
  onRetry?: () => void
}) {
  return (
    <Alert variant="destructive">
      <AlertCircleIcon />
      <AlertTitle>{title}</AlertTitle>
      <AlertDescription className="flex flex-col gap-3">
        <span>{message}</span>
        {onRetry ? (
          <Button variant="outline" size="sm" onClick={onRetry}>
            <RefreshCwIcon data-icon="inline-start" />
            重试
          </Button>
        ) : null}
      </AlertDescription>
    </Alert>
  )
}

export function EmptyState({
  title,
  description,
  action,
}: {
  title: string
  description?: string
  action?: ReactNode
}) {
  return (
    <Empty className="rounded-[4px] border bg-white/60 py-10">
      <EmptyHeader>
        <EmptyMedia variant="icon">
          <DatabaseIcon />
        </EmptyMedia>
        <EmptyTitle>{title}</EmptyTitle>
        {description ? <EmptyDescription>{description}</EmptyDescription> : null}
      </EmptyHeader>
      {action ? <EmptyContent>{action}</EmptyContent> : null}
    </Empty>
  )
}

export function RefreshButton({
  refreshing,
  onClick,
}: {
  refreshing?: boolean
  onClick: () => void
}) {
  return (
    <Button variant="outline" size="sm" onClick={onClick} disabled={refreshing}>
      {refreshing ? (
        <Loader2Icon className="animate-spin" data-icon="inline-start" />
      ) : (
        <RefreshCwIcon data-icon="inline-start" />
      )}
      刷新
    </Button>
  )
}

export function LoadMoreButton({
  hasMore,
  loading,
  onClick,
}: {
  hasMore?: boolean
  loading?: boolean
  onClick: () => void
}) {
  if (!hasMore) {
    return null
  }
  return (
    <div className="flex justify-center py-2">
      <Button variant="outline" onClick={onClick} disabled={loading}>
        {loading ? (
          <Loader2Icon className="animate-spin" data-icon="inline-start" />
        ) : (
          <CircleDashedIcon data-icon="inline-start" />
        )}
        加载更多
      </Button>
    </div>
  )
}

export function FilterSelect({
  value,
  placeholder,
  options,
  onChange,
  className,
}: {
  value: string
  placeholder: string
  options: Array<{ value: string; label: string }>
  onChange: (value: string) => void
  className?: string
}) {
  return (
    <Select value={value} onValueChange={onChange}>
      <SelectTrigger className={cn("w-full sm:w-44", className)}>
        <SelectValue placeholder={placeholder} />
      </SelectTrigger>
      <SelectContent>
        <SelectGroup>
          {options.map((option) => (
            <SelectItem key={option.value} value={option.value}>
              {option.label}
            </SelectItem>
          ))}
        </SelectGroup>
      </SelectContent>
    </Select>
  )
}

export function PageHeader({
  title,
  actions,
  lastUpdatedAt,
}: {
  title: string
  description?: string
  eyebrow?: string
  actions?: ReactNode
  lastUpdatedAt?: Date | null
}) {
  return (
    <div className="flex flex-col gap-2 border-b pb-3 md:flex-row md:items-center md:justify-between">
      <h1 className="text-3xl font-light md:text-4xl">{title}</h1>
      <div className="flex flex-wrap items-center gap-2">
        {lastUpdatedAt ? (
          <span className="text-xs text-muted-foreground">
            最近刷新：{formatDateTime(lastUpdatedAt.toISOString())}
          </span>
        ) : null}
        {actions}
      </div>
    </div>
  )
}

export function Section({
  title,
  children,
  actions,
  className,
}: {
  title: string
  description?: string
  children: ReactNode
  actions?: ReactNode
  className?: string
}) {
  return (
    <section className={cn("flex flex-col gap-3", className)}>
      <div className="flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
        <h2 className="text-xl font-light">{title}</h2>
        {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
      </div>
      {children}
    </section>
  )
}

export function KeyValueList({
  items,
  className,
}: {
  items: Array<{ label: string; value: ReactNode }>
  className?: string
}) {
  return (
    <div className={cn("grid gap-2 text-sm", className)}>
      {items.map((item) => (
        <div key={item.label} className="flex justify-between gap-3 border-b py-2 last:border-b-0">
          <span className="text-muted-foreground">{item.label}</span>
          <span className="min-w-0 text-right">{item.value}</span>
        </div>
      ))}
    </div>
  )
}
