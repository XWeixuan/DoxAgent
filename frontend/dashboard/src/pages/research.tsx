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
  DashboardEvent,
  DashboardDocument,
  DocumentType,
  DocumentVersion,
} from "@/lib/dashboard-types"
import { getErrorMessage, useDashboardQuery } from "@/hooks/use-dashboard-query"
import { useDashboardEvents } from "@/hooks/use-dashboard-events"

export function ResearchPage() {
  const ticker = useParams().ticker?.toUpperCase() ?? "MU"
  const [selectedDocuments, setSelectedDocuments] = useState<
    Partial<Record<DocumentType, DashboardDocument>>
  >({})
  const [selectingVersion, setSelectingVersion] = useState(false)
  const [activatingRunId, setActivatingRunId] = useState<string | null>(null)
  const [versionsByType, setVersionsByType] = useState<
    Partial<Record<DocumentType, DocumentVersion[]>>
  >({})
  const [versionsError, setVersionsError] = useState<string | null>(null)
  const [versionsLoading, setVersionsLoading] = useState(false)
  const [historyOpen, setHistoryOpen] = useState(false)

  const documentsLoader = useCallback(
    () => dashboardApi.documentsCurrent(ticker, ["document1", "document2"]),
    [ticker]
  )
  const reloadVersions = useCallback(async () => {
    setVersionsLoading(true)
    setVersionsError(null)
    try {
      const [document1, document2] = await Promise.all([
        dashboardApi.documentVersions(ticker, "document1"),
        dashboardApi.documentVersions(ticker, "document2"),
      ])
      setVersionsByType({
        document1: document1.items,
        document2: document2.items,
      })
    } catch (error) {
      setVersionsError(getErrorMessage(error))
    } finally {
      setVersionsLoading(false)
    }
  }, [ticker])

  const documents = useDashboardQuery(documentsLoader)
  const documentsData = documents.data
  const reloadDocuments = documents.reload

  useEffect(() => {
    setSelectedDocuments({})
    setVersionsByType({})
    setVersionsError(null)
    setHistoryOpen(false)
  }, [ticker])

  const currentByType = useMemo(() => {
    const entries = documentsData?.documents.map((item) => [item.document_type, item]) ?? []
    return Object.fromEntries(entries) as Partial<Record<DocumentType, DashboardDocument>>
  }, [documentsData])

  const document1 = selectedDocuments.document1 ?? currentByType.document1
  const document2 = selectedDocuments.document2 ?? currentByType.document2

  const probeRevision = useCallback(async () => {
    if (!documentsData) {
      return
    }
    try {
      const revision = await dashboardApi.documentRevision(ticker)
      const changed =
        revision.document_run_id !== documentsData.document_run_id ||
        revision.document1_updated_at !== currentByType.document1?.updated_at ||
        revision.document2_updated_at !== currentByType.document2?.updated_at
      if (changed) {
        setSelectedDocuments({})
        await reloadDocuments()
        if (historyOpen) {
          await reloadVersions()
        }
      }
    } catch {
      // Revision probing is a lightweight missed-event guard; visible errors stay on content APIs.
    }
  }, [
    currentByType.document1?.updated_at,
    currentByType.document2?.updated_at,
    documentsData,
    historyOpen,
    reloadDocuments,
    reloadVersions,
    ticker,
  ])

  const handleEvent = useCallback(
    (event: DashboardEvent) => {
      const eventTicker = event.ticker?.toUpperCase()
      if (eventTicker && eventTicker !== ticker) {
        return
      }
      if (event.event_type !== "dashboard.document.updated") {
        return
      }
      const documentType = event.payload.document_type
      if (documentType !== "document1" && documentType !== "document2") {
        return
      }
      setSelectedDocuments({})
      void reloadDocuments()
      if (historyOpen) {
        void reloadVersions()
      }
    },
    [historyOpen, reloadDocuments, reloadVersions, ticker]
  )

  useDashboardEvents({
    ticker,
    eventTypes: ["dashboard.document.updated"],
    onEvent: handleEvent,
  })

  useEffect(() => {
    const onFocus = () => void probeRevision()
    window.addEventListener("focus", onFocus)
    const timer = window.setInterval(() => {
      void probeRevision()
    }, 300000)
    return () => {
      window.removeEventListener("focus", onFocus)
      window.clearInterval(timer)
    }
  }, [probeRevision])

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

  const activateVersion = async (_documentType: DocumentType, version: DocumentVersion) => {
    setActivatingRunId(version.document_run_id)
    try {
      await dashboardApi.activateDocumentSet(
        ticker,
        version.document_run_id,
        `Dashboard 手动切换 ${version.document_id} 为现行投研文档。`
      )
      setSelectedDocuments({})
      await Promise.all([documents.reload(), reloadVersions()])
      toast.success("已切换为现行文档。")
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error))
    } finally {
      setActivatingRunId(null)
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
              versionsByType={versionsByType}
              loading={versionsLoading || selectingVersion || activatingRunId !== null}
              activatingRunId={activatingRunId}
              onOpenChange={(open) => {
                setHistoryOpen(open)
                if (open) {
                  void reloadVersions()
                }
              }}
              onSelect={(documentType, versionId) => void selectVersion(documentType, versionId)}
              onActivate={(documentType, version) => void activateVersion(documentType, version)}
            />
            <RefreshButton
              refreshing={documents.isRefreshing || versionsLoading}
              onClick={() => {
                setSelectedDocuments({})
                void documents.reload()
                if (historyOpen) {
                  void reloadVersions()
                }
              }}
            />
          </>
        }
      />

      {documents.error ? (
        <ErrorState message={documents.error} onRetry={() => void documents.reload()} />
      ) : null}
      {versionsError ? (
        <ErrorState
          title="历史版本加载失败"
          message={versionsError}
          onRetry={() => void reloadVersions()}
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
            ticker={ticker}
            statusItems={[
              { label: "Document Run", value: documents.data?.document_run_id ?? "暂无数据" },
              { label: "Document 1", value: versionStatusLabel(document1) },
            ]}
          />
          <DocumentSection
            title="Document 2：Expectation Units"
            description="当前监测使用的 expectation units 与失效条件。"
            document={document2}
            ticker={ticker}
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
