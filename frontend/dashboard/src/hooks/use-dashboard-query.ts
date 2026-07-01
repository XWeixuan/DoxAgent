import { useCallback, useEffect, useRef, useState } from "react"

import { DashboardApiError } from "@/lib/dashboard-api"

export interface QueryState<T> {
  data: T | null
  error: string | null
  isLoading: boolean
  isRefreshing: boolean
  lastUpdatedAt: Date | null
  reload: () => Promise<void>
}

export function getErrorMessage(error: unknown) {
  if (error instanceof DashboardApiError) {
    return `${error.message}（${error.code}）`
  }
  if (error instanceof Error) {
    return error.message
  }
  return String(error)
}

export function useDashboardQuery<T>(
  loader: () => Promise<T>,
  options: { intervalMs?: number; enabled?: boolean } = {}
): QueryState<T> {
  const { intervalMs, enabled = true } = options
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [isRefreshing, setIsRefreshing] = useState(false)
  const [lastUpdatedAt, setLastUpdatedAt] = useState<Date | null>(null)
  const mountedRef = useRef(true)
  const loadingRef = useRef(false)
  const hasDataRef = useRef(false)

  const reload = useCallback(async () => {
    if (!enabled || loadingRef.current) {
      return
    }
    loadingRef.current = true
    setIsRefreshing(hasDataRef.current)
    setIsLoading(!hasDataRef.current)
    try {
      const next = await loader()
      if (!mountedRef.current) {
        return
      }
      setData(next)
      hasDataRef.current = true
      setError(null)
      setLastUpdatedAt(new Date())
    } catch (nextError) {
      if (!mountedRef.current) {
        return
      }
      setError(getErrorMessage(nextError))
    } finally {
      if (mountedRef.current) {
        setIsLoading(false)
        setIsRefreshing(false)
      }
      loadingRef.current = false
    }
  }, [enabled, loader])

  useEffect(() => {
    mountedRef.current = true
    void reload()
    return () => {
      mountedRef.current = false
    }
  }, [reload])

  useEffect(() => {
    if (!enabled || !intervalMs) {
      return undefined
    }
    const timer = window.setInterval(() => {
      void reload()
    }, intervalMs)
    return () => window.clearInterval(timer)
  }, [enabled, intervalMs, reload])

  return {
    data,
    error,
    isLoading,
    isRefreshing,
    lastUpdatedAt,
    reload,
  }
}
