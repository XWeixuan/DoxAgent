import { useCallback, useEffect, useMemo, useState } from "react"
import { useParams } from "react-router-dom"
import { ChevronDownIcon, FileCogIcon, LinkIcon } from "lucide-react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import {
  DocumentHistorySheet,
  DocumentStatusPanel,
} from "@/components/dashboard/document-view"
import {
  EmptyState,
  ErrorState,
  FilterSelect,
  LoadMoreButton,
  LoadingGrid,
  MetricCell,
  MetricStrip,
  PageHeader,
  RefreshButton,
  Section,
  StatusBadge,
} from "@/components/dashboard/shared"
import { dashboardApi } from "@/lib/dashboard-api"
import type {
  DashboardEvent,
  DashboardDocument,
  DocumentType,
  DocumentVersion,
  KnownEvent,
  PageResult,
  Policy,
} from "@/lib/dashboard-types"
import { actionTypeLabel, formatDateTime } from "@/lib/format"
import { cn } from "@/lib/utils"
import { downloadDashboardDocumentMarkdown } from "@/lib/document-markdown"
import { getErrorMessage, useDashboardQuery } from "@/hooks/use-dashboard-query"
import { useDashboardEvents } from "@/hooks/use-dashboard-events"

const listLimit = 6

const actionOptions = [
  { value: "all", label: "全部动作" },
  { value: "DTC", label: "DTC" },
  { value: "EBA", label: "EBA" },
  { value: "NULL", label: "NULL" },
  { value: "Irrelevant", label: "Irrelevant" },
]

