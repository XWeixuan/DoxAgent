// Synthetic UI contract fixture; no production API or authentication calls.
import { createRoot } from "react-dom/client";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import type { Period } from "@contract";
import { createRuntime, RuntimeProvider } from "../../src/core/runtime";
import { createTestAuth } from "../fixtures/auth";
import * as wire from "../fixtures/wire";
import MessageBus from "../../src/pages/message-bus";
import "../../src/styles.css";
import "../../src/business.css";
import "../../src/refinement.css";

const runtime = createRuntime(createTestAuth());
const calls: string[] = [];
Object.assign(window,{busFixtureCalls:calls});
const meta = { as_of: new Date().toISOString() };
const emptyPage = {items: [], has_more: false, next_cursor: null, limit: 20, snapshot_id: "fixture"};
runtime.api.request = (async (name: string, path: string) => {
  calls.push(name+":"+path);
  if (name === "ReadContext") {
    const period = new URL(path,location.origin).searchParams.get("period") as Period;
    const context = wire.context("CURRENT_TRADING_DAY");
    return {meta,data:{...context,view_id:"fixture-"+period,page:"MESSAGE_BUS",ticker:"MU",
      period:{...context.period,selected:period},clock:{...context.clock,next_minute_at:new Date(Date.now()+60000).toISOString()}}};
  }
  if (name === "BusStatus") { const status = {meta,data:wire.resource({run_state:"RUNNING",
    source_counts:{normal:wire.value(8),abnormal:wire.value(1),excluded:wire.value(0)},
    continuous_run_started_at:wire.value("2026-10-23T12:00:00Z"),continuous_run_seconds:wire.value(900),
    average_poll_latency_seconds:wire.value(12),latency_sample_count:9,
    timeliness:{window_seconds:1800,samples:136,mean_seconds:3.28,p95_seconds:8.6,deferred:4,failed:1,sample_limit_reached:false,
      cadence:{expected_cycles:140,fresh_cycles:135,missing_cycles:1,coverage:135/140,
        max_gap_seconds:65,gap_mean_seconds:30.28,gap_p95_seconds:34.6,worst_request_p95_seconds:2.1,
        units:9,eligibility_unknown:0,sample_limit_reached:false,deferred:4,failed:1,
        pending_intake_candidates:2,failed_intake_candidates:1}}})};
    if (new URL(path,location.origin).searchParams.get("diagnostics")!=="true" && status.data.data) Reflect.deleteProperty(status.data.data,"timeliness");
    return status;
  }
  if (name === "BusMetrics") return {meta,data:wire.resource({
    published_revisions:wire.metric("published_revisions","125"),
    body_completion_success_ratio:wire.metric("body_completion_success_ratio","0.61"),
    new_article_body:{total:100,full:84,pending:6,failed:10,ratio:.84,basis:"FIRST_OBSERVED_ARTICLE",
      legacy_excluded:true,network_attempts:95,network_succeeded:79,cache_reuses:23}})};
  if (name === "Sources") return {meta,data:wire.resource({...emptyPage,items:[{
    published_today:wire.value(17),
    source:{source_id:"yahoo_finance_news",binding_id:"MU:yahoo_finance_news",name:"Yahoo Finance",kind:"crawler"},
    binding_version:1,source_version:1,publication_mode:"immediate",enabled:true,global_enabled:true,
    poll_status:"succeeded",health_group:"NORMAL",last_success_at:wire.value(new Date().toISOString()),
    last_published_count:wire.value(1),last_poll_latency_seconds:wire.value(3.28),current_error:null,
    target_interval_seconds:30,active_windows:[],next_target_at:wire.value(new Date(Date.now()+20000).toISOString()),
    countdown_state:"SCHEDULED",capture_age_seconds:1.3,pending_timestamp_count:3,
    cadence:{expected_cycles:60,fresh_cycles:59,missing_cycles:1,coverage:59/60,max_gap_seconds:65,
      gap_mean_seconds:30.28,gap_p95_seconds:34.6,worst_request_p95_seconds:2.1,
      units:2,eligibility_unknown:0,sample_limit_reached:false,deferred:0,failed:1}
  }]})};
  if (name === "Messages") return {meta,data:wire.resource({messages:emptyPage,stream_cursor:""})};
  throw new Error("Unexpected fixture request: "+name);
}) as typeof runtime.api.request;
createRoot(document.getElementById("root")!).render(<RuntimeProvider runtime={runtime}>
  <MemoryRouter initialEntries={["/tickers/MU/message-bus"]}><Routes>
    <Route path="/tickers/:ticker/message-bus" element={<MessageBus/>}/>
  </Routes></MemoryRouter>
</RuntimeProvider>);
