-- Hybrid Codex D1 v2 persistence. Evidence text remains in local SQLite.
create schema if not exists doxagent;

create table if not exists doxagent.codex_run_registry (
  run_id text primary key,
  ticker text not null check (ticker = upper(ticker) and octet_length(ticker) between 1 and 32),
  workflow_version text not null check (workflow_version = 'codex_d1_v2'),
  status text not null check (status in ('queued','running','failed','cancelled','published')),
  current_node text,
  completed_node_count integer not null default 0 check (completed_node_count between 0 and 64),
  failed_node_count integer not null default 0 check (failed_node_count between 0 and 64),
  latest_event_sequence bigint not null default -1 check (latest_event_sequence >= -1),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  published_at timestamptz,
  constraint codex_run_registry_publish_state check (
    (status = 'published' and published_at is not null)
    or (status <> 'published' and published_at is null)
  )
);

create index if not exists idx_codex_run_registry_ticker_cursor
  on doxagent.codex_run_registry (ticker, created_at desc, run_id desc);
create index if not exists idx_codex_run_registry_status_updated
  on doxagent.codex_run_registry (status, updated_at desc);

create table if not exists doxagent.codex_thread_registry (
  run_id text not null references doxagent.codex_run_registry(run_id),
  agent_role text not null,
  thread_id text not null,
  model text not null,
  model_provider text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (run_id, agent_role),
  constraint codex_thread_registry_size check (
    octet_length(run_id) + octet_length(agent_role) + octet_length(thread_id)
    + octet_length(model) + octet_length(coalesce(model_provider,'')) <= 4096
  )
);

create table if not exists doxagent.codex_node_attempts (
  attempt_id text primary key,
  run_id text not null references doxagent.codex_run_registry(run_id),
  node text not null,
  status text not null check (status in ('pending','running','succeeded','failed','cancelled')),
  attempt_number integer not null check (attempt_number > 0),
  thread_id text,
  input_sha256 text check (input_sha256 is null or input_sha256 ~ '^[0-9a-f]{64}$'),
  error_code text,
  error_message text check (error_message is null or octet_length(error_message) <= 4096),
  started_at timestamptz,
  completed_at timestamptz,
  created_at timestamptz not null default now(),
  unique (run_id, node, attempt_number),
  constraint codex_node_attempts_size check (
    octet_length(attempt_id) + octet_length(run_id) + octet_length(node)
    + octet_length(status) + octet_length(coalesce(thread_id,''))
    + octet_length(coalesce(input_sha256,'')) + octet_length(coalesce(error_code,''))
    + octet_length(coalesce(error_message,'')) <= 8192
  )
);

create index if not exists idx_codex_node_attempts_run_created
  on doxagent.codex_node_attempts (run_id, created_at, attempt_id);

create table if not exists doxagent.codex_workflow_checkpoints (
  run_id text primary key references doxagent.codex_run_registry(run_id),
  ticker text not null check (ticker = upper(ticker)),
  workflow_version text not null check (workflow_version = 'codex_d1_v2'),
  completed_nodes text[] not null default '{}',
  current_nodes text[] not null default '{}',
  failed_nodes text[] not null default '{}',
  cancelled boolean not null default false,
  updated_at timestamptz not null default now(),
  constraint codex_checkpoint_array_counts check (
    cardinality(completed_nodes) <= 64
    and cardinality(current_nodes) <= 64
    and cardinality(failed_nodes) <= 64
  ),
  constraint codex_checkpoint_size check (
    octet_length(to_jsonb(completed_nodes)::text)
    + octet_length(to_jsonb(current_nodes)::text)
    + octet_length(to_jsonb(failed_nodes)::text) <= 16384
  )
);

create table if not exists doxagent.codex_artifacts (
  artifact_id text primary key,
  run_id text not null references doxagent.codex_run_registry(run_id),
  node text not null,
  attempt_id text not null,
  kind text not null check (kind in ('context','report','structured_completion','manifest','audit','bundle')),
  relative_path text not null,
  sha256 text not null check (sha256 ~ '^[0-9a-f]{64}$'),
  size_bytes bigint not null check (size_bytes >= 0),
  content_type text not null,
  published boolean not null default false,
  created_at timestamptz not null default now(),
  constraint codex_artifacts_metadata_size check (
    octet_length(artifact_id) + octet_length(run_id) + octet_length(node)
    + octet_length(attempt_id) + octet_length(kind) + octet_length(relative_path)
    + octet_length(sha256) + octet_length(content_type) <= 4096
  )
);

create index if not exists idx_codex_artifacts_run_created
  on doxagent.codex_artifacts (run_id, created_at, artifact_id);

