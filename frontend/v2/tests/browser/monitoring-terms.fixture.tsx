import { createRoot } from "react-dom/client";
import { QueryClient } from "@tanstack/react-query";
import type { MonitoringTermsConfig } from "@contract";
import { RuntimeProvider, type Runtime } from "../../src/core/runtime";
import { ApiClient } from "../../src/core/api";
import MonitoringTermsSettings from "../../src/pages/monitoring-terms-settings";
import "../../src/styles.css";
import "../../src/business.css";
import "../../src/refinement.css";

let current: MonitoringTermsConfig = {
  ticker: "MU", revision: 1, control_etag: '"monitoring-terms:MU:1"', updated_at: "2026-09-29T00:00:00Z",
  required_languages: ["en", "zh-Hant"],
  language_requirements: [{ language: "en", source_ids: ["reuters_site_search"] }, { language: "zh-Hant", source_ids: ["ctee_semiconductor"] }],
  configuration: {
    l1_concepts: [{ concept_id: "memory", expressions: { en: "Micron memory", "zh-Hant": "美光 記憶體" } }],
    l2: { en: { groups: [{ id: "memory", any: [{ literal: "Micron", regex: null, field: "title", case_sensitive: false, whole_word: false }], all: [], none: [] }] }, "zh-Hant": { groups: [{ id: "memory-zh", any: [{ literal: "美光", regex: null, field: "title", case_sensitive: false, whole_word: false }], all: [], none: [] }] } },
    definition: { relevant: "Micron business", irrelevant: "Unrelated products" },
  },
  consumers: [{ source_id: "reuters_site_search", name: "Reuters Site Search", binding_id: "MU:reuters_site_search", acquisition_mode: "by_search", content_language: "en", source_enabled: true, binding_enabled: true, search_policy_mode: "separate", jev_source_enabled: null, terms_mode: "UNIFIED", queries: [{ concept_ids: ["memory"], query: '"Micron memory"', query_key: "preview" }], preview_issue: null }],
};
const runtime = {
  scope: "fixture",
  query: new QueryClient(),
  api: { request: async (name: string, _path: string, options?: { method?: string; body?: { configuration: MonitoringTermsConfig["configuration"] } }) => {
    if (name === "MonitoringTerms" || name === "MonitoringTermsWrite") {
      if (options?.method === "PUT") {
        current = { ...current, revision: current.revision + 1, control_etag: '"monitoring-terms:MU:2"', configuration: options.body!.configuration };
        document.getElementById("saved-revision")!.textContent = String(current.revision);
      }
      return { data: name === "MonitoringTerms" ? { state: "AVAILABLE", data: current, reason: null, coverage: { state: "COMPLETE", reasons: [], known_count: null, excluded_count: null, observed_through: null } } : current };
    }
    if (name === "MonitoringTermsValidation") return { data: { valid: true, issues: [], search_previews: current.consumers } };
    throw new Error(`Unexpected ${name}`);
  } },
} as unknown as Runtime;

if (new URLSearchParams(location.search).has("real")) {
  runtime.api = new ApiClient(
    { token: () => "fixture", refresh: async () => false, invalidate: () => {} },
    (input, init) => fetch("http://127.0.0.1:8099" + String(input), init),
  );
}

createRoot(document.getElementById("root")!).render(<RuntimeProvider runtime={runtime}><main style={{ maxWidth: 1200, margin: "24px auto", padding: "0 16px" }}><MonitoringTermsSettings ticker="MU" /><output id="saved-revision">1</output></main></RuntimeProvider>);
