import { describe, expect, it } from "vitest"

import { parseSseBlock, queryString } from "@/lib/dashboard-api"

describe("dashboard api helpers", () => {
  it("builds query strings without empty values", () => {
    expect(
      queryString({
        limit: 8,
        cursor: null,
        q: "",
        status: "running",
        enabled: false,
      })
    ).toBe("?limit=8&status=running&enabled=false")
  })

  it("parses dashboard sse data blocks", () => {
    const event = parseSseBlock(
      [
        "id: evt_1",
        "event: runtime.execution.updated",
        'data: {"event_id":"evt_1","event_type":"runtime.execution.updated","ticker":"MU","occurred_at":"2026-06-30T12:00:00Z","payload":{"status":"completed"}}',
      ].join("\n")
    )

    expect(event).toMatchObject({
      event_id: "evt_1",
      event_type: "runtime.execution.updated",
      ticker: "MU",
      payload: { status: "completed" },
    })
  })
})
