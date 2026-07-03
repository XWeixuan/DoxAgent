import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react"
import type { ReactNode } from "react"
import { createClient } from "@supabase/supabase-js"
import type { Session, SupabaseClient } from "@supabase/supabase-js"

import {
  DashboardApiError,
  clearDashboardAuthToken,
  dashboardApi,
  setDashboardAccessTokenProvider,
  subscribeDashboardAuthErrors,
} from "@/lib/dashboard-api"
import type { DashboardAuthConfig, DashboardCurrentUser } from "@/lib/dashboard-api"

type DashboardAuthStatus =
  | "loading"
  | "unauthenticated"
  | "authenticated"
  | "forbidden"
  | "error"

interface DashboardAuthContextValue {
  status: DashboardAuthStatus
  config: DashboardAuthConfig | null
  session: Session | null
  user: DashboardCurrentUser | null
  error: string | null
  signIn: (email: string, password: string) => Promise<void>
  signOut: () => Promise<void>
  refresh: () => Promise<void>
}

const DashboardAuthContext = createContext<DashboardAuthContextValue | null>(null)

let cachedSupabaseClient: SupabaseClient | null = null
let cachedSupabaseClientKey: string | null = null

export function DashboardAuthProvider({ children }: { children: ReactNode }) {
  const [config, setConfig] = useState<DashboardAuthConfig | null>(null)
  const [client, setClient] = useState<SupabaseClient | null>(null)
  const [session, setSession] = useState<Session | null>(null)
  const [user, setUser] = useState<DashboardCurrentUser | null>(null)
  const [status, setStatus] = useState<DashboardAuthStatus>("loading")
  const [error, setError] = useState<string | null>(null)

  const validateSession = useCallback(
    async (nextSession: Session | null, nextClient: SupabaseClient | null = client) => {
      setSession(nextSession)
      if (!nextSession) {
        setUser(null)
        setStatus("unauthenticated")
        return null
      }

      setStatus("loading")
      try {
        const currentUser = await dashboardApi.me()
        if (!currentUser.is_dev) {
          setUser(currentUser)
          setStatus("forbidden")
          setError("当前账号没有 Dashboard dev 权限。")
          return currentUser
        }
        setUser(currentUser)
        setStatus("authenticated")
        setError(null)
        return currentUser
      } catch (nextError) {
        if (nextError instanceof DashboardApiError && nextError.status === 401) {
          await nextClient?.auth.signOut()
          clearDashboardAuthToken()
          setSession(null)
          setUser(null)
          setStatus("unauthenticated")
          setError("登录已过期，请重新登录。")
          return null
        }
        if (nextError instanceof DashboardApiError && nextError.status === 403) {
          setStatus("forbidden")
          setError(nextError.message)
          return null
        }
        setStatus("error")
        setError(nextError instanceof Error ? nextError.message : String(nextError))
        return null
      }
    },
    [client]
  )

  const signOut = useCallback(async () => {
    await client?.auth.signOut()
    clearDashboardAuthToken()
    setSession(null)
    setUser(null)
    setStatus("unauthenticated")
    setError(null)
  }, [client])

  const refresh = useCallback(async () => {
    if (!client) {
      setStatus("loading")
      const nextConfig = await dashboardApi.authConfig()
      setConfig(nextConfig)
      if (nextConfig.provider !== "supabase") {
        setDashboardAccessTokenProvider(null)
        setUser({
          user_id: "mock-dev-user",
          email: null,
          tier: nextConfig.dev_tier || "DEVELOPER",
          timezone: null,
          is_dev: true,
          auth_mode: nextConfig.auth_mode,
        })
        setStatus("authenticated")
        setError(null)
        return
      }
      if (!nextConfig.supabase_url || !nextConfig.supabase_publishable_key) {
        throw new Error("Dashboard Supabase auth config is incomplete.")
      }
      const nextClient = dashboardSupabaseClient(
        nextConfig.supabase_url,
        nextConfig.supabase_publishable_key
      )
      setClient(nextClient)
      setDashboardAccessTokenProvider(async () => {
        const {
          data: { session: currentSession },
        } = await nextClient.auth.getSession()
        return currentSession?.access_token
      })
      const {
        data: { session: currentSession },
      } = await nextClient.auth.getSession()
      await validateSession(currentSession, nextClient)
      return
    }

    const {
      data: { session: currentSession },
    } = await client.auth.getSession()
    await validateSession(currentSession, client)
  }, [client, validateSession])

  const signIn = useCallback(
    async (email: string, password: string) => {
      if (!client) {
        throw new Error("Dashboard auth client is not ready.")
      }
      setStatus("loading")
      setError(null)
      const { data, error: signInError } = await client.auth.signInWithPassword({
        email,
        password,
      })
      if (signInError) {
        setStatus("unauthenticated")
        setError(signInError.message)
        throw signInError
      }
      const currentUser = await validateSession(data.session, client)
      if (!currentUser?.is_dev) {
        throw new Error("当前账号没有 Dashboard dev 权限。")
      }
    },
    [client, validateSession]
  )

  useEffect(() => {
    let active = true
    let unsubscribeSupabase: (() => void) | null = null

    async function boot() {
      try {
        await refresh()
        if (!active || !client) {
          return
        }
        const {
          data: { subscription },
        } = client.auth.onAuthStateChange((_event, nextSession) => {
          void validateSession(nextSession, client)
        })
        unsubscribeSupabase = () => subscription.unsubscribe()
      } catch (nextError) {
        if (!active) {
          return
        }
        setStatus("error")
        setError(nextError instanceof Error ? nextError.message : String(nextError))
      }
    }

    void boot()
    return () => {
      active = false
      unsubscribeSupabase?.()
    }
  }, [client, refresh, validateSession])

  useEffect(
    () =>
      subscribeDashboardAuthErrors((detail) => {
        if (detail.status === 401) {
          void signOut()
          setError("登录已过期，请重新登录。")
          return
        }
        if (detail.status === 403) {
          setStatus("forbidden")
          setError(detail.message)
        }
      }),
    [signOut]
  )

  const value = useMemo<DashboardAuthContextValue>(
    () => ({
      status,
      config,
      session,
      user,
      error,
      signIn,
      signOut,
      refresh,
    }),
    [config, error, refresh, session, signIn, signOut, status, user]
  )

  return (
    <DashboardAuthContext.Provider value={value}>{children}</DashboardAuthContext.Provider>
  )
}

export function useDashboardAuth() {
  const context = useContext(DashboardAuthContext)
  if (!context) {
    throw new Error("useDashboardAuth must be used inside DashboardAuthProvider.")
  }
  return context
}

function dashboardSupabaseClient(supabaseUrl: string, supabasePublishableKey: string) {
  const cacheKey = `${supabaseUrl}::${supabasePublishableKey}`
  if (!cachedSupabaseClient || cachedSupabaseClientKey !== cacheKey) {
    cachedSupabaseClient = createClient(supabaseUrl, supabasePublishableKey, {
      auth: {
        persistSession: true,
        autoRefreshToken: true,
        detectSessionInUrl: true,
      },
    })
    cachedSupabaseClientKey = cacheKey
  }
  return cachedSupabaseClient
}
