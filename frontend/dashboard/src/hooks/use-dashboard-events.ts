import { useEffect, useMemo, useState } from "react"

import { connectDashboardEvents } from "@/lib/dashboard-api"
import type { DashboardEvent } from "@/lib/dashboard-types"

export type EventConnectionState = "connecting" | "open" | "error" | "closed"

export function useDashboardEvents({
  ticker,
  eventTypes,
  enabled = true,
  onEvent,
}: {
  ticker?: string
  eventTypes?: string[]
  enabled?: boolean
  onEvent: (event: DashboardEvent) => void
}) {
  const [state, setState] = useState<EventConnectionState>("closed")
  const [lastEvent, setLastEvent] = useState<DashboardEvent | null>(null)
  const [error, setError] = useState<string | null>(null)
  const eventTypesKey = useMemo(() => eventTypes?.join(",") ?? "", [eventTypes])

  useEffect(() => {
    if (!enabled) {
      setState("closed")
      return undefined
    }

    const controller = new AbortController()
    let reconnectTimer: number | null = null
    let disposed = false

    const open = () => {
      setState("connecting")
      void connectDashboardEvents({
        ticker,
        eventTypes: eventTypesKey ? eventTypesKey.split(",") : undefined,
        signal: controller.signal,
        onEvent: (event) => {
          setState("open")
          setError(null)
          setLastEvent(event)
          onEvent(event)
        },
        onError: (nextError) => {
          if (disposed) {
            return
          }
          setState("error")
          setError(nextError.message)
          reconnectTimer = window.setTimeout(open, 3000)
        },
      })
    }

    open()

    return () => {
      disposed = true
      controller.abort()
      if (reconnectTimer !== null) {
        window.clearTimeout(reconnectTimer)
      }
      setState("closed")
    }
  }, [enabled, eventTypesKey, onEvent, ticker])

  return { state, lastEvent, error }
}
