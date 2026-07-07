import { describe, expect, it } from "vitest"

import { buildDashboardDocumentMarkdown } from "@/lib/document-markdown"
import type { DashboardDocument } from "@/lib/dashboard-types"

describe("document markdown export", () => {
  it("renders the same dashboard document display model used by the UI", () => {
    const document: DashboardDocument = {
      document_type: "document1",
      document_type_label: "Document 1: Global Research",
      document_id: "doc_display",
      generated_at: "2026-05-29T04:00:00Z",
      updated_at: "2026-05-29T05:00:00Z",
      version_status: "current",
      availability: "available",
      cards: [
        {
          card_id: "fundamental_report",
          title: "Fundamental Research",
          updated_at: "2026-05-29T05:00:00Z",
          summary: "Displayed card summary.",
          fields: [
            {
              key: "text",
              label: "Research Text",
              value: "Displayed field text.",
            },
          ],
        },
      ],
      raw: {
        internal_only: "must not be exported",
      },
    }

    const markdown = buildDashboardDocumentMarkdown(document, { ticker: "NVDA" })

    expect(markdown).toContain("# NVDA Document 1: Global Research")
    expect(markdown).toContain("Displayed card summary.")
    expect(markdown).toContain("### Research Text")
    expect(markdown).toContain("Displayed field text.")
    expect(markdown).not.toContain("internal_only")
    expect(markdown).not.toContain("must not be exported")
  })
})
