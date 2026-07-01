import { useState } from "react"
import type { ReactNode } from "react"
import {
  ChevronDownIcon,
  Clock3Icon,
  FileClockIcon,
  HistoryIcon,
  Layers3Icon,
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
}: {
  document?: DashboardDocument
  title: string
  description?: string
  statusItems?: Array<{ label: string; value: ReactNode }>
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

        <DocumentStatusPanel document={document} statusItems={statusItems} />
      </div>
    </Section>
  )
}

export function DocumentStatusPanel({
  document,
  statusItems = [],
  detailItems,
  className,
}: {
  document?: DashboardDocument | null
  statusItems?: Array<{ label: string; value: ReactNode }>
  detailItems?: Array<{ label: string; value: ReactNode }>
  className?: string
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
        <KeyValueList items={detailItems ?? [
          ...statusItems,
          { label: "文档 ID", value: <span className="font-mono text-xs">{document.document_id}</span> },
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
              <div key={field.key} className="rounded-[4px] border bg-white/55 p-4">
                <div className="mb-2 text-sm font-medium">{field.label}</div>
                <pre className="whitespace-pre-wrap break-words font-sans text-sm leading-6 text-muted-foreground">
                  {renderJsonValue(field.value)}
                </pre>
              </div>
            ))
          ) : (
            <p className="text-sm text-muted-foreground">暂无字段数据</p>
          )}
        </CardContent>
      ) : null}
    </Card>
  )
}

export function DocumentHistorySheet({
  title,
  versionsByType,
  onSelect,
  loading,
}: {
  title: string
  versionsByType: Partial<Record<DocumentType, DocumentVersion[]>>
  onSelect: (documentType: DocumentType, versionId: string) => void
  loading?: boolean
}) {
  const groups = Object.entries(versionsByType) as Array<[DocumentType, DocumentVersion[]]>
  return (
    <Sheet>
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
                      <button
                        key={version.version_id}
                        type="button"
                        className="flex flex-col gap-2 rounded-[4px] border bg-white/70 p-3 text-left transition-colors hover:bg-accent"
                        disabled={loading}
                        onClick={() => onSelect(documentType, version.version_id)}
                      >
                        <span className="text-sm font-medium">{version.document_id}</span>
                        {version.summary ? (
                          <span className="text-xs leading-5 text-muted-foreground">
                            {version.summary}
                          </span>
                        ) : null}
                        <span className="text-xs text-muted-foreground">
                          生成：{formatDateTime(version.generated_at)}
                        </span>
                        <span className="font-mono text-xs text-muted-foreground">
                          {version.version_id}
                        </span>
                        <StatusBadge
                          status={version.version_status === "current" ? "normal" : "historical"}
                          label={version.version_status === "current" ? "现行" : "历史"}
                        />
                      </button>
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