export function StrategyPage() {
  const ticker = useParams().ticker?.toUpperCase() ?? "MU"
  const [selectedDocument, setSelectedDocument] = useState<DashboardDocument | null>(null)
  const [selectingVersion, setSelectingVersion] = useState(false)
  const [activatingRunId, setActivatingRunId] = useState<string | null>(null)
  const [expectationFilter, setExpectationFilter] = useState("all")
  const [actionFilter, setActionFilter] = useState("all")
  const [knownEventPage, setKnownEventPage] = useState<PageResult<KnownEvent> | null>(null)
  const [policyPage, setPolicyPage] = useState<PageResult<Policy> | null>(null)
  const [loadingMoreKnownEvents, setLoadingMoreKnownEvents] = useState(false)
  const [loadingMorePolicies, setLoadingMorePolicies] = useState(false)
  const [versionsByType, setVersionsByType] = useState<
    Partial<Record<DocumentType, DocumentVersion[]>>
  >({})
  const [versionsError, setVersionsError] = useState<string | null>(null)
  const [versionsLoading, setVersionsLoading] = useState(false)
  const [historyOpen, setHistoryOpen] = useState(false)
  const [downloadingDocument, setDownloadingDocument] = useState(false)

  const documentLoader = useCallback(
    () => dashboardApi.documentsCurrent(ticker, ["document3"]),
    [ticker]
  )
  const reloadVersions = useCallback(async () => {
    setVersionsLoading(true)
    setVersionsError(null)
    try {
      const result = await dashboardApi.documentVersions(ticker, "document3")
      setVersionsByType({ document3: result.items })
    } catch (error) {
      setVersionsError(getErrorMessage(error))
    } finally {
      setVersionsLoading(false)
    }
  }, [ticker])
  const knownEventsLoader = useCallback(
    () =>
      dashboardApi.knownEvents(ticker, {
        limit: listLimit,
        expectation_id: expectationFilter === "all" ? undefined : expectationFilter,
      }),
    [expectationFilter, ticker]
  )
  const policiesLoader = useCallback(
    () =>
      dashboardApi.policies(ticker, {
        limit: listLimit,
        action_type: actionFilter === "all" ? undefined : actionFilter,
      }),
    [actionFilter, ticker]
  )

  const documents = useDashboardQuery(documentLoader)
  const knownEvents = useDashboardQuery(knownEventsLoader)
  const policies = useDashboardQuery(policiesLoader)
  const documentsData = documents.data
  const reloadDocuments = documents.reload
  const reloadKnownEvents = knownEvents.reload
  const reloadPolicies = policies.reload

  useEffect(() => {
    setSelectedDocument(null)
    setExpectationFilter("all")
    setActionFilter("all")
    setVersionsByType({})
    setVersionsError(null)
    setHistoryOpen(false)
  }, [ticker])

  useEffect(() => {
    if (knownEvents.data) {
      setKnownEventPage(knownEvents.data)
    }
  }, [knownEvents.data])

  useEffect(() => {
    if (policies.data) {
      setPolicyPage(policies.data)
    }
  }, [policies.data])

  const document3 = selectedDocument ?? documentsData?.documents[0]
  const latestKnownEventsUpdatedAt = knownEventPage?.items[0]?.updated_at ?? null
  const latestPoliciesUpdatedAt = policyPage?.items[0]?.updated_at ?? null

  const expectationOptions = useMemo(() => {
    const ids = new Set<string>()
    for (const item of knownEventPage?.items ?? []) {
      item.related_expectation_ids.forEach((id) => ids.add(id))
    }
    for (const item of policyPage?.items ?? []) {
      if (item.expectation_id) {
        ids.add(item.expectation_id)
      }
    }
    return [
      { value: "all", label: "全部 expectation" },
      ...Array.from(ids).map((id) => ({ value: id, label: id })),
    ]
  }, [knownEventPage, policyPage])

  const selectVersion = async (_documentType: DocumentType, versionId: string) => {
    setSelectingVersion(true)
    try {
      const detail = await dashboardApi.documentVersionDetail(ticker, "document3", versionId)
      setSelectedDocument(detail.document)
      toast.success("已切换 Document 3 历史版本。")
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
        `Dashboard 手动切换 ${version.document_id} 为现行执行策略文档。`
      )
      setSelectedDocument(null)
      await Promise.all([
        reloadDocuments(),
        reloadVersions(),
        reloadKnownEvents(),
        reloadPolicies(),
      ])
      toast.success("已切换为现行文档。")
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error))
    } finally {
      setActivatingRunId(null)
    }
  }

  const downloadDocument3 = async (document: DashboardDocument) => {
    setDownloadingDocument(true)
    try {
      const detail = await dashboardApi.documentVersionDetail(
        ticker,
        "document3",
        document.document_id
      )
      downloadDashboardDocumentMarkdown(detail.document, { ticker })
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error))
    } finally {
      setDownloadingDocument(false)
    }
  }

  const probeRevision = useCallback(async () => {
    if (!documentsData) {
      return
    }
    try {
      const revision = await dashboardApi.documentRevision(ticker)
      if (
        revision.document_run_id !== documentsData.document_run_id ||
        revision.document3_updated_at !== document3?.updated_at
      ) {
        setSelectedDocument(null)
        await reloadDocuments()
        if (historyOpen) {
          await reloadVersions()
        }
      }
      if (
        revision.known_events_updated_at &&
        revision.known_events_updated_at !== latestKnownEventsUpdatedAt
      ) {
        await reloadKnownEvents()
      }
      if (revision.policies_updated_at && revision.policies_updated_at !== latestPoliciesUpdatedAt) {
        await reloadPolicies()
      }
    } catch {
      // Revision probing is a lightweight missed-event guard; visible errors stay on content APIs.
    }
  }, [
    document3?.updated_at,
    documentsData,
    historyOpen,
    latestKnownEventsUpdatedAt,
    latestPoliciesUpdatedAt,
    reloadDocuments,
    reloadKnownEvents,
    reloadPolicies,
    reloadVersions,
    ticker,
  ])

  const handleEvent = useCallback(
    (event: DashboardEvent) => {
      const eventTicker = event.ticker?.toUpperCase()
      if (eventTicker && eventTicker !== ticker) {
        return
      }
      if (event.event_type === "dashboard.document.updated") {
        if (event.payload.document_type !== "document3") {
          return
        }
        setSelectedDocument(null)
        void reloadDocuments()
        if (historyOpen) {
          void reloadVersions()
        }
        return
      }
      if (event.event_type === "dashboard.known_events.updated") {
        void reloadKnownEvents()
        return
      }
      if (event.event_type === "dashboard.policies.updated") {
        void reloadPolicies()
      }
    },
    [historyOpen, reloadDocuments, reloadKnownEvents, reloadPolicies, reloadVersions, ticker]
  )

  useDashboardEvents({
    ticker,
    eventTypes: [
      "dashboard.document.updated",
      "dashboard.known_events.updated",
      "dashboard.policies.updated",
    ],
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

  const loadMoreKnownEvents = async () => {
    if (!knownEventPage?.page.next_cursor) {
      return
    }
    setLoadingMoreKnownEvents(true)
    try {
      const next = await dashboardApi.knownEvents(ticker, {
        limit: listLimit,
        cursor: knownEventPage.page.next_cursor,
        expectation_id: expectationFilter === "all" ? undefined : expectationFilter,
      })
      setKnownEventPage({
        items: [...knownEventPage.items, ...next.items],
        page: next.page,
      })
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error))
    } finally {
      setLoadingMoreKnownEvents(false)
    }
  }

  const loadMorePolicies = async () => {
    if (!policyPage?.page.next_cursor) {
      return
    }
    setLoadingMorePolicies(true)
    try {
      const next = await dashboardApi.policies(ticker, {
        limit: listLimit,
        cursor: policyPage.page.next_cursor,
        action_type: actionFilter === "all" ? undefined : actionFilter,
      })
      setPolicyPage({
        items: [...policyPage.items, ...next.items],
        page: next.page,
      })
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error))
    } finally {
      setLoadingMorePolicies(false)
    }
  }

  const policyCount = policyPage?.page.total_count ?? policyPage?.items.length ?? 0
  const knownEventCount = knownEventPage?.page.total_count ?? knownEventPage?.items.length ?? 0
  const latestUpdatedAt = useMemo(() => {
    const timestamps = [
      document3?.updated_at,
      ...(knownEventPage?.items.map((item) => item.updated_at) ?? []),
      ...(policyPage?.items.map((item) => item.updated_at) ?? []),
    ]
      .map((value) => (value ? new Date(value).getTime() : Number.NaN))
      .filter((value) => !Number.isNaN(value))

    if (!timestamps.length) {
      return null
    }
    return new Date(Math.max(...timestamps)).toISOString()
  }, [document3, knownEventPage, policyPage])

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title={`${ticker} 执行策略`}
        eyebrow="Runtime Strategy"
        description="查看 Document 3 中参与持久化监测执行的 Known Events 与 Policy。"
        lastUpdatedAt={documents.lastUpdatedAt}
        actions={
          <>
            <DocumentHistorySheet
              title="执行策略历史版本"
              versionsByType={versionsByType}
              loading={versionsLoading || selectingVersion || activatingRunId !== null}
              activatingRunId={activatingRunId}
              onOpenChange={(open) => {
                setHistoryOpen(open)
                if (open) {
                  void reloadVersions()
                }
              }}
              onSelect={selectVersion}
              onActivate={(documentType, version) => void activateVersion(documentType, version)}
            />
            <RefreshButton
              refreshing={
                documents.isRefreshing ||
                versionsLoading ||
                knownEvents.isRefreshing ||
                policies.isRefreshing
              }
              onClick={() => {
                setSelectedDocument(null)
                void documents.reload()
                if (historyOpen) {
                  void reloadVersions()
                }
                void knownEvents.reload()
                void policies.reload()
              }}
            />
          </>
        }
      />

      {[documents.error, versionsError, knownEvents.error, policies.error]
        .filter(Boolean)
        .map((message) => (
          <ErrorState key={message} message={message ?? ""} />
        ))}

      <MetricStrip>
        <MetricCell
          title="Document 3"
          value={document3?.version_status === "current" ? "现行" : "历史"}
          description={document3 ? formatDateTime(document3.updated_at) : "暂无数据"}
          status={document3?.version_status === "current" ? "normal" : "historical"}
          icon={<FileCogIcon />}
        />
        <MetricCell
          title="Known Events"
          value={knownEventCount}
          description="当前分页已加载"
          status="normal"
        />
        <MetricCell
          title="Policy"
          value={policyCount}
          description="当前分页已加载"
          status="normal"
        />
        <MetricCell
          title="最近修改时间"
          value={formatDateTime(latestUpdatedAt)}
          description="Document / Event / Policy"
          status={latestUpdatedAt ? "normal" : "unknown"}
        />
      </MetricStrip>

      <div className="strategy-workspace">
        <div className="flex min-w-0 flex-col gap-6">
          <Section
            title="Known Events"
            description="用于重复事件识别和新旧判定的事件记忆。"
            actions={
              <FilterSelect
                value={expectationFilter}
                placeholder="Expectation Unit"
                options={expectationOptions}
                onChange={setExpectationFilter}
              />
            }
          >
            {knownEvents.isLoading && !knownEventPage ? (
              <LoadingGrid rows={2} />
            ) : knownEventPage && knownEventPage.items.length > 0 ? (
              <div className="flex flex-col gap-3">
                {knownEventPage.items.map((event) => (
                  <ExpandableKnownEvent key={event.event_id} event={event} />
                ))}
                <LoadMoreButton
                  hasMore={knownEventPage.page.has_more}
                  loading={loadingMoreKnownEvents}
                  onClick={() => void loadMoreKnownEvents()}
                />
              </div>
            ) : (
              <EmptyState title="暂无 Known Events" />
            )}
          </Section>

          <Section
            title="Monitoring Execution Policy"
            description="展示 DTC / EBA / NULL / Irrelevant 的监测执行策略。"
            actions={
              <FilterSelect
                value={actionFilter}
                placeholder="动作类型"
                options={actionOptions}
                onChange={setActionFilter}
              />
            }
          >
            {policies.isLoading && !policyPage ? (
              <LoadingGrid rows={2} />
            ) : policyPage && policyPage.items.length > 0 ? (
              <div className="flex flex-col gap-3">
                {policyPage.items.map((policy) => (
                  <ExpandablePolicy key={policy.policy_id} policy={policy} />
                ))}
                <LoadMoreButton
                  hasMore={policyPage.page.has_more}
                  loading={loadingMorePolicies}
                  onClick={() => void loadMorePolicies()}
                />
              </div>
            ) : (
              <EmptyState title="暂无执行策略" />
            )}
          </Section>
        </div>

        {documents.isLoading && !documents.data ? (
          <LoadingGrid rows={1} />
        ) : (
          <DocumentStatusPanel
            document={document3}
            ticker={ticker}
            detailItems={[
              {
                label: "Document 3",
                value: document3?.version_status === "current" ? "现行" : "历史",
              },
              { label: "Known Events", value: knownEventCount },
              { label: "Policy", value: policyCount },
              { label: "生成时间", value: formatDateTime(document3?.generated_at) },
              { label: "最近修改", value: formatDateTime(latestUpdatedAt) },
              {
                label: "文档 ID",
                value: (
                  <span className="inline-block max-w-full break-all font-mono text-xs leading-5">
                    {document3?.document_id ?? "暂无数据"}
                  </span>
                ),
              },
            ]}
            onDownload={(document) => void downloadDocument3(document)}
            downloading={downloadingDocument}
          />
        )}
      </div>
    </div>
  )
}

