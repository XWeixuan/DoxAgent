import { useState } from "react"
import type { ReactNode } from "react"
import {
  ChevronDownIcon,
  Clock3Icon,
  DownloadIcon,
  FileClockIcon,
  HistoryIcon,
  Layers3Icon,
  RefreshCwIcon,
} from "lucide-react"

import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { ScrollArea } from "@/components/ui/scroll-area"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet"
import type {
  DashboardDocument,
  DocumentType,
  DocumentVersion,
} from "@/lib/dashboard-types"
import { downloadDashboardDocumentMarkdown } from "@/lib/document-markdown"
import { formatDateTime, renderJsonValue } from "@/lib/format"
import { cn } from "@/lib/utils"
import {
  EmptyState,
  KeyValueList,
  Section,
  StatusBadge,
} from "@/components/dashboard/shared"

const documentTypeLabels: Record<DocumentType, string> = {
  document1: "Document 1：Global Research",
  document2: "Document 2：Expectation Units",
  document3: "Document 3：Runtime Strategy",
}

export function DocumentSection({
  document,
  title,
  description,
  statusItems,
  ticker,
}: {
  document?: DashboardDocument
  title: string
  description?: string
  statusItems?: Array<{ label: string; value: ReactNode }>
  ticker?: string
}) {
  if (!document) {
    return (
      <Section title={title} description={description}>
        <EmptyState title="暂无文档" description="Mock API 当前没有返回该 document。" />
      </Section>
    )
  }

  return (
    <Section title={title} description={description}>
      <div className="document-workspace">
        <div className="flex min-w-0 flex-col gap-4">
          {document.cards.length > 0 ? (
            document.cards.map((card, index) => (
              <DocumentContentCard key={card.card_id} card={card} index={index + 1} />
            ))
          ) : (
            <EmptyState title="暂无内容卡片" description="该版本没有可展示的卡片字段。" />
          )}
        </div>

        <DocumentStatusPanel document={document} statusItems={statusItems} ticker={ticker} />
      </div>
    </Section>
  )
}

export function DocumentStatusPanel({
  document,
  statusItems = [],
  detailItems,
  className,
  ticker,
  onDownload,
  downloading,
}: {
  document?: DashboardDocument | null
  statusItems?: Array<{ label: string; value: ReactNode }>
  detailItems?: Array<{ label: string; value: ReactNode }>
  className?: string
  ticker?: string
  onDownload?: (document: DashboardDocument) => void
  downloading?: boolean
}) {
  if (!document) {
    return (
      <aside className={cn("document-rail glass-slab p-4", className)}>
        <div className="text-xs font-medium uppercase tracking-[0.2em] text-primary">
          文档状态
        </div>
        <div className="mt-4">
          <EmptyState title="暂无文档状态" />
        </div>
      </aside>
    )
  }

  return (
    <aside className={cn("document-rail glass-slab p-4", className)}>
      <div className="flex flex-col gap-4">
        <div>
          <div className="text-xs font-medium uppercase tracking-[0.2em] text-primary">
            文档状态
          </div>
          <h3 className="mt-2 text-lg font-light">{document.document_type_label}</h3>
        </div>
        <StatusBadge
          status={document.version_status === "current" ? "normal" : "historical"}
          label={document.version_status === "current" ? "现行版本" : "历史版本"}
        />
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={downloading}
          onClick={() => {
            if (onDownload) {
              onDownload(document)
              return
            }
            downloadDashboardDocumentMarkdown(document, { ticker })
          }}
        >
          <DownloadIcon data-icon="inline-start" />
          {downloading ? "准备下载" : "下载 Markdown"}
        </Button>
        <KeyValueList items={detailItems ?? [
          ...statusItems,
          {
            label: "文档 ID",
            value: (
              <span className="inline-block max-w-full break-all font-mono text-xs leading-5">
                {document.document_id}
              </span>
            ),
          },
          { label: "生成时间", value: formatDateTime(document.generated_at) },
          { label: "更新时间", value: formatDateTime(document.updated_at) },
          { label: "可用性", value: document.availability },
          { label: "卡片数量", value: document.cards.length },
        ]} />
      </div>
    </aside>
  )
}

function DocumentContentCard({
  card,
  index,
}: {
  card: DashboardDocument["cards"][number]
  index: number
}) {
  const [open, setOpen] = useState(true)

  return (
    <Card>
      <CardHeader>
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="mb-2 flex items-center gap-2 text-xs text-muted-foreground">
              <Layers3Icon data-icon="inline-start" />
              #{index.toString().padStart(2, "0")}
              <span>·</span>
              <Clock3Icon data-icon="inline-start" />
              {formatDateTime(card.updated_at)}
            </div>
            <CardTitle className="text-xl font-light">{card.title}</CardTitle>
            {card.summary ? (
              <CardDescription className="mt-2 text-sm leading-6">{card.summary}</CardDescription>
            ) : null}
          </div>
          <Button variant="ghost" size="icon-sm" onClick={() => setOpen((value) => !value)}>
            <ChevronDownIcon
              className={cn("transition-transform", open && "rotate-180")}
            />
            <span className="sr-only">{open ? "收起" : "展开"}</span>
          </Button>
        </div>
      </CardHeader>
      {open ? (
        <CardContent className="flex flex-col gap-3">
          {card.fields.length > 0 ? (
            card.fields.map((field) => (
              <DocumentFieldBlock key={field.key} field={field} />
            ))
          ) : (
            <p className="text-sm text-muted-foreground">暂无字段数据</p>
          )}
        </CardContent>
      ) : null}
    </Card>
  )
}

