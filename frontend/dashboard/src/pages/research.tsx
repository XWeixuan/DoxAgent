import { useCallback, useEffect, useMemo, useState } from "react"
import { useParams } from "react-router-dom"
import { toast } from "sonner"

import {
  DocumentHistorySheet,
  DocumentSection,
} from "@/components/dashboard/document-view"
import {
  EmptyState,
  ErrorState,
  LoadingGrid,
  PageHeader,
  RefreshButton,
} from "@/components/dashboard/shared"
import { dashboardApi } from "@/lib/dashboard-api"
import type {
  DashboardDocument,
  DocumentType,
  DocumentVersion,
} from "@/lib/dashboard-types"
import { useDashboardQuery } from "@/hooks/use-dashboard-query"

export function ResearchPage() {
  const ticker = useParams().ticker?.toUpperCase() ?? "MU"
  const [selectedDocuments, setSelectedDocuments] = useState<
    Partial<Record<DocumentType, DashboardDocument>>
  >({})
  const [selectingVersion, setSelectingVersion] = useState(false)

  const documentsLoader = useCallback(
    () => dashboardApi.documentsCurrent(ticker, ["document1", "document2"]),
    [ticker]
  )
  const versionsLoader = useCallback(async () => {
    const [document1, document2] = await Promise.all([
      dashboardApi.documentVersions(ticker, "document1"),
      dashboardApi.documentVersions(ticker, "document2"),
    ])
    return {
      document1: document1.items,
      document2: document2.items,
    } satisfies Partial<Record<DocumentType, DocumentVersion[]>>
  }, [ticker])

  const documents = useDashboardQuery(documentsLoader, { intervalMs: 60000 })
  const versions = useDashboardQuery(versionsLoader, { intervalMs: 60000 })

  useEffect(() => {
    setSelectedDocuments({})
  }, [ticker])

  const currentByType = useMemo(() => {
    const entries = documents.data?.documents.map((item) => [item.document_type, item]) ?? []
    return Object.fromEntries(entries) as Partial<Record<DocumentType, DashboardDocument>>
  }, [documents.data])

  const document1 = selectedDocuments.document1 ?? currentByType.document1
  const document2 = selectedDocuments.document2 ?? currentByType.document2

  const selectVersion = async (documentType: DocumentType, versionId: string) => {
    setSelectingVersion(true)
    try {
      const detail = await dashboardApi.documentVersionDetail(ticker, documentType, versionId)
      setSelectedDocuments((value) => ({ ...value, [documentType]: detail.document }))
      toast.success("已切换历史版本。")
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error))
    } finally {
      setSelectingVersion(false)
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title={`${ticker} 投研资料`}
        eyebrow="Research Workspace"
        description="查看当前 belief state 中的 Document 1 / Document 2，并支持历史版本切换。"
        lastUpdatedAt={documents.lastUpdatedAt}
        actions={
          <>
            <DocumentHistorySheet
              title="投研资料历史版本"
              versionsByType={versions.data ?? {}}
              loading={selectingVersion}
              onSelect={(documentType, versionId) => void selectVersion(documentType, versionId)}
            />
            <RefreshButton
              refreshing={documents.isRefreshing || versions.isRefreshing}
              onClick={() => {
                setSelectedDocuments({})
                void documents.reload()
                void versions.reload()
              }}
            />
          </>
        }
      />

      {documents.error ? (
        <ErrorState message={documents.error} onRetry={() => void documents.reload()} />
      ) : null}
      {versions.error ? (
        <ErrorState
          title="历史版本加载失败"
          message={versions.error}
          onRetry={() => void versions.reload()}
        />
      ) : null}

      {documents.isLoading && !documents.data ? (
        <LoadingGrid rows={4} />
      ) : document1 || document2 ? (
        <>
          <DocumentSection
            title="Document 1：Global Research"
            description="全局研究资料、基本面与市场观点。"
            document={document1}
            statusItems={[
              { label: "Document Run", value: documents.data?.document_run_id ?? "暂无数据" },
              { label: "Document 1", value: versionStatusLabel(document1) },
            ]}
          />
          <DocumentSection
            title="Document 2：Expectation Units"
            description="当前监测使用的 expectation units 与失效条件。"
            document={document2}
            statusItems={[
              { label: "Document Run", value: documents.data?.document_run_id ?? "暂无数据" },
              { label: "Document 2", value: versionStatusLabel(document2) },
            ]}
          />
        </>
      ) : (
        <EmptyState title="暂无投研资料" description="Mock API 当前没有返回 Document 1 或 Document 2。" />
      )}
    </div>
  )
}

function versionStatusLabel(document?: DashboardDocument) {
  if (!document) {
    return "暂无数据"
  }
  return document.version_status === "current" ? "现行" : "历史"
}
