import {
  Component,
  Suspense,
  lazy,
  useState,
  type FormEvent,
  type ReactNode,
} from "react";
import { TickerSwitcher } from "@/components/ticker-navigation";
import { Link, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ArrowRight, LogOut, ShieldCheck } from "lucide-react";
import { Toaster } from "sonner";
import { Button } from "@/components/ui/button";

import { Input } from "@/components/ui/input";
import { Field, FieldGroup, FieldLabel } from "@/components/ui/field";
import { Skeleton } from "@/components/ui/skeleton";
import { Notice } from "@/components/state";

import { useIdentity, useRuntime } from "@/core/runtime";
const TickerWorkspace = lazy(() => import("@/pages/ticker-workspace"));
const Overview = lazy(() => import("@/pages/overview"));
export class ErrorBoundary extends Component<
  { children: ReactNode },
  { error: boolean }
> {
  state = { error: false };
  static getDerivedStateFromError() {
    return { error: true };
  }
  render() {
    return this.state.error ? (
      <div className="boot-error">
        <h1>页面暂时无法显示</h1>
        <p>请重新打开页面后再试。</p>
        <Button onClick={() => location.reload()}>重新加载</Button>
      </div>
    ) : (
      this.props.children
    );
  }
}
export function Brand() {
  return (
    <Link to="/overview" aria-label="DoxAgent 首页" className="brand">
      <span>doxagent</span>
    </Link>
  );
}
function Login() {
  const { auth } = useRuntime();
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy(true);
    setError("");
    try {
      await auth.login(String(form.get("email")), String(form.get("password")));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <main className="login-page">
      <Brand />
      <section className="login-panel">
        <h1>登录 DoxAgent</h1>
        <form onSubmit={submit}>
          <FieldGroup>
            <Field>
              <FieldLabel htmlFor="email">邮箱</FieldLabel>
              <Input
                id="email"
                name="email"
                type="email"
                autoComplete="username"
                required
              />
            </Field>
            <Field>
              <FieldLabel htmlFor="password">密码</FieldLabel>
              <Input
                id="password"
                name="password"
                type="password"
                autoComplete="current-password"
                required
              />
            </Field>
            {error && <Notice danger>{error}</Notice>}
            <Button type="submit" disabled={busy}>
              {busy ? "正在登录…" : "登录"}
              <ArrowRight data-icon="inline-end" />
            </Button>
          </FieldGroup>
        </form>
      </section>
    </main>
  );
}
function Workspace() {
  const location = useLocation();
  const tickerRoute = /^\/ticker\/([^/]+)\/([^/]+)/.exec(location.pathname);
  const runtime = useRuntime(),
    identity = useIdentity();
  const principal = useQuery({
    queryKey: [runtime.scope, "principal"],
    queryFn: ({ signal }) =>
      runtime.api.request("Principal", "/auth/me", { signal }),
  });
  return (
    <>
      <a href="#main" className="skip-link">
        跳到主内容
      </a>
      <header className="app-header">
        <Brand />

        {tickerRoute && (
          <TickerSwitcher
            ticker={decodeURIComponent(tickerRoute[1])}
            page={tickerRoute[2]}
          />
        )}

        <div className="account-area">
          {!principal.data?.data.can_operate && (
            <span className="account-label">
              <ShieldCheck aria-hidden="true" />
              只读访问
            </span>
          )}
          <Button
            variant="ghost"
            size="icon-sm"
            title={identity?.email}
            aria-label="退出登录"
            onClick={() => void runtime.auth.logout()}
          >
            <LogOut data-icon="inline-start" />
          </Button>
        </div>
      </header>
      {principal.isPending ? (
        <div className="boot-loading">
          <Skeleton className="h-8 w-48" />
          <Skeleton className="h-40 w-full" />
        </div>
      ) : principal.error ? (
        <main className="boot-error">
          <Notice danger>
            {principal.error.message}
            <Button variant="link" onClick={() => void principal.refetch()}>
              重试
            </Button>
          </Notice>
        </main>
      ) : !principal.data?.data.can_read ? (
        <main className="boot-error">
          <h1>暂无工作空间访问权限</h1>
          <p>此账户尚未获得开发者访问授权。</p>
        </main>
      ) : (
        <Suspense
          fallback={
            <div className="boot-loading">
              <Skeleton className="h-8 w-48" />
              <Skeleton className="h-60 w-full" />
            </div>
          }
        >
          <Routes>
            <Route path="/overview" element={<Overview />} />
            <Route path="/ticker/:ticker/:page" element={<TickerWorkspace />} />
            <Route path="/" element={<Navigate to="/overview" replace />} />
            <Route
              path="*"
              element={
                <main className="boot-error">
                  <h1>页面不存在</h1>
                  <Button asChild>
                    <Link to="/overview">返回 Overview</Link>
                  </Button>
                </main>
              }
            />
          </Routes>
        </Suspense>
      )}
      <Toaster position="bottom-right" />
    </>
  );
}
export function App() {
  const identity = useIdentity();
  return identity ? (
    <Workspace key={`${identity.id}:${identity.sessionKey ?? ""}`} />
  ) : (
    <Login />
  );
}