create table if not exists doxagent.codex_workflow_events (
  event_id text primary key,
  run_id text not null references doxagent.codex_run_registry(run_id),
  sequence bigint not null check (sequence >= 0),
  event_type text not null,
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  unique (run_id, sequence),
  constraint codex_workflow_events_payload_size check (octet_length(payload::text) <= 16384)
);

create table if not exists doxagent.codex_document1_bundles (
  run_id text primary key references doxagent.codex_run_registry(run_id),
  ticker text not null check (ticker = upper(ticker)),
  workflow_version text not null check (workflow_version = 'codex_d1_v2'),
  status text not null check (status in ('draft','published','failed')),
  report_index jsonb not null default '{}'::jsonb check (jsonb_typeof(report_index) = 'object'),
  entity_relations jsonb not null default '[]'::jsonb check (jsonb_typeof(entity_relations) = 'array'),
  future_nodes jsonb not null default '[]'::jsonb check (jsonb_typeof(future_nodes) = 'array'),
  citation_manifest_artifact_id text,
  document1_artifact_id text,
  created_at timestamptz not null default now(),
  published_at timestamptz,
  constraint codex_document1_bundles_size check (
    octet_length(report_index::text) + octet_length(entity_relations::text)
    + octet_length(future_nodes::text) <= 524288
  ),
  constraint codex_document1_bundles_publish_state check (
    (status = 'published' and published_at is not null and document1_artifact_id is not null)
    or status <> 'published'
  )
);

create table if not exists doxagent.codex_published_documents (
  artifact_id text primary key references doxagent.codex_artifacts(artifact_id),
  run_id text not null references doxagent.codex_run_registry(run_id),
  artifact_kind text not null check (artifact_kind in ('report','bundle','manifest')),
  sha256 text not null check (sha256 ~ '^[0-9a-f]{64}$'),
  size_bytes bigint not null check (size_bytes >= 0),
  content_type text not null,
  content_text text,
  storage_path text,
  published_at timestamptz not null,
  constraint codex_published_documents_location check (
    (content_text is not null) <> (storage_path is not null)
  ),
  constraint codex_published_documents_text_size check (
    content_text is null or (
      octet_length(content_text) <= 2097152
      and octet_length(content_text) = size_bytes
    )
  )
);

create index if not exists idx_codex_published_documents_run
  on doxagent.codex_published_documents (run_id, published_at desc, artifact_id);

alter table doxagent.codex_run_registry enable row level security;
alter table doxagent.codex_run_registry force row level security;
alter table doxagent.codex_thread_registry enable row level security;
alter table doxagent.codex_thread_registry force row level security;
alter table doxagent.codex_node_attempts enable row level security;
alter table doxagent.codex_node_attempts force row level security;
alter table doxagent.codex_workflow_checkpoints enable row level security;
alter table doxagent.codex_workflow_checkpoints force row level security;
alter table doxagent.codex_artifacts enable row level security;
alter table doxagent.codex_artifacts force row level security;
alter table doxagent.codex_workflow_events enable row level security;
alter table doxagent.codex_workflow_events force row level security;
alter table doxagent.codex_document1_bundles enable row level security;
alter table doxagent.codex_document1_bundles force row level security;
alter table doxagent.codex_published_documents enable row level security;
alter table doxagent.codex_published_documents force row level security;

revoke usage on schema doxagent from anon;
revoke usage on schema doxagent from authenticated;
grant usage on schema doxagent to service_role;

revoke all on table doxagent.codex_run_registry from anon, authenticated;
revoke all on table doxagent.codex_thread_registry from anon, authenticated;
revoke all on table doxagent.codex_node_attempts from anon, authenticated;
revoke all on table doxagent.codex_workflow_checkpoints from anon, authenticated;
revoke all on table doxagent.codex_artifacts from anon, authenticated;
revoke all on table doxagent.codex_workflow_events from anon, authenticated;
revoke all on table doxagent.codex_document1_bundles from anon, authenticated;
revoke all on table doxagent.codex_published_documents from anon, authenticated;

grant select, insert, update on table doxagent.codex_run_registry to service_role;
grant select, insert, update on table doxagent.codex_thread_registry to service_role;
grant select, insert, update on table doxagent.codex_node_attempts to service_role;
grant select, insert, update on table doxagent.codex_workflow_checkpoints to service_role;
grant select, insert, update on table doxagent.codex_artifacts to service_role;
grant select, insert, update on table doxagent.codex_workflow_events to service_role;
grant select, insert, update on table doxagent.codex_document1_bundles to service_role;
grant select, insert, update on table doxagent.codex_published_documents to service_role;
