import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import type { Period } from "@contract";
import { createRuntime, RuntimeProvider } from "../../src/core/runtime";
import { createTestAuth } from "../fixtures/auth";
import * as wire from "../fixtures/wire";
import Overview from "../../src/pages/overview";
import "../../src/styles.css";
import "../../src/business.css";
import "../../src/refinement.css";

const runtime = createRuntime(createTestAuth());
const items = wire.rows();
items[0].metrics.api_token_cost.current_coverage.state = "UNKNOWN";
items[0].metrics.policy_hits.current_coverage.state = "UNKNOWN";
items[1].metrics.policy_hits.current = {
  state: "NOT_RECORDED",
  value: null,
  reason: "NOT_RECORDED",
};
let selected: Period = "CURRENT_TRADING_DAY";
runtime.api.request = (async (name: string, path: string) => {
  if (name === "ReadContext") {
    selected = new URL(path, location.origin).searchParams.get(
      "period",
    ) as Period;
    return { data: wire.context(selected) };
  }
  if (name === "Status")
    return {
      data: wire.resource({
        clock: wire.clock,
        normal_tickers: wire.value(3),
        blocked_tickers: wire.value(1),
        ib_gateway_status: "CONNECTED",
      }),
    };
  if (name === "Metrics")
    return { data: wire.resource(wire.metrics(selected)) };
  if (name === "Tickers")
    return {
      data: wire.resource({
        items: items.map((row) => wire.rowForPeriod(row, selected)),
        has_more: false,
        next_cursor: null,
        limit: 20,
        snapshot_id: "fixture",
      }),
    };
  if (name === "Capabilities") return { data: wire.capabilities };
  if (name === "Principal") return { data: { can_operate: false } };
  throw new Error(`Unexpected fixture request: ${name}`);
}) as typeof runtime.api.request;
createRoot(document.getElementById("root")!).render(
  <RuntimeProvider runtime={runtime}>
    <BrowserRouter>
      <Overview />
    </BrowserRouter>
  </RuntimeProvider>,
);
