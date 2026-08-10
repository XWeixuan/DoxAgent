import { useCallback } from "react"

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { EmptyState, ErrorState, LoadingGrid, Section, StatusBadge } from "@/components/dashboard/shared"
import { dashboardApi } from "@/lib/dashboard-api"
import type { CodexDocument1Bundle } from "@/lib/dashboard-types"
import { formatDateTime } from "@/lib/format"
import { useDashboardQuery } from "@/hooks/use-dashboard-query"

export function CodexDocument1View({ ticker }: { ticker: string }) {
  const loader = useCallback(async () => {
    const runs = await dashboardApi.codexDocument1Runs(ticker)
    const latest = runs.items[0]
    return latest ? dashboardApi.codexDocument1Run(latest.run_id) : null
  }, [ticker])
  const query = useDashboardQuery(loader)

  if (query.isLoading && query.data === undefined) {
    return <LoadingGrid rows={2} />
  }
  if (query.error) {
    return <ErrorState title="Codex Document 1 v2 加载失败" message={query.error} onRetry={() => void query.reload()} />
  }
  if (!query.data) {
    return <EmptyState title="暂无 Codex Document 1 v2" description="v2 API 尚未返回已发布运行；旧版 Document 1 不受影响。" />
  }
  return <Bundle bundle={query.data} />
}

function Bundle({ bundle }: { bundle: CodexDocument1Bundle }) {
  return (
    <Section
      title="Document 1 v2 · Codex SDK"
      description={`独立工作流 ${bundle.run_id} · ${formatDateTime(bundle.published_at)}`}
    >
      <div className="flex flex-col gap-4">
        <div className="flex flex-wrap items-center gap-3">
          <StatusBadge status={bundle.status === "published" ? "normal" : "degraded"} label={bundle.status} />
          <span className="font-mono text-xs text-muted-foreground">{bundle.workflow_version}</span>
        </div>

        <Card>
          <CardHeader><CardTitle>实体关系</CardTitle></CardHeader>
          <CardContent className="overflow-x-auto">
            {bundle.entity_relations.length ? (
              <table className="w-full min-w-[760px] text-left text-sm">
                <thead><tr className="border-b text-muted-foreground"><th>主体</th><th>对象</th><th>类型</th><th>说明</th><th>业务或产品</th></tr></thead>
                <tbody>{bundle.entity_relations.map((row, index) => (
                  <tr className="border-b align-top" key={`${row.关系主体}-${row.关系对象}-${index}`}>
                    <td className="py-3 pr-4">{row.关系主体}</td><td className="py-3 pr-4">{row.关系对象}</td><td className="py-3 pr-4">{row.关系类型}</td><td className="py-3 pr-4">{row.关系说明}</td><td className="py-3">{row.关联业务或产品}</td>
                  </tr>
                ))}</tbody>
              </table>
            ) : <EmptyState title="暂无实体关系" />}
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle>未来节点</CardTitle></CardHeader>
          <CardContent className="overflow-x-auto">
            {bundle.future_nodes.length ? (
              <table className="w-full min-w-[760px] text-left text-sm">
                <thead><tr className="border-b text-muted-foreground"><th>时间</th><th>事项</th><th>与目标公司的关系</th><th>来源</th><th>发布日期</th></tr></thead>
                <tbody>{bundle.future_nodes.map((row, index) => (
                  <tr className="border-b align-top" key={`${row.时间}-${row.未来事项}-${index}`}>
                    <td className="py-3 pr-4">{row.时间}</td><td className="py-3 pr-4">{row.未来事项}</td><td className="py-3 pr-4">{row.与目标公司的关系}</td><td className="py-3 pr-4">{row.来源}</td><td className="py-3">{row.来源发布日期}</td>
                  </tr>
                ))}</tbody>
              </table>
            ) : <EmptyState title="暂无未来节点" />}
          </CardContent>
        </Card>

        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {Object.entries(bundle.reports).map(([name, artifact]) => (
            <Card key={artifact.artifact_id}>
              <CardHeader><CardTitle className="text-base">{name}</CardTitle></CardHeader>
              <CardContent className="space-y-2 text-xs text-muted-foreground">
                <div>{artifact.relative_path}</div><div className="font-mono break-all">{artifact.sha256}</div>
              </CardContent>
            </Card>
          ))}
        </div>
      </div>
    </Section>
  )
}
