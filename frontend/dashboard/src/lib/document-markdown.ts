import type { DashboardDocument } from "@/lib/dashboard-types"
import { formatDateTime, renderJsonValue } from "@/lib/format"

export function buildDashboardDocumentMarkdown(
  document: DashboardDocument,
  { ticker }: {
    ticker?: string
  } = {}
) {
  const lines: string[] = []
  const statusLabel = document.version_status === "current" ? "现行版本" : "历史版本"
  lines.push(`# ${[ticker, document.document_type_label].filter(Boolean).join(" ")}`)
  lines.push("")
  lines.push(`- 版本状态：${statusLabel}`)
  lines.push(`- 文档 ID：${document.document_id}`)
  lines.push(`- 生成时间：${formatDateTime(document.generated_at)}`)
  lines.push(`- 更新时间：${formatDateTime(document.updated_at)}`)
  lines.push(`- 可用性：${document.availability}`)
  lines.push(`- 卡片数量：${document.cards.length}`)
  lines.push("")

  for (const [index, card] of document.cards.entries()) {
    lines.push(`## ${index + 1}. ${card.title}`)
    lines.push("")
    lines.push(`- 更新时间：${formatDateTime(card.updated_at)}`)
    if (card.summary) {
      lines.push("")
      lines.push(card.summary)
    }
    if (card.fields.length > 0) {
      lines.push("")
    }
    for (const field of card.fields) {
      lines.push(`### ${field.label}`)
      lines.push("")
      lines.push(renderJsonValue(field.value))
      lines.push("")
    }
  }

  return `${lines.join("\n").replace(/\n{3,}/g, "\n\n").trim()}\n`
}

export function dashboardDocumentMarkdownFilename(
  document: DashboardDocument,
  { ticker }: {
    ticker?: string
  } = {}
) {
  const stamp = new Date().toISOString().replace(/[:.]/g, "-")
  const tickerPart = sanitizeFilenamePart(ticker || "ticker")
  const typePart = sanitizeFilenamePart(document.document_type)
  const statusPart = document.version_status === "current" ? "current" : "history"
  return `${tickerPart}-${typePart}-${statusPart}-${stamp}.md`
}

export function downloadDashboardDocumentMarkdown(
  document: DashboardDocument,
  { ticker }: {
    ticker?: string
  } = {}
) {
  const markdown = buildDashboardDocumentMarkdown(document, { ticker })
  const blob = new Blob([markdown], { type: "text/markdown;charset=utf-8" })
  const url = URL.createObjectURL(blob)
  const anchor = globalThis.document.createElement("a")
  anchor.href = url
  anchor.download = dashboardDocumentMarkdownFilename(document, { ticker })
  globalThis.document.body.append(anchor)
  anchor.click()
  anchor.remove()
  URL.revokeObjectURL(url)
}

function sanitizeFilenamePart(value: string) {
  return value.trim().replace(/[^a-zA-Z0-9_-]+/g, "_").replace(/^_+|_+$/g, "") || "document"
}
