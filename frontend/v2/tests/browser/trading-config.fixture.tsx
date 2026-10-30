import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import type {
  Period,
  TradingConfiguration,
  TradingCapital,
  TradingSettings,
} from "@contract";
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
const defaults: TradingSettings = {
  environment: "PAPER",
  cash_short_substitute_enabled: false,
  inverse_single_stock_etf: null,
  hedge_enabled: false,
  hedge_ratio: "1",
  margin_index_etf: null,
  cash_positive_index_etf: null,
  cash_inverse_index_etf: null,
  trailing_profit_enabled: false,
  banned_semantic_dates: [],
};
let configuration: TradingConfiguration = {
  ticker: "MU",
  revision: 1,
  configuration: defaults,
  active: {
    configuration: defaults,
    ticker_revision: 1,
    account_revision: "paper-profile",
    applied_revision: "applied-1",
    plan_schema_version: 2,
    applied_at: new Date().toISOString(),
  },
  accounts: {
    PAPER: { account_type: "MARGIN", revision: "paper-profile" },
    LIVE: { account_type: "CASH", revision: "live-profile" },
  },
  error: null,
};
let capital: TradingCapital = {
  accounts: [
    {
      environment: "PAPER",
      account_type: "MARGIN",
      desired: null,
      applied: null,
      book: {
        allocated: "4000",
        realized_net_pnl: "0",
        trading_equity: "4000",
        unrealized_pnl: "0",
        net_value: "4000",
        occupied_budget: "0",
        provisional: false,
      },
    },
  ],
};
runtime.api.request = (async (
  name: string,
  path: string,
  options: { body?: unknown } = {},
) => {
  if (name === "TradingConfiguration")
    return { data: wire.resource(structuredClone(configuration)) };
  if (name === "TradingConfigurationWrite") {
    const body = options.body as {
      configuration: TradingSettings;
      expected_revision: number;
    };
    configuration = {
      ...configuration,
      configuration: body.configuration,
      revision: configuration.revision + 1,
    };
    setTimeout(() => {
      configuration = {
        ...configuration,
        active: {
          configuration: configuration.configuration,
          ticker_revision: configuration.revision,
          account_revision:
            configuration.accounts[configuration.configuration.environment]
              .revision,
          applied_revision: `applied-${configuration.revision}`,
          plan_schema_version: 2,
          applied_at: new Date().toISOString(),
        },
      };
    }, 600);
    return { data: structuredClone(configuration) };
  }
  if (name === "TradingCapital")
    return { data: wire.resource(structuredClone(capital)) };
  if (name === "TradingCapitalWrite") {
    const body = options.body as { amount: string };
    capital.accounts[0].desired = {
      identity: "allocation-1",
      amount: body.amount,
      actor: "fixture",
      reason: "test",
    };
    setTimeout(() => {
      capital.accounts[0].applied = {
        identity: "allocation-1",
        amount: body.amount,
        applied_at: new Date().toISOString(),
      };
      capital.accounts[0].book!.allocated = body.amount;
    }, 600);
    return { data: structuredClone(capital) };
  }
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
        ib_gateway_live_status: "NOT_CONFIGURED",
        ib_gateway_paper_status: "CONNECTED",
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
  if (name === "Principal") return { data: { can_operate: true } };
  throw new Error(`Unexpected fixture request: ${name}`);
}) as typeof runtime.api.request;
createRoot(document.getElementById("root")!).render(
  <RuntimeProvider runtime={runtime}>
    <BrowserRouter>
      <Overview />
    </BrowserRouter>
  </RuntimeProvider>,
);
