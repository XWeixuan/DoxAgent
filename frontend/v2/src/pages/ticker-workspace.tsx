import { Suspense, lazy, useSyncExternalStore, useEffect } from "react";
import { ModuleFreshness, Notice } from "@/components/state";
import { useRuntime } from "@/core/runtime";
import { staleMessage } from "@/core/api";
import type { Meta, PageKind, Period } from "@contract";
import { Navigate, useParams, useSearchParams } from "react-router-dom";
import { usePageContext } from "@/core/page-query";
import { periods } from "@/components/business-metrics";
import { TickerNavigation, tickerPages } from "@/components/ticker-navigation";
const Research = lazy(() => import("./research"));
const Expectations = lazy(() => import("./expectations"));
const Strategy = lazy(() => import("./strategy"));
const Events = lazy(() => import("./events"));
const MessageBus = lazy(() => import("./message-bus"));
const RuntimePage = lazy(() => import("./runtime"));
const Audit = lazy(() => import("./audit"));
const contextPages: Record<string, PageKind> = {
  research: "RESEARCH",
  expectations: "EXPECTATIONS",
  strategy: "POLICIES",
  events: "EVENTS",
  "message-bus": "MESSAGE_BUS",
  runtime: "RUNTIME",
  audit: "COST",
};
export default function TickerWorkspace() {
  const { ticker = "", page = "research" } = useParams();
  const [params] = useSearchParams();
  const period =
    page === "research" || page === "expectations"
      ? undefined
      : ((periods.find((x) => x[0] === params.get("period"))?.[0] ??
          "CURRENT_TRADING_DAY") as Period);
  // Shares the page's existing query; no separate ticker polling or mode endpoint.
  const context = usePageContext(
    ticker,
    contextPages[page] ?? "RESEARCH",
    period,
    { renew: false },
  );
  const lightweight =
    context.data?.data.activation.data?.running_mode === "LIGHTWEIGHT";
  const disabledPage =
    lightweight && (page === "expectations" || page === "strategy");
  useEffect(() => {
    const prior = document.title;
    document.title = `${ticker} · ${tickerPages[page] ?? "DoxAgent"}`;
    return () => {
      document.title = prior;
    };
  }, [ticker, page]);
  return (
    <ModuleFreshness value={false}>
      <main id="main" className="ticker-workspace">
        <TickerNavigation
          ticker={ticker}
          page={page}
          lightweight={lightweight}
        />
        <FreshnessSummary />
        <Suspense fallback={<div className="module-loading">正在加载</div>}>
          <div
            key={`${ticker}:${page}`}
            className="business-page"
            data-page={page}
          >
            {context.isPending ? (
              <div className="module-loading">正在加载</div>
            ) : disabledPage ? (
              <Navigate
                to={`/ticker/${encodeURIComponent(ticker)}/research`}
                replace
              />
            ) : page === "research" ? (
              <Research />
            ) : page === "expectations" ? (
              <Expectations />
            ) : page === "strategy" ? (
              <Strategy />
            ) : page === "events" ? (
              <Events />
            ) : page === "message-bus" ? (
              <MessageBus />
            ) : page === "runtime" ? (
              <RuntimePage />
            ) : page === "audit" ? (
              <Audit />
            ) : (
              <h1>{tickerPages[page] ?? "页面不存在"}</h1>
            )}
          </div>
        </Suspense>
      </main>
    </ModuleFreshness>
  );
}
function FreshnessSummary() {
  const { query } = useRuntime();
  const warning = useSyncExternalStore(
    (cb) => query.getQueryCache().subscribe(cb),
    () => {
      for (const q of query.getQueryCache().findAll()) {
        if (q.isActive()) {
          const data = q.state.data as { meta?: Meta } | undefined;
          const message = staleMessage(data?.meta);
          if (message) return message;
        }
      }
      return "";
    },
  );
  return warning ? (
    <div className="page-freshness">
      <Notice>{warning}</Notice>
    </div>
  ) : null;
}
