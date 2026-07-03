import { useEffect, useMemo, useRef, useState } from "react"

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
  const onEventRef = useRef(onEvent)
  const lastEventIdRef = useRef<string | null>(null)
  const seenEventIdsRef = useRef<Set<string>>(new Set())

  useEffect(() => {
    onEventRef.current = onEvent
  }, [onEvent])

  useEffect(() => {
    if (!enabled) {
      setState("closed")
      return undefined
    }

    lastEventIdRef.current = null
    seenEventIdsRef.current = new Set()
    const controller = new AbortController()
    let reconnectTimer: number | null = null
    let disposed = false

    const open = () => {
      setState("connecting")
      void connectDashboardEvents({
        ticker,
        eventTypes: eventTypesKey ? eventTypesKey.split(",") : undefined,
        lastEventId: lastEventIdRef.current ?? undefined,
        signal: controller.signal,
        onEvent: (event) => {
          setState("open")
          setError(null)
          if (seenEventIdsRef.current.has(event.event_id)) {
            return
          }
          seenEventIdsRef.current.add(event.event_id)
          if (seenEventIdsRef.current.size > 500) {
            const firstEventId = seenEventIdsRef.current.values().next().value
            if (firstEventId) {
              seenEventIdsRef.current.delete(firstEventId)
            }
          }
          lastEventIdRef.current = event.event_id
          setLastEvent(event)
          onEventRef.current(event)
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
  }, [enabled, eventTypesKey, ticker])

  return { state, lastEvent, error }
}
