import type { ReactNode } from "react"
import { Navigate, useLocation } from "react-router-dom"
import { KeyRoundIcon, ShieldAlertIcon } from "lucide-react"

import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { useDashboardAuth } from "@/lib/dashboard-auth"

export function ProtectedDashboardRoute({ children }: { children: ReactNode }) {
  const location = useLocation()
  const auth = useDashboardAuth()

  if (auth.status === "loading") {
    return (
      <div className="mx-auto flex min-h-[360px] w-full max-w-lg items-center justify-center">
        <Card className="w-full">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-lg font-light">
              <KeyRoundIcon />
              正在校验登录状态
            </CardTitle>
            <CardDescription>正在确认 Supabase session 和 Dashboard dev 权限。</CardDescription>
          </CardHeader>
        </Card>
      </div>
    )
  }

  if (auth.status === "unauthenticated") {
    const next = encodeURIComponent(`${location.pathname}${location.search}`)
    return <Navigate to={`/login?next=${next}`} replace />
  }

  if (auth.status === "forbidden") {
    return (
      <div className="mx-auto flex min-h-[360px] w-full max-w-lg items-center justify-center">
        <Card className="w-full">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-lg font-light">
              <ShieldAlertIcon />
              无 Dashboard dev 权限
            </CardTitle>
            <CardDescription>
              当前账号已登录，但没有访问 DoxAgent Dashboard 的 dev 层级权限。
            </CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            {auth.error ? <p className="text-sm text-muted-foreground">{auth.error}</p> : null}
            <Button variant="outline" onClick={() => void auth.signOut()}>
              退出登录
            </Button>
          </CardContent>
        </Card>
      </div>
    )
  }

  if (auth.status === "error") {
    return (
      <div className="mx-auto flex min-h-[360px] w-full max-w-lg items-center justify-center">
        <Card className="w-full">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-lg font-light">
              <ShieldAlertIcon />
              鉴权配置异常
            </CardTitle>
            <CardDescription>{auth.error ?? "Dashboard auth 初始化失败。"}</CardDescription>
          </CardHeader>
          <CardContent>
            <Button variant="outline" onClick={() => void auth.refresh()}>
              重试
            </Button>
          </CardContent>
        </Card>
      </div>
    )
  }

  return children
}
