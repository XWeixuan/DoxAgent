-- Independent Codex Global Research and Market Situation lanes.
-- High-text evidence remains in local SQLite; these tables contain bounded runtime state.

alter table doxagent.codex_run_registry
  add column if not exists research_lane text not null default 'legacy_document1';

alter table doxagent.codex_workflow_checkpoints
  add column if not exists research_lane text not null default 'legacy_document1';

alter table doxagent.codex_run_registry
  drop constraint if exists codex_run_registry_workflow_version_check;
alter table doxagent.codex_workflow_checkpoints
  drop constraint if exists codex_workflow_checkpoints_workflow_version_check;

do $$
begin
  if not exists (
    select 1 from pg_constraint
    where conname = 'codex_run_registry_workflow_lane_check'
      and conrelid = 'doxagent.codex_run_registry'::regclass
  ) then
    alter table doxagent.codex_run_registry
      add constraint codex_run_registry_workflow_lane_check check (
        (workflow_version = 'codex_d1_v2' and research_lane = 'legacy_document1')
        or (workflow_version = 'codex_global_research_v1' and research_lane = 'global_research')
        or (
          workflow_version = 'codex_market_situation_v1'
          and research_lane = 'market_situation_research'
        )
      );
  end if;
  if not exists (
    select 1 from pg_constraint
    where conname = 'codex_checkpoint_workflow_lane_check'
      and conrelid = 'doxagent.codex_workflow_checkpoints'::regclass
  ) then
    alter table doxagent.codex_workflow_checkpoints
      add constraint codex_checkpoint_workflow_lane_check check (
        (workflow_version = 'codex_d1_v2' and research_lane = 'legacy_document1')
        or (workflow_version = 'codex_global_research_v1' and research_lane = 'global_research')
        or (
          workflow_version = 'codex_market_situation_v1'
          and research_lane = 'market_situation_research'
        )
      );
  end if;
end $$;

create index if not exists idx_codex_run_registry_lane_ticker_cursor
  on doxagent.codex_run_registry
  (research_lane, ticker, created_at desc, run_id desc);

create table if not exists doxagent.codex_global_research_bundles (
  run_id text primary key references doxagent.codex_run_registry(run_id),
  ticker text not null check (ticker = upper(ticker)),
  workflow_version text not null default 'codex_global_research_v1'
    check (workflow_version = 'codex_global_research_v1'),
  research_lane text not null default 'global_research'
    check (research_lane = 'global_research'),
  status text not null check (status in ('draft','published','failed')),
  report_index jsonb not null default '{}'::jsonb check (jsonb_typeof(report_index) = 'object'),
  entity_relations jsonb not null default '[]'::jsonb
    check (jsonb_typeof(entity_relations) = 'array'),
  future_nodes jsonb not null default '[]'::jsonb
    check (jsonb_typeof(future_nodes) = 'array'),
  citation_manifest_artifact_id text,
  document_artifact_id text,
  created_at timestamptz not null default now(),
  published_at timestamptz,
  constraint codex_global_research_bundles_size check (
    octet_length(report_index::text) + octet_length(entity_relations::text)
    + octet_length(future_nodes::text) <= 524288
  ),
  constraint codex_global_research_bundles_publish_state check (
    (status = 'published' and published_at is not null and document_artifact_id is not null)
    or status <> 'published'
  )
);

create table if not exists doxagent.codex_market_situation_bundles (
  run_id text primary key references doxagent.codex_run_registry(run_id),
  ticker text not null check (ticker = upper(ticker)),
  workflow_version text not null default 'codex_market_situation_v1'
    check (workflow_version = 'codex_market_situation_v1'),
  research_lane text not null default 'market_situation_research'
    check (research_lane = 'market_situation_research'),
  status text not null check (status in ('draft','published','failed')),
  report_index jsonb not null default '{}'::jsonb check (jsonb_typeof(report_index) = 'object'),
  citation_manifest_artifact_id text,
  document_artifact_id text,
  created_at timestamptz not null default now(),
  published_at timestamptz,
  constraint codex_market_situation_bundles_size check (
    octet_length(report_index::text) <= 262144
  ),
  constraint codex_market_situation_bundles_publish_state check (
    (status = 'published' and published_at is not null and document_artifact_id is not null)
    or status <> 'published'
  )
);

alter table doxagent.codex_global_research_bundles enable row level security;
alter table doxagent.codex_global_research_bundles force row level security;
alter table doxagent.codex_market_situation_bundles enable row level security;
alter table doxagent.codex_market_situation_bundles force row level security;

revoke all on table doxagent.codex_global_research_bundles from anon, authenticated;
revoke all on table doxagent.codex_market_situation_bundles from anon, authenticated;
grant select, insert, update on table doxagent.codex_global_research_bundles to service_role;
grant select, insert, update on table doxagent.codex_market_situation_bundles to service_role;
