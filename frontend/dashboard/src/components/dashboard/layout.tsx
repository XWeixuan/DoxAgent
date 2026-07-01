import type { ReactNode } from "react"
import { Link, NavLink, useLocation } from "react-router-dom"
import {
  BarChart3Icon,
  BookOpenTextIcon,
  ChartNoAxesCombinedIcon,
  CircuitBoardIcon,
  MessageSquareTextIcon,
  RouteIcon,
} from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { cn } from "@/lib/utils"

const tickerNavItems = [
  { label: "投研资料", path: "research", icon: BookOpenTextIcon },
  { label: "执行策略", path: "strategy", icon: RouteIcon },
  { label: "消息总线", path: "message-bus", icon: MessageSquareTextIcon },
  { label: "运行状态", path: "runtime", icon: CircuitBoardIcon },
  { label: "收益 / 成本审计", path: "audit", icon: ChartNoAxesCombinedIcon },
]

function useTickerFromPath() {
  const location = useLocation()
  const match = location.pathname.match(/^\/ticker\/([^/]+)/)
  return match?.[1]?.toUpperCase() ?? null
}

export function DashboardLayout({ children }: { children: ReactNode }) {
  const location = useLocation()
  const ticker = useTickerFromPath()
  const isTickerPage = location.pathname.startsWith("/ticker/") && Boolean(ticker)

  return (
    <div className="dox-shell">
      <header className="dox-header">
        <div className="relative mx-auto flex min-h-14 w-full max-w-[1440px] items-center px-4 md:px-6">
          <Link
            to="/overview"
            className="brand-wordmark shrink-0 text-xl text-primary transition-opacity hover:opacity-80"
          >
            doxagent
          </Link>

          {isTickerPage ? (
            <nav className="absolute left-1/2 top-1/2 hidden -translate-x-1/2 -translate-y-1/2 items-center lg:flex">
              {tickerNavItems.map((item, index) => {
                const Icon = item.icon
                return (
                  <NavLink
                    key={item.path}
                    to={`/ticker/${ticker}/${item.path}`}
                    className={({ isActive }) =>
                      cn(
                        "flex h-8 items-center gap-2 border-border px-4 text-sm text-muted-foreground transition-colors hover:text-foreground",
                        index > 0 && "border-l",
                        isActive && "font-medium text-primary"
                      )
                    }
                  >
                    <Icon data-icon="inline-start" />
                    {item.label}
                  </NavLink>
                )
              })}
            </nav>
          ) : null}

          <div className="ml-auto flex items-center gap-2">
            <Badge variant="outline" className="rounded-[4px] px-3 py-1">
              {ticker ?? "全局"}
            </Badge>
          </div>
        </div>

        {isTickerPage ? (
          <nav className="flex overflow-x-auto border-t px-4 py-2 lg:hidden">
            {tickerNavItems.map((item, index) => {
              const Icon = item.icon
              return (
                <NavLink
                  key={item.path}
                  to={`/ticker/${ticker}/${item.path}`}
                  className={({ isActive }) =>
                    cn(
                      "flex h-8 shrink-0 items-center gap-2 border-border px-3 text-sm text-muted-foreground",
                      index > 0 && "border-l",
                      isActive && "font-medium text-primary"
                    )
                  }
                >
                  <Icon data-icon="inline-start" />
                  {item.label}
                </NavLink>
              )
            })}
          </nav>
        ) : null}
      </header>

      <main className="mx-auto flex w-full max-w-[1440px] flex-col gap-5 px-4 py-5 md:px-6">
        {children}
      </main>
    </div>
  )
}

export function TopologyMark() {
  return (
    <span className="inline-flex items-center gap-2 text-xs text-muted-foreground">
      <BarChart3Icon data-icon="inline-start" />
      Dashboard State API
    </span>
  )
}
