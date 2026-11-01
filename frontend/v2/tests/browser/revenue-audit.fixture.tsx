import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { createRuntime, RuntimeProvider } from "../../src/core/runtime";
import { createTestAuth } from "../fixtures/auth";
import { resource, context } from "../fixtures/wire";
import costFixtures from "./cost-audit.data.json";
import Audit from "../../src/pages/audit";
import fixtures from "./revenue-audit.data.json";
import type { RevenueReport } from "@contract";
import "../../src/styles.css";
import "../../src/business.css";
import "../../src/refinement.css";

const runtime = createRuntime(createTestAuth());
const calls: string[] = [];
let captures = 0,
  evaluated = false;
Object.assign(window, { revenueAuditCalls: calls });
runtime.api.request = async (name, path) => {
  calls.push(String(path));
  const params = new URL(String(path), location.origin).searchParams;
  if (name === "ReadContext")
    return {
      data: {
        ...context("PREVIOUS_TRADING_DAY"),
        page: "COST",
        view_id: "cost-view",
      },
      meta: {},
    } as never;
  if (name === "CostReport")
    return { data: resource(costFixtures["ALL:"]), meta: {} } as never;
  if (name === "RevenueSnapshot")
    return {
      data: {
        frame_id: `frame-${++captures}`,
        as_of: fixtures.ACTUAL.as_of,
        since: fixtures.ACTUAL.since,
      },
      meta: {},
    } as never;
  if (name === "RevenueEvaluation") {
    evaluated = true;
    return { data: { evaluated: 2 }, meta: {} } as never;
  }
  if (name === "RevenueDetail") {
    const identity = decodeURIComponent(
      String(path).split("/records/")[1].split("?")[0],
    );
    return {
      data: resource(
        fixtures.details[identity as keyof typeof fixtures.details],
      ),
      meta: {},
    } as never;
  }
  let data = structuredClone(
    fixtures[
      (params.get("mode") || "ACTUAL") as "ACTUAL" | "ALL" | "UNEXECUTED"
    ],
  ) as RevenueReport;
  if (!evaluated && params.get("mode") !== "ACTUAL") {
    data.items.forEach((r) => (r.hypothesis = null));
    data.hypothesis_pending_ids = fixtures.UNEXECUTED.items
      .filter((r) => r.kind === "UNEXECUTED")
      .map((r) => r.id);
    data.hypothesis_pending_count = data.hypothesis_pending_ids.length;
  }
  if (params.get("environment") === "LIVE")
    data = {
      ...data,
      environment: "LIVE",
      items: [],
      holdings: [],
      total: 0,
      kpis: {
        ...data.kpis,
        strategy_value: null,
        net: "0",
        realized: "0",
        upl: "0",
        fees: "0",
        actual_count: 0,
        intent_count: 0,
        completed_count: 0,
        win_rate: null,
      },
    };
  return { data: resource(data), meta: {} } as never;
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
