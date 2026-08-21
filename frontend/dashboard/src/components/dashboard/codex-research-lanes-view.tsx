import { useCallback } from "react"

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Separator } from "@/components/ui/separator"
import { EmptyState, ErrorState, LoadingGrid, Section, StatusBadge } from "@/components/dashboard/shared"
import { dashboardApi } from "@/lib/dashboard-api"
import type { CodexResearchBundle, CodexResearchLane } from "@/lib/dashboard-types"
import { formatDateTime } from "@/lib/format"
import { useDashboardQuery } from "@/hooks/use-dashboard-query"

const laneMeta: Record<CodexResearchLane, { title: string; description: string }> = {
  global_research: {
    title: "Global Research / Document 1",
    description: "C4 pre-scan → C1/C3 → C5 → C4 enrichment",
  },
  market_situation_research: {
    title: "Market Situation Research",
    description: "独立运行的 C2 宏观研究与 O4 价格研究",
  },
}

export function CodexResearchLanesView({ ticker }: { ticker: string }) {
  return (
    <Section
      title="Codex SDK Research Lanes"
      description="两条研究线独立启动、检查点恢复与发布，不互相读取产物。"
    >
      <div className="grid gap-4 xl:grid-cols-2">
        <LaneCard ticker={ticker} lane="global_research" />
        <LaneCard ticker={ticker} lane="market_situation_research" />
      </div>
    </Section>
  )
}

function LaneCard({ ticker, lane }: { ticker: string; lane: CodexResearchLane }) {
  const loader = useCallback(async () => {
    const runs = await dashboardApi.codexResearchRuns(ticker, lane)
    const latest = runs.items[0]
    return latest ? dashboardApi.codexResearchRun(latest.run_id) : null
  }, [lane, ticker])
  const query = useDashboardQuery(loader)
  const meta = laneMeta[lane]

  if (query.isLoading && query.data === undefined) {
    return <LoadingGrid rows={1} />
  }
  if (query.error) {
    return <ErrorState title={`${meta.title} 加载失败`} message={query.error} onRetry={() => void query.reload()} />
  }
  if (!query.data) {
    return <EmptyState title={`暂无 ${meta.title}`} description={meta.description} />
  }
  return <BundleCard bundle={query.data} />
}

function BundleCard({ bundle }: { bundle: CodexResearchBundle }) {
  const meta = laneMeta[bundle.research_lane]
  const structuredSummary =
    bundle.research_lane === "global_research"
      ? `${bundle.entity_relations.length} 条实体关系 · ${bundle.future_nodes.length} 个未来节点`
      : "不读取 Global Research 上游"
  return (
    <Card>
      <CardHeader>
        <CardTitle>{meta.title}</CardTitle>
        <CardDescription>{meta.description}</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <div className="flex flex-wrap items-center gap-3">
          <StatusBadge status={bundle.status === "published" ? "normal" : "degraded"} label={bundle.status} />
          <span className="font-mono text-xs text-muted-foreground">{bundle.workflow_version}</span>
        </div>
        <div className="text-sm text-muted-foreground">
          <div>{bundle.run_id}</div>
          <div>{formatDateTime(bundle.published_at)}</div>
          <div>{structuredSummary}</div>
        </div>
        <ul className="flex flex-col gap-3">
          {Object.entries(bundle.reports).map(([name, artifact], index) => (
            <li className="flex flex-col gap-1" key={artifact.artifact_id}>
              {index > 0 ? <Separator className="mb-2" /> : null}
              <span className="font-medium">{name}</span>
              <span className="break-all text-xs text-muted-foreground">
                {artifact.relative_path}
              </span>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  )
}