function ExpandableKnownEvent({ event }: { event: KnownEvent }) {
  const [open, setOpen] = useState(false)
  return (
    <Card>
      <CardHeader>
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <CardTitle className="text-lg font-light">{event.event_name}</CardTitle>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <CardDescription>
                更新：{formatDateTime(event.updated_at)} · 来源：{event.source}
              </CardDescription>
              {event.related_expectation_ids.map((id) => (
                <StatusBadge key={id} status="normal" label={id} />
              ))}
            </div>
          </div>
          <Button variant="ghost" size="icon-sm" onClick={() => setOpen((value) => !value)}>
            <ChevronDownIcon className={cn("transition-transform", open && "rotate-180")} />
            <span className="sr-only">{open ? "收起" : "展开"}</span>
          </Button>
        </div>
      </CardHeader>
      {open ? (
        <CardContent className="flex flex-col gap-3 text-sm">
          <div className="rounded-[4px] border bg-white/55 p-4">
            <div className="font-medium">事件描述</div>
            <p className="mt-1 whitespace-pre-wrap leading-6 text-muted-foreground">
              {event.description || "暂无数据"}
            </p>
          </div>
          <div className="rounded-[4px] border bg-white/55 p-4">
            <div className="font-medium">去重关键字</div>
            <p className="mt-1 text-muted-foreground">
              {event.duplicate_detection_keys.join(" / ") || "暂无数据"}
            </p>
          </div>
        </CardContent>
      ) : null}
    </Card>
  )
}

