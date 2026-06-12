create schema if not exists doxagent;

create or replace function doxagent.set_updated_at()
returns trigger
language plpgsql
as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

create table if not exists doxagent.blackboard_runs (
    run_id text primary key,
    ticker text not null,
    created_by text not null,
    workflow_state text not null,
    created_at timestamptz not null,
    updated_at timestamptz not null default now(),
    version bigint not null default 1
);

drop trigger if exists set_blackboard_runs_updated_at on doxagent.blackboard_runs;
create trigger set_blackboard_runs_updated_at
before update on doxagent.blackboard_runs
for each row execute function doxagent.set_updated_at();

create table if not exists doxagent.belief_state_snapshots (
    snapshot_id text primary key,
    run_id text not null references doxagent.blackboard_runs(run_id) on delete cascade,
    ticker text not null,
    documents jsonb not null default '{}'::jsonb,
    commit_ids jsonb not null default '[]'::jsonb,
    created_at timestamptz not null,
    updated_at timestamptz not null default now(),
    unique (run_id)
);

drop trigger if exists set_belief_state_snapshots_updated_at
on doxagent.belief_state_snapshots;
create trigger set_belief_state_snapshots_updated_at
before update on doxagent.belief_state_snapshots
for each row execute function doxagent.set_updated_at();

create table if not exists doxagent.evidence_refs (
    evidence_id text primary key,
    source_type text not null,
    source_id text not null,
    title text not null,
    summary text not null,
    retrieval_metadata jsonb not null default '{}'::jsonb,
    confidence double precision not null check (confidence >= 0 and confidence <= 1),
    citation_scope text not null,
    evidence_json jsonb not null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

drop trigger if exists set_evidence_refs_updated_at on doxagent.evidence_refs;
create trigger set_evidence_refs_updated_at
before update on doxagent.evidence_refs
for each row execute function doxagent.set_updated_at();

create table if not exists doxagent.working_memory_entries (
    entry_id text primary key,
    run_id text not null references doxagent.blackboard_runs(run_id) on delete cascade,
    ticker text not null,
    author_agent text not null,
    content_type text not null,
    payload jsonb not null default '{}'::jsonb,
    evidence_refs jsonb not null default '[]'::jsonb,
    entry_json jsonb not null,
    created_at timestamptz not null
);

create table if not exists doxagent.commit_log_entries (
    commit_id text primary key,
    run_id text not null references doxagent.blackboard_runs(run_id) on delete cascade,
    patch_id text not null,
    document_type text not null,
    object_id text,
    field_path text not null,
    author_agent text not null,
    trigger_reason text not null,
    evidence_refs jsonb not null default '[]'::jsonb,
    commit_json jsonb not null,
    created_at timestamptz not null,
    unique (run_id, patch_id)
);

create table if not exists doxagent.objections (
    objection_id text primary key,
    run_id text not null references doxagent.blackboard_runs(run_id) on delete cascade,
    source_agent text not null,
    status text not null,
    severity text not null,
    taxonomy text not null default 'general',
    dedupe_hash text,
    target_path text,
    merged_objection_ids jsonb not null default '[]'::jsonb,
    document_type text not null,
    object_id text,
    field_path text not null,
    target_json jsonb not null,
    objection_json jsonb not null,
    created_at timestamptz not null default now()
);

create table if not exists doxagent.delegations (
    delegation_id text primary key,
    run_id text not null references doxagent.blackboard_runs(run_id) on delete cascade,
    requester_agent text not null,
    target_agent text not null,
    status text not null,
    document_type text not null,
    object_id text,
    field_path text not null,
    blocking_scope_json jsonb not null,
    delegation_json jsonb not null,
    created_at timestamptz not null default now()
);

create table if not exists doxagent.workflow_checkpoints (
    checkpoint_id text primary key,
    run_id text not null references doxagent.blackboard_runs(run_id) on delete cascade,
    ticker text not null,
    status text not null,
    next_node text,
    completed_nodes jsonb not null default '[]'::jsonb,
    checkpoint_json jsonb not null,
    is_latest boolean not null default true,
    created_at timestamptz not null
);

create unique index if not exists workflow_checkpoints_one_latest_per_run
on doxagent.workflow_checkpoints (run_id)
where is_latest;

create index if not exists blackboard_runs_ticker_created_at_idx
on doxagent.blackboard_runs (ticker, created_at desc);

create index if not exists working_memory_entries_run_created_at_idx
on doxagent.working_memory_entries (run_id, created_at);

create index if not exists commit_log_entries_run_created_at_idx
on doxagent.commit_log_entries (run_id, created_at);

create index if not exists commit_log_entries_trace_idx
on doxagent.commit_log_entries (run_id, document_type, object_id, field_path);

create index if not exists objections_blocking_lookup_idx
on doxagent.objections (run_id, status, document_type, object_id, field_path);

create index if not exists objections_dedupe_lookup_idx
on doxagent.objections (run_id, status, target_path, taxonomy, dedupe_hash);

create index if not exists delegations_blocking_lookup_idx
on doxagent.delegations (run_id, status, document_type, object_id, field_path);

create index if not exists evidence_refs_source_idx
on doxagent.evidence_refs (source_type, source_id);

create index if not exists workflow_checkpoints_run_created_at_idx
on doxagent.workflow_checkpoints (run_id, created_at desc);
