-- Low-egress Persistent Runtime V2 terminal and Daily Close projections.
-- Full messages, model turns, effects and provisional facts remain in local SQLite.

create table if not exists doxagent.persistent_runtime_v2_terminal_cases (
  case_id text primary key,
  ticker text not null check (ticker = upper(ticker)),
  trading_date date not null,
  case_status text not null,
  technical_status text not null,
  primary_route text,
  event_library_version bigint not null check (event_library_version >= 1),
  policy_set_version bigint not null check (policy_set_version >= 1),
  hot_path_latency_ms bigint check (hot_path_latency_ms is null or hot_path_latency_ms >= 0),
  model_turn_count integer not null check (model_turn_count >= 0),
  projection_json jsonb not null check (jsonb_typeof(projection_json) = 'object'),
  updated_at timestamptz not null,
  constraint persistent_runtime_v2_terminal_projection_size check (
    octet_length(projection_json::text) <= 16384
  )
);

create index if not exists idx_persistent_runtime_v2_terminal_ticker_date
  on doxagent.persistent_runtime_v2_terminal_cases (ticker, trading_date desc, updated_at desc);

create table if not exists doxagent.persistent_runtime_v2_daily_closes (
  run_id text primary key,
  ticker text not null check (ticker = upper(ticker)),
  trading_date date not null,
  stage text not null,
  base_library_version bigint not null check (base_library_version >= 0),
  published_library_version bigint check (
    published_library_version is null or published_library_version >= 0
  ),
  candidate_count integer not null check (candidate_count >= 0),
  trade_record_count integer not null check (trade_record_count >= 0),
  badcase_count integer not null check (badcase_count >= 0),
  projection_json jsonb not null check (jsonb_typeof(projection_json) = 'object'),
  updated_at timestamptz not null,
  unique (ticker, trading_date),
  constraint persistent_runtime_v2_daily_projection_size check (
    octet_length(projection_json::text) <= 16384
  )
);

alter table doxagent.persistent_runtime_v2_terminal_cases enable row level security;
alter table doxagent.persistent_runtime_v2_terminal_cases force row level security;
alter table doxagent.persistent_runtime_v2_daily_closes enable row level security;
alter table doxagent.persistent_runtime_v2_daily_closes force row level security;
revoke all on table doxagent.persistent_runtime_v2_terminal_cases from anon, authenticated;
revoke all on table doxagent.persistent_runtime_v2_daily_closes from anon, authenticated;
grant select, insert, update on table doxagent.persistent_runtime_v2_terminal_cases to service_role;
grant select, insert, update on table doxagent.persistent_runtime_v2_daily_closes to service_role;
