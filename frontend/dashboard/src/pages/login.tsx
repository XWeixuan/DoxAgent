import { useState } from "react"
import type { FormEvent } from "react"
import { useNavigate } from "react-router-dom"
import { KeyRoundIcon, LogInIcon } from "lucide-react"
import { toast } from "sonner"

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
import { PageHeader } from "@/components/dashboard/shared"
import { dashboardApi, setDashboardAuthToken } from "@/lib/dashboard-api"

export function LoginPage() {
  const [token, setToken] = useState("dev-mock-token")
  const [submitting, setSubmitting] = useState(false)
  const navigate = useNavigate()

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    const nextToken = token.trim()
    if (!nextToken) {
      toast.error("请输入 mock token。")
      return
    }
    setSubmitting(true)
    try {
      setDashboardAuthToken(nextToken)
      await dashboardApi.overview()
      toast.success("登录状态已保存。")
      navigate("/overview")
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
        eyebrow="Mock Auth"
        description="本地 mock 鉴权入口；真实 Supabase dev 权限不在本阶段前端 mock 接入范围内。"
      />
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-lg font-light">
            <KeyRoundIcon />
            Mock Dashboard Token
          </CardTitle>
          <CardDescription>
            mock-required 模式下使用 dev-mock-token；mock-open 模式下可直接进入 Overview。
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form className="flex flex-col gap-4" onSubmit={submit}>
            <FieldGroup>
              <Field>
                <FieldLabel htmlFor="mock-token">Token</FieldLabel>
                <Input
                  id="mock-token"
                  value={token}
                  onChange={(event) => setToken(event.target.value)}
                  autoComplete="off"
                />
                <FieldDescription>输入 forbidden 可验证 FORBIDDEN 错误态。</FieldDescription>
              </Field>
            </FieldGroup>
            <Button type="submit" disabled={submitting}>
              <LogInIcon data-icon="inline-start" />
              登录
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  )
}
