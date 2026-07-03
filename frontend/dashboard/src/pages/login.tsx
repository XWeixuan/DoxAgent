import { useEffect, useState } from "react"
import type { FormEvent } from "react"
import { useNavigate, useSearchParams } from "react-router-dom"
import { KeyRoundIcon, LogInIcon, MailIcon } from "lucide-react"
import { toast } from "sonner"

import { PageHeader } from "@/components/dashboard/shared"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import {
  Field,
  FieldDescription,
  FieldGroup,
  FieldLabel,
} from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import { useDashboardAuth } from "@/lib/dashboard-auth"

export function LoginPage() {
  const auth = useDashboardAuth()
  const [email, setEmail] = useState("")
  const [password, setPassword] = useState("")
  const [submitting, setSubmitting] = useState(false)
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const next = searchParams.get("next") || "/overview"

  useEffect(() => {
    if (auth.status === "authenticated") {
      navigate(next, { replace: true })
    }
  }, [auth.status, navigate, next])

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (!email.trim() || !password) {
      toast.error("请输入邮箱和密码。")
      return
    }
    if (auth.config?.provider !== "supabase") {
      toast.error("当前后端未启用 Supabase 鉴权。")
      return
    }

    setSubmitting(true)
    try {
      await auth.signIn(email.trim(), password)
      toast.success("登录成功，已确认 Dashboard dev 权限。")
      navigate(next, { replace: true })
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="mx-auto flex w-full max-w-xl flex-col gap-6">
      <PageHeader
        title="登录"
        eyebrow="Supabase Auth"
        description="使用 DoxAtlas Supabase 账号登录；只有 DEVELOPER tier 用户可以访问 DoxAgent Dashboard。"
      />
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-lg font-light">
            <KeyRoundIcon />
            Dashboard Dev Access
          </CardTitle>
          <CardDescription>
            登录态和权限都会由后端 Dashboard State API 再次校验。
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form className="flex flex-col gap-4" onSubmit={submit}>
            <FieldGroup>
              <Field>
                <FieldLabel htmlFor="dashboard-email">Email</FieldLabel>
                <Input
                  id="dashboard-email"
                  type="email"
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                  autoComplete="email"
                  disabled={submitting || auth.status === "loading"}
                />
              </Field>
              <Field>
                <FieldLabel htmlFor="dashboard-password">Password</FieldLabel>
                <Input
                  id="dashboard-password"
                  type="password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  autoComplete="current-password"
                  disabled={submitting || auth.status === "loading"}
                />
                <FieldDescription>
                  非 dev 用户登录后会停留在无权限状态，不会进入 Dashboard 页面。
                </FieldDescription>
              </Field>
            </FieldGroup>
            {auth.error ? <p className="text-sm text-destructive">{auth.error}</p> : null}
            <Button
              type="submit"
              disabled={submitting || auth.status === "loading" || auth.config?.provider !== "supabase"}
            >
              <LogInIcon data-icon="inline-start" />
              {submitting ? "登录中" : "登录"}
            </Button>
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <MailIcon className="size-3.5" />
              {auth.config?.provider === "supabase"
                ? "当前后端已启用真实 Supabase 鉴权。"
                : "当前后端处于本地 mock 鉴权模式。"}
            </div>
          </form>
        </CardContent>
      </Card>
    </div>
  )
}
