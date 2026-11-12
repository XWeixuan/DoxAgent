import { createRoot } from "react-dom/client";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import type { GlobalSourceCard, SourceCatalog } from "@contract";
import SourceCatalogPage from "../../src/pages/source-catalog";
import { GlobalActions } from "../../src/components/global-actions";
import { Brand } from "../../src/app";
import { createRuntime, RuntimeProvider } from "../../src/core/runtime";
import { createTestAuth } from "../fixtures/auth";
import { value, resource } from "../fixtures/wire";
import { ApiFailure } from "../../src/core/api";
import "../../src/styles.css";
import "../../src/business.css";
import "../../src/refinement.css";

const runtime = createRuntime(createTestAuth());
const control = {
  calls: [] as string[],
  fail: location.search.includes("fail"),
  serial: 0,
};
Object.assign(window, { catalogControl: control });
const tickers = [
  "BE",
  "CEG",
  "COHR",
  "INTC",
  "IONQ",
  "MU",
  "NBIS",
  "ONDS",
  "RKLB",
  "SNOW",
];
const sources = [
  [
    "barrons_ticker_news",
    "Barron's ticker news and Other Dow Jones (excluding IBD)",
  ],
  ["reuters_site_search", "Reuters Site Search"],
  ["yahoo_finance_news", "Yahoo Finance News"],
  ["corporate_ir_press_releases", "企业 IR / Press Releases"],
  ["globenewswire_massive", "GlobeNewswire"],
  ["newswire_rss", "Newswire RSS"],
  ["data_center_frontier", "Data Center Frontier / 数据中心基础设施"],
  [
    "register_software_cloud_security",
    "The Register / Software, Cloud & Security",
  ],
  ["unknown_source", "新登记但未标注的消息源"],
].map(([source_id, name], i): GlobalSourceCard => ({
  source_id,
  name,
  short_name: i === 0 ? "Barron’s" : undefined,
  configured_intervals_seconds: i === 2 ? [20, 30] : [20],
  poll_counts:
    i === 1
      ? { succeeded: 0, partial: 0, failed: 10, unknown: 0 }
      : i === 2
        ? { succeeded: 5, partial: 0, failed: 5, unknown: 0 }
        : { succeeded: 10, partial: 0, failed: 0, unknown: 0 },
  description:
    i === 0
      ? "Barron’s 是道琼斯旗下财经媒体；采集个股新闻页及相关道琼斯文章，排除 IBD 栏目。"
      : i === 1
        ? "Reuters（路透社）是全球新闻通讯社；按公司及主题检索词监测站内财经、产业和企业报道。"
        : "科技与产业消息源；监测已配置的公司动态、行业新闻与公开披露入口。",
  enabled: i !== 5,
  delivery_mode: i === 4 ? "test" : "production",
  acquisition_mode:
    i === 4
      ? "massive_distribution"
      : i >= 6
        ? "by_distribution"
        : i === 1
          ? "by_search"
          : "by_ticker",
  classification:
    i === 8
      ? null
      : {
          applicability: i >= 6 ? "INDUSTRY" : "GLOBAL",
          sector_ids: i === 6 ? ["L1-01", "L1-02"] : i === 7 ? ["L1-02"] : [],
          listing: {
            primary:
              i === 1 ? ["CHROME_CDP"] : i === 7 ? ["RSS", "HTTP"] : ["HTTP"],
            fallback: i === 0 ? ["CHROME_CDP"] : [],
          },
          body: { primary: i === 0 ? ["CHROME_CDP"] : ["HTTP"], fallback: [] },
        },
  entry_points: [
    {
      url: "https://example.com/news/" + source_id,
      label: "example.com",
      ticker: null,
    },
    {
      url: "https://example.com/feeds/{ticker}",
      label: "example.com",
      ticker: "MU",
    },
  ],
  subscriptions:
    i === 4 || i === 8
      ? []
      : tickers.map((ticker, j) => ({
          ticker,
          binding_id: ticker + ":" + source_id,
          enabled: i !== 5 && j !== 9,
        })),
  state_counts: {
    running: i === 4 || i === 8 ? 0 : 6,
    sleeping: 2,
    abnormal: i === 1 ? 1 : 0,
    disabled: i === 5 ? 10 : 1,
    unknown: 0,
  },
  errors:
    i === 1
      ? [
          {
            ticker: "BE",
            code: "HTTP_403",
            message: "当前采集请求失败，请检查消息源配置与服务端记录。",
          },
        ]
      : [],
  metrics: {
    last_success_at: value("2026-11-12T14:00:00Z"),
    published_today: value(i === 5 ? 0 : 123),
    mean_e2e_seconds: i === 4 ? null : 91,
    p95_e2e_seconds: i === 4 ? null : 120,
    mean_capture_seconds: i === 4 ? null : 304.26,
    e2e_samples: i === 4 ? 0 : 10,
    capture_samples: i === 4 ? 0 : 8,
    refresh_seconds: 30,
    refresh_samples: 19,
    body_ratio: i === 5 ? 0 : 0.8,
    body_total: 5,
    body_full: i === 5 ? 0 : 4,
    article_samples: 10,
    body_applicable: true,
  },
}));
const data: SourceCatalog = {
  snapshot_at: "2026-11-12T14:00:00Z",
  semantic_day: "2026-11-12",
  sectors: [
    { id: "L1-01", name: "AI、半导体与计算基础设施" },
    { id: "L1-02", name: "软件、云、网络安全与前沿计算" },
  ],
  sources,
};
runtime.api.request = async (name, path) => {
  control.calls.push(path);
  if (name !== "SourceCatalog") throw new Error("Unexpected request: " + name);
  if (control.fail)
    throw new ApiFailure(
      "INTERNAL_ERROR",
      "读取失败",
      500,
      "catalog-fixture-error",
    );
  control.serial++;
  return {
    data: resource((window as any).catalogPreview ?? data),
    meta: {},
  } as never;
};
createRoot(document.getElementById("root")!).render(
  <RuntimeProvider runtime={runtime}>
    <MemoryRouter initialEntries={["/message-bus/sources"]}>
      <header className="app-header">
        <Brand />
        <div className="account-area">
          <GlobalActions />
        </div>
      </header>
      <main className="ticker-workspace global-workspace">
        <Routes>
          <Route path="/message-bus/sources" element={<SourceCatalogPage />} />
          <Route path="/audit/cost" element={<h1>收益 / 成本审计</h1>} />
        </Routes>
      </main>
    </MemoryRouter>
  </RuntimeProvider>,
);
