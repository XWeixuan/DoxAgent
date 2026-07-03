import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom"
import type { ReactNode } from "react"

import { DashboardLayout } from "@/components/dashboard/layout"
import { ProtectedDashboardRoute } from "@/components/dashboard/protected-route"
import { AuditPage } from "@/pages/audit"
import { LoginPage } from "@/pages/login"
import { MessageBusPage } from "@/pages/message-bus"
import { OverviewPage } from "@/pages/overview"
import { ResearchPage } from "@/pages/research"
import { RuntimePage } from "@/pages/runtime"
import { StrategyPage } from "@/pages/strategy"
import { DashboardAuthProvider } from "@/lib/dashboard-auth"

function protectedPage(page: ReactNode) {
  return <ProtectedDashboardRoute>{page}</ProtectedDashboardRoute>
}

function App() {
  return (
    <BrowserRouter>
      <DashboardAuthProvider>
        <DashboardLayout>
          <Routes>
            <Route path="/" element={<Navigate to="/overview" replace />} />
            <Route path="/login" element={<LoginPage />} />
            <Route path="/overview" element={protectedPage(<OverviewPage />)} />
            <Route
              path="/ticker/:ticker/research"
              element={protectedPage(<ResearchPage />)}
            />
            <Route
              path="/ticker/:ticker/strategy"
              element={protectedPage(<StrategyPage />)}
            />
            <Route
              path="/ticker/:ticker/message-bus"
              element={protectedPage(<MessageBusPage />)}
            />
            <Route path="/ticker/:ticker/runtime" element={protectedPage(<RuntimePage />)} />
            <Route path="/ticker/:ticker/audit" element={protectedPage(<AuditPage />)} />
            <Route path="*" element={<Navigate to="/overview" replace />} />
          </Routes>
        </DashboardLayout>
      </DashboardAuthProvider>
    </BrowserRouter>
  )
}

export default App
