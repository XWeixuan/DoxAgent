-- Independent DoxAgent V2 D3/O3 Policy Set lifecycle and atomic current head.

alter table doxagent.codex_run_registry
  drop constraint if exists codex_run_registry_workflow_lane_check;
alter table doxagent.codex_run_registry
  add constraint codex_run_registry_workflow_lane_check check (
    (workflow_version = 'codex_d1_v2' and research_lane = 'legacy_document1')
    or (workflow_version = 'codex_global_research_v1' and research_lane = 'global_research')
    or (workflow_version = 'codex_market_situation_v1' and research_lane = 'market_situation_research')
    or (workflow_version = 'codex_document2_v1' and research_lane = 'document2')
    or (workflow_version = 'codex_document3_v1' and research_lane = 'document3')
  );

alter table doxagent.codex_workflow_checkpoints
  drop constraint if exists codex_checkpoint_workflow_lane_check;
alter table doxagent.codex_workflow_checkpoints
  add constraint codex_checkpoint_workflow_lane_check check (
    (workflow_version = 'codex_d1_v2' and research_lane = 'legacy_document1')
    or (workflow_version = 'codex_global_research_v1' and research_lane = 'global_research')
    or (workflow_version = 'codex_market_situation_v1' and research_lane = 'market_situation_research')
    or (workflow_version = 'codex_document2_v1' and research_lane = 'document2')
    or (workflow_version = 'codex_document3_v1' and research_lane = 'document3')
  );

create table if not exists doxagent.codex_document3_bundles (
  run_id text primary key references doxagent.codex_run_registry(run_id),
  ticker text not null check (ticker = upper(ticker)),
  workflow_version text not null default 'codex_document3_v1'
    check (workflow_version = 'codex_document3_v1'),
  research_lane text not null default 'document3' check (research_lane = 'document3'),
  status text not null check (status in ('draft','published','failed')),
  bundle_json jsonb not null check (jsonb_typeof(bundle_json) = 'object'),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  published_at timestamptz,
  constraint codex_document3_bundle_size check (octet_length(bundle_json::text) <= 524288),
  constraint codex_document3_bundle_publish_state check (
    (status = 'published' and published_at is not null) or status <> 'published'
  )
);

create table if not exists doxagent.codex_document3_policy_sets (
  ticker text not null check (ticker = upper(ticker)),
  policy_set_version bigint not null check (policy_set_version >= 1),
  is_current boolean not null default false,
  publication_state text not null check (publication_state in ('COMPLETE','PARTIAL')),
  document2_run_id text not null,
  event_library_version bigint check (event_library_version is null or event_library_version >= 1),
  policy_count integer not null check (policy_count >= 0),
  policy_set_json jsonb not null check (jsonb_typeof(policy_set_json) = 'object'),
  runtime_projection_json jsonb not null
    check (jsonb_typeof(runtime_projection_json) = 'object'),
  published_at timestamptz not null,
  primary key (ticker, policy_set_version),
  constraint codex_document3_policy_set_size check (
    octet_length(policy_set_json::text) <= 4194304
  ),
  constraint codex_document3_runtime_projection_size check (
    octet_length(runtime_projection_json::text) <= 2097152
  )
);

create unique index if not exists uq_codex_document3_current_ticker
  on doxagent.codex_document3_policy_sets (ticker) where is_current;
create index if not exists idx_codex_document3_document2_run
  on doxagent.codex_document3_policy_sets (document2_run_id, ticker, policy_set_version desc);

comment on column doxagent.codex_document3_policy_sets.runtime_projection_json is
  'Compact runtime read model; consumers must not poll policy_set_json.';

alter table doxagent.codex_document3_bundles enable row level security;
alter table doxagent.codex_document3_bundles force row level security;
alter table doxagent.codex_document3_policy_sets enable row level security;
alter table doxagent.codex_document3_policy_sets force row level security;
revoke all on table doxagent.codex_document3_bundles from anon, authenticated;
revoke all on table doxagent.codex_document3_policy_sets from anon, authenticated;
grant select, insert, update on table doxagent.codex_document3_bundles to service_role;
grant select, insert, update on table doxagent.codex_document3_policy_sets to service_role;
