import { createRoot } from "react-dom/client";
import { QueryClient } from "@tanstack/react-query";
import type { BindingConfig, SourceStatus } from "@contract";
import { RuntimeProvider, type Runtime } from "../../src/core/runtime";
import BindingSettings from "../../src/pages/source-settings";
import { resource } from "../fixtures/wire";
import "../../src/styles.css";
import "../../src/business.css";
import "../../src/refinement.css";

const config: BindingConfig = {
  binding_id: "MU:huggingnews_rss", ticker: "MU",
  source: { binding_id: "MU:huggingnews_rss", source_id: "huggingnews_rss", name: "Hugging News", kind: "crawler" },
  binding_version: 1, source_version: 1, control_etag: '"binding:1:source:1"',
  effective: {
    enabled: true, source_parameters: {},
    polling: { enabled: true, target_interval_seconds: 60, tolerance_ratio: .5, alert_after_seconds: 180, active_windows: [] },
    streaming: { publication_mode: "immediate", buffer: { max_items: 10, max_wait_seconds: 60, max_compiled_body_chars: 16000 } },
    extra_relevance: null,
  },
  parameter_schema: { type: "object", properties: {}, additionalProperties: false },
  editor: { parameter_form: false, parameter_form_id: null, json: true, polling_interval_form: false },
  writable_parameter_paths: [], redacted_parameter_paths: [],
  monitoring_terms_usage: { acquisition_mode: "by_distribution", terms_mode: "UNIFIED", managed_parameter_paths: [] },
};
let writes = 0;
const page = (items: unknown[]) => resource({ items, next_cursor: null, has_more: false, snapshot_id: "fixture" });
const source = () => ({
  source: config.source, extra_relevance_configured: config.effective.extra_relevance != null,
  enabled: true, target_interval_seconds: 60, publication_mode: "immediate",
  health_group: "HEALTHY", poll_status: "succeeded", current_error: null,
  last_success_at: { state: "AVAILABLE", value: "2026-11-27T00:00:00Z", reason: null },
  last_poll_latency_seconds: { state: "AVAILABLE", value: .5, reason: null },
  last_published_count: { state: "AVAILABLE", value: 3, reason: null },
} as unknown as SourceStatus);
const runtime = {
  scope: "binding-extra-fixture", query: new QueryClient(),
  api: { request: async (name: string, _path: string, options?: { body?: Record<string, unknown>; etag?: string }) => {
    if (name === "Sources") return { data: page([source()]) };
    if (name === "AvailableSources") return { data: page([]) };
    if (name === "MonitoringTerms") return { data: { state: "UNAVAILABLE", data: null } };
    if (name === "Binding") return { data: resource(structuredClone(config)) };
    if (name === "BindingWrite") {
      if (options?.etag !== config.control_etag) throw new Error("stale binding");
      config.effective.extra_relevance = options.body!.extra_relevance as BindingConfig["effective"]["extra_relevance"];
      config.binding_version++;
      config.control_etag = `"binding:${config.binding_version}:source:1"`;
      writes++;
      document.getElementById("saved")!.textContent = JSON.stringify({ writes, extra: config.effective.extra_relevance });
      return { data: config };
    }
    throw new Error("Unexpected request " + name);
  } },
} as unknown as Runtime;
createRoot(document.getElementById("root")!).render(
  <RuntimeProvider runtime={runtime}><main className="ticker-workspace" style={{ maxWidth: 1450, margin: "auto" }}>
    <h1>额外消息源</h1><BindingSettings ticker="MU" view="fixture" refresh={async () => {}} />
    <output id="saved" hidden />
  </main></RuntimeProvider>,
);
