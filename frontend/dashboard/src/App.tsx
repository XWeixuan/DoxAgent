import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom"

import { DashboardLayout } from "@/components/dashboard/layout"
import { AuditPage } from "@/pages/audit"
import { LoginPage } from "@/pages/login"
import { MessageBusPage } from "@/pages/message-bus"
import { OverviewPage } from "@/pages/overview"
import { ResearchPage } from "@/pages/research"
import { RuntimePage } from "@/pages/runtime"
import { StrategyPage } from "@/pages/strategy"

function App() {
  return (
    <BrowserRouter>
      <DashboardLayout>
        <Routes>
          <Route path="/" element={<Navigate to="/overview" replace />} />
          <Route path="/login" element={<LoginPage />} />
          <Route path="/overview" element={<OverviewPage />} />
          <Route path="/ticker/:ticker/research" element={<ResearchPage />} />
          <Route path="/ticker/:ticker/strategy" element={<StrategyPage />} />
          <Route path="/ticker/:ticker/message-bus" element={<MessageBusPage />} />
          <Route path="/ticker/:ticker/runtime" element={<RuntimePage />} />
          <Route path="/ticker/:ticker/audit" element={<AuditPage />} />
          <Route path="*" element={<Navigate to="/overview" replace />} />
        </Routes>
      </DashboardLayout>
    </BrowserRouter>
  )
}

export default App
