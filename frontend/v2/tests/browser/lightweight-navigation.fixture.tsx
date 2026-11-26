import { createRoot } from "react-dom/client";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { createRuntime, RuntimeProvider } from "../../src/core/runtime";
import { createTestAuth } from "../fixtures/auth";
import * as wire from "../fixtures/wire";
import TickerWorkspace from "../../src/pages/ticker-workspace";
import "../../src/styles.css";
import "../../src/business.css";
import "../../src/refinement.css";

const runtime = createRuntime(createTestAuth());
const fixtureCalls: string[] = [];
Object.assign(window, { fixtureCalls });
runtime.api.request = (async (name: string, path: string) => {
  fixtureCalls.push(name);
  if (name === "ReadContext") {
    const context = wire.context("CURRENT_TRADING_DAY");
    return {
      data: {
        ...context,
        ticker: "MU",
        page: new URL(path, location.origin).searchParams.get("page"),
        activation: wire.resource({ running_mode: "LIGHTWEIGHT" }),
      },
    };
  }
  if (name === "Research")
    return {
      data: {
        state: "NOT_PRODUCED",
        data: null,
        reason: "NOT_APPLICABLE",
        coverage: wire.coverage,
      },
    };
  throw new Error(`Unexpected fixture request: ${name}`);
}) as typeof runtime.api.request;
const page = new URLSearchParams(location.search).get("page") ?? "research";
createRoot(document.getElementById("root")!).render(
  <RuntimeProvider runtime={runtime}>
    <MemoryRouter initialEntries={[`/ticker/MU/${page}`]}>
      <Routes>
        <Route path="/ticker/:ticker/:page" element={<TickerWorkspace />} />
      </Routes>
    </MemoryRouter>
  </RuntimeProvider>,
);
