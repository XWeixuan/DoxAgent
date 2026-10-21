import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { createRuntime, RuntimeProvider } from "../../src/core/runtime";
import { createTestAuth } from "../fixtures/auth";
import { context, resource } from "../fixtures/wire";
import Audit from "../../src/pages/audit";
import fixtures from "./cost-audit.data.json";
import type { CostReport } from "@contract";
import "../../src/styles.css";
import "../../src/business.css";
import "../../src/refinement.css";
const runtime = createRuntime(createTestAuth());
const calls: string[] = [];
Object.assign(window, { costAuditCalls: calls });
runtime.api.request = async (name, path) => {
  calls.push(String(path));
  if (name === "ReadContext")
    return {
      data: {
        ...context("PREVIOUS_TRADING_DAY"),
        page: "COST",
        view_id: "cost-view",
      },
      meta: { view_id: "cost-view" },
    } as never;
  const params = new URL(String(path), location.origin).searchParams;
  const key =
    (params.get("ticker_selector") || "ALL") +
    ":" +
    (params.get("node_id") || "");
  const data = (fixtures as Record<string, unknown>)[key] as CostReport;
  return { data: resource(data), meta: { view_id: "cost-view" } } as never;
};
createRoot(document.getElementById("root")!).render(
  <BrowserRouter>
    <RuntimeProvider runtime={runtime}>
      <main className="ticker-workspace">
        <div className="business-page">
          <Audit />
        </div>
      </main>
    </RuntimeProvider>
  </BrowserRouter>,
);