function ExpandablePolicy({ policy }: { policy: Policy }) {
  const [open, setOpen] = useState(false)
  return (
    <Card>
      <CardHeader>
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <CardTitle className="text-lg font-light">{policy.title}</CardTitle>
            <CardDescription className="mt-1">
              {policy.policy_id} · 更新：{formatDateTime(policy.updated_at)}
            </CardDescription>
          </div>
          <div className="flex items-center gap-2">
            <StatusBadge
              status={policy.action_type === "DTC" ? "normal" : "unknown"}
              label={actionTypeLabel(policy.action_type)}
            />
            <Button variant="ghost" size="icon-sm" onClick={() => setOpen((value) => !value)}>
              <ChevronDownIcon className={cn("transition-transform", open && "rotate-180")} />
              <span className="sr-only">{open ? "收起" : "展开"}</span>
            </Button>
          </div>
        </div>
      </CardHeader>
      {open ? (
        <CardContent className="grid gap-4 text-sm">
          <div>
            <div className="font-medium">触发条件</div>
            <p className="mt-1 whitespace-pre-wrap leading-6 text-muted-foreground">
              {policy.trigger_condition || "暂无数据"}
            </p>
          </div>
          <div className="grid gap-3 md:grid-cols-3">
            <div className="rounded-[4px] border bg-white/55 p-3">
              <div className="font-medium">Expectation Unit</div>
              <p className="mt-1 text-muted-foreground">{policy.expectation_id || "暂无数据"}</p>
            </div>
            <div className="rounded-[4px] border bg-white/55 p-3">
              <div className="font-medium">严重度</div>
              <p className="mt-1 text-muted-foreground">{policy.severity || "暂无数据"}</p>
            </div>
            <div className="rounded-[4px] border bg-white/55 p-3">
              <div className="font-medium">动作类型</div>
              <p className="mt-1 flex items-center gap-2 text-muted-foreground">
                <LinkIcon data-icon="inline-start" />
                {actionTypeLabel(policy.action_type)}
              </p>
            </div>
          </div>
        </CardContent>
      ) : null}
    </Card>
  )
}
