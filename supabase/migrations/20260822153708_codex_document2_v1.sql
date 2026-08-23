-- Compact Document2 lifecycle projection. High-frequency recovery state stays in SQLite.

alter table doxagent.codex_run_registry
  drop constraint if exists codex_run_registry_workflow_lane_check;

alter table doxagent.codex_run_registry
  add constraint codex_run_registry_workflow_lane_check check (
    (workflow_version = 'codex_d1_v2' and research_lane = 'legacy_document1')
    or (workflow_version = 'codex_global_research_v1' and research_lane = 'global_research')
    or (
      workflow_version = 'codex_market_situation_v1'
      and research_lane = 'market_situation_research'
    )
    or (workflow_version = 'codex_document2_v1' and research_lane = 'document2')
  );

create table if not exists doxagent.codex_document2_bundles (
  run_id text primary key references doxagent.codex_run_registry(run_id),
  ticker text not null check (ticker = upper(ticker)),
  source_global_run_id text not null
    references doxagent.codex_global_research_bundles(run_id),
  workflow_version text not null default 'codex_document2_v1'
    check (workflow_version = 'codex_document2_v1'),
  research_lane text not null default 'document2' check (research_lane = 'document2'),
  status text not null check (status in ('draft','published','failed')),
  publication_state text check (publication_state is null or publication_state in ('COMPLETE','PARTIAL')),
  citation_status text not null default 'UNAVAILABLE'
    check (citation_status in ('COMPLETE','PARTIAL','UNAVAILABLE')),
  citation_manifest_artifact_id text,
  document2_artifact_id text,
  is_current boolean not null default false,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  published_at timestamptz,
  constraint codex_document2_publish_state check (
    (status = 'published' and published_at is not null and document2_artifact_id is not null
      and publication_state is not null)
    or status <> 'published'
  ),
  constraint codex_document2_current_state check (
    not is_current or (status = 'published' and publication_state = 'COMPLETE')
  )
);

-- Keep this migration safe if a pre-release table was created manually.
alter table doxagent.codex_document2_bundles
  drop column if exists artifact_index,
  drop column if exists shell_outcomes,
  drop column if exists checkpoint;
alter table doxagent.codex_document2_bundles
  add column if not exists updated_at timestamptz not null default now();

create unique index if not exists uq_codex_document2_current_ticker
  on doxagent.codex_document2_bundles (ticker) where is_current;
create index if not exists idx_codex_document2_source_global
  on doxagent.codex_document2_bundles (source_global_run_id, created_at desc);
create index if not exists idx_codex_artifacts_run_path
  on doxagent.codex_artifacts (run_id, relative_path, created_at desc);

alter table doxagent.codex_document2_bundles enable row level security;
alter table doxagent.codex_document2_bundles force row level security;
revoke all on table doxagent.codex_document2_bundles from anon, authenticated;
grant select, insert, update on table doxagent.codex_document2_bundles to service_role;
