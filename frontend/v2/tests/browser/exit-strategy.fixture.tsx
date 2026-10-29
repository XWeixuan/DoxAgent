import { createRoot } from "react-dom/client";
import type { ExitStrategySummary } from "@contract";
import { ExitStrategyStatus } from "../../src/pages/runtime-case";
import "../../src/styles.css";
import "../../src/business.css";
import "../../src/refinement.css";

const base: ExitStrategySummary = {
  position_id: "trade:test",
  phase: "TRACKING",
  data_health: "WARMING_ATR",
  policy_revision: "xp_test",
  first_fill_at: "2026-10-27T14:00:00Z",
  notional: 10000,
  pnl: 100,
  mfe: 100,
  atr: null,
  stop_net: null,
  activated_at: null,
  exit_requested_at: null,
  exit_reason: null,
  updated_at: "2026-10-27T14:01:00Z",
  remaining_quantity: 100,
  group_status: "SINGLE_LEG",
  valid_observations: 6,
  invalid_observations: 1,
  confirmation: [],
};
createRoot(document.getElementById("root")!).render(
  <main
    className="case-details"
    style={{ padding: 24, maxWidth: 1100, margin: "auto" }}
  >
    {[
      base,
      {
        ...base,
        phase: "EXIT_REQUESTED" as const,
        data_health: "OK",
        pnl: 480,
        mfe: 1000,
        atr: 10,
        stop_net: 500,
        remaining_quantity: 25,
        exit_reason: "TRAILING_PROFIT",
        confirmation: [true, true, false, true, true, true],
      },
      {
        ...base,
        phase: "CLOSED" as const,
        data_health: "OK",
        remaining_quantity: 0,
      },
    ].map((value, i) => (
      <article className="execution-card" key={i}>
        <h3>{["跟踪", "退出待成交", "完成"][i]}</h3>
        <ExitStrategyStatus value={value} />
      </article>
    ))}
  </main>,
);