function DocumentFieldBlock({
  field,
}: {
  field: DashboardDocument["cards"][number]["fields"][number]
}) {
  const textValue = typeof field.value === "string" ? field.value : null
  const renderedValue = textValue ?? renderJsonValue(field.value)
  const longContent =
    renderedValue.length > 900 || renderedValue.split(/\r?\n/).length > 12
  const [open, setOpen] = useState(!longContent)

  return (
    <div className="rounded-[4px] border bg-white/55 p-4">
      <div className="mb-2 flex items-center justify-between gap-3">
        <div className="text-sm font-medium">{field.label}</div>
        {longContent ? (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="h-7 px-2 text-xs"
            onClick={() => setOpen((value) => !value)}
          >
            {open ? "收起" : "展开"}
          </Button>
        ) : null}
      </div>
      <div className={cn("relative", longContent && !open && "max-h-72 overflow-hidden")}>
        {textValue !== null ? (
          <MarkdownContent text={textValue} />
        ) : (
          <pre className="whitespace-pre-wrap break-words font-sans text-sm leading-6 text-muted-foreground">
            {renderedValue}
          </pre>
        )}
        {longContent && !open ? (
          <div className="pointer-events-none absolute inset-x-0 bottom-0 h-16 bg-gradient-to-b from-white/0 to-white/90" />
        ) : null}
      </div>
    </div>
  )
}

function MarkdownContent({ text }: { text: string }) {
  const blocks = markdownBlocks(text)
  if (blocks.length === 0) {
    return <p className="text-sm text-muted-foreground">暂无数据</p>
  }
  return <div className="markdown-content">{blocks}</div>
}

function markdownBlocks(text: string): ReactNode[] {
  const nodes: ReactNode[] = []
  const lines = text.trim().split(/\r?\n/)
  let listItems: ReactNode[] = []
  let listKind: "ul" | "ol" = "ul"

  const flushList = () => {
    if (!listItems.length) {
      return
    }
    const ListTag = listKind
    nodes.push(
      <ListTag key={`list-${nodes.length}`} className="my-2 ml-5 list-outside space-y-1 text-sm leading-6 text-muted-foreground">
        {listItems}
      </ListTag>
    )
    listItems = []
  }

  lines.forEach((rawLine, index) => {
    const line = rawLine.trim()
    if (!line) {
      flushList()
      return
    }

    const heading = /^(#{1,6})\s+(.+)$/.exec(line)
    if (heading) {
      flushList()
      const level = Math.min(heading[1].length, 4)
      const className =
        level === 1
          ? "mt-2 text-xl font-medium text-foreground"
          : level === 2
            ? "mt-4 text-lg font-medium text-foreground"
            : "mt-3 text-base font-medium text-foreground"
      if (level === 1) {
        nodes.push(
          <h1 key={`heading-${index}`} className={className}>
            {markdownInline(heading[2])}
          </h1>
        )
      } else if (level === 2) {
        nodes.push(
          <h2 key={`heading-${index}`} className={className}>
            {markdownInline(heading[2])}
          </h2>
        )
      } else {
        nodes.push(
          <h3 key={`heading-${index}`} className={className}>
            {markdownInline(heading[2])}
          </h3>
        )
      }
      return
    }

    const unordered = /^[-*]\s+(.+)$/.exec(line)
    const ordered = /^\d+[.)]\s+(.+)$/.exec(line)
    if (unordered || ordered) {
      const nextKind = ordered ? "ol" : "ul"
      if (listItems.length && listKind !== nextKind) {
        flushList()
      }
      listKind = nextKind
      listItems.push(
        <li key={`li-${index}`} className={nextKind === "ol" ? "list-decimal" : "list-disc"}>
          {markdownInline((unordered ?? ordered)?.[1] ?? line)}
        </li>
      )
      return
    }

    flushList()
    nodes.push(
      <p key={`p-${index}`} className="my-2 whitespace-pre-wrap break-words text-sm leading-6 text-muted-foreground">
        {markdownInline(line)}
      </p>
    )
  })
  flushList()
  return nodes
}

