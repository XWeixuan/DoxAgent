-- Compact W3 exception-plane fields only. Full prompts, artifacts, Web Search
-- traces, source messages, and thread history remain in authoritative SQLite.

alter table doxagent.persistent_runtime_v2_terminal_cases
  add column if not exists initial_route text,
  add column if not exists resolved_route text,
  add column if not exists w3_resolved boolean not null default false;

alter table doxagent.persistent_runtime_v2_daily_closes
  add column if not exists w3_coverage_gap_count integer not null default 0
    check (w3_coverage_gap_count >= 0);

comment on column doxagent.persistent_runtime_v2_terminal_cases.initial_route is
  'Immutable fast-plane W1/W2 route before W3 adjudication.';
comment on column doxagent.persistent_runtime_v2_terminal_cases.resolved_route is
  'Final W3 route when the exception plane adjudicated the case.';
comment on column doxagent.persistent_runtime_v2_daily_closes.w3_coverage_gap_count is
  'Compact count only; full W3 coverage-gap records stay in local SQLite.';