function markdownInline(text: string): ReactNode[] {
  return text.split(/(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\([^)]+\))/g).map((part, index) => {
    if (!part) {
      return null
    }
    if (part.startsWith("**") && part.endsWith("**")) {
      return <strong key={index} className="font-semibold text-foreground">{part.slice(2, -2)}</strong>
    }
    if (part.startsWith("`") && part.endsWith("`")) {
      return <code key={index} className="rounded-[3px] bg-muted px-1 py-0.5 font-mono text-xs text-foreground">{part.slice(1, -1)}</code>
    }
    const link = /^\[([^\]]+)\]\(([^)]+)\)$/.exec(part)
    if (link && /^https?:\/\//i.test(link[2])) {
      return (
        <a key={index} href={link[2]} target="_blank" rel="noreferrer" className="text-primary underline-offset-4 hover:underline">
          {link[1]}
        </a>
      )
    }
    return part
  })
}

export function DocumentHistorySheet({
  title,
  versionsByType,
  onSelect,
  onActivate,
  loading,
  activatingRunId,
  onOpenChange,
}: {
  title: string
  versionsByType: Partial<Record<DocumentType, DocumentVersion[]>>
  onSelect: (documentType: DocumentType, versionId: string) => void
  onActivate?: (documentType: DocumentType, version: DocumentVersion) => void
  loading?: boolean
  activatingRunId?: string | null
  onOpenChange?: (open: boolean) => void
}) {
  const groups = Object.entries(versionsByType) as Array<[DocumentType, DocumentVersion[]]>
  return (
    <Sheet onOpenChange={onOpenChange}>
      <SheetTrigger asChild>
        <Button variant="outline">
          <HistoryIcon data-icon="inline-start" />
          历史版本
        </Button>
      </SheetTrigger>
      <SheetContent side="left" className="flex w-full flex-col sm:max-w-md">
        <SheetHeader>
          <SheetTitle>{title}</SheetTitle>
          <SheetDescription>选择历史版本后，当前页面会切换为对应内容。</SheetDescription>
        </SheetHeader>
        <ScrollArea className="min-h-0 flex-1 px-4 pb-4">
          <div className="flex flex-col gap-5">
            {groups.length > 0 ? (
              groups.map(([documentType, versions]) => (
                <div key={documentType} className="flex flex-col gap-2">
                  <h3 className="flex items-center gap-2 text-sm font-medium">
                    <FileClockIcon data-icon="inline-start" />
                    {documentTypeLabels[documentType] ?? documentType}
                  </h3>
                  {versions.length > 0 ? (
                    versions.map((version) => (
                      <div
                        key={version.version_id}
                        className="flex flex-col gap-2 rounded-[4px] border bg-white/70 p-3 text-left transition-colors hover:bg-accent"
                      >
                        <button
                          type="button"
                          className="flex min-w-0 flex-col gap-2 text-left"
                          disabled={loading}
                          onClick={() => onSelect(documentType, version.version_id)}
                        >
                          <span className="max-w-full break-all text-sm font-medium">
                            {version.document_id}
                          </span>
                          {version.summary ? (
                            <span className="max-w-full break-words text-xs leading-5 text-muted-foreground">
                              {version.summary}
                            </span>
                          ) : null}
                          <span className="text-xs text-muted-foreground">
                            生成：{formatDateTime(version.generated_at)}
                          </span>
                          <span className="max-w-full break-all font-mono text-xs text-muted-foreground">
                            {version.version_id}
                          </span>
                          <span className="text-xs leading-5 text-muted-foreground">
                            原因：{documentReasonLabel(version.reason_label)}
                            {version.reason_text ? ` · ${version.reason_text}` : ""}
                          </span>
                          {version.updated_by_label ? (
                            <span className="text-xs text-muted-foreground">
                              来源：{version.updated_by_label}
                            </span>
                          ) : null}
                        </button>
                        <div className="flex items-center justify-between gap-2">
                          <StatusBadge
                            status={version.version_status === "current" ? "normal" : "historical"}
                            label={version.version_status === "current" ? "现行" : "历史"}
                          />
                          {version.version_status !== "current" && onActivate ? (
                            <Button
                              type="button"
                              variant="outline"
                              size="sm"
                              disabled={loading || activatingRunId === version.document_run_id}
                              onClick={() => onActivate(documentType, version)}
                            >
                              <RefreshCwIcon data-icon="inline-start" />
                              {activatingRunId === version.document_run_id
                                ? "切换中"
                                : "切换为现行文档"}
                            </Button>
                          ) : null}
                        </div>
                      </div>
                    ))
                  ) : (
                    <p className="text-sm text-muted-foreground">暂无历史版本</p>
                  )}
                </div>
              ))
            ) : (
              <EmptyState title="暂无历史版本" />
            )}
          </div>
        </ScrollArea>
      </SheetContent>
    </Sheet>
  )
}

function documentReasonLabel(reason?: DocumentVersion["reason_label"]) {
  const labels: Record<NonNullable<DocumentVersion["reason_label"]>, string> = {
    workflow_generated: "工作流生成",
    agent_refreshed: "Agent 刷新",
    manual_activated: "人工激活",
    monitoring_policy_reviewed: "监测策略复核",
    unknown: "原因未记录",
  }
  return reason ? labels[reason] : labels.unknown
}
