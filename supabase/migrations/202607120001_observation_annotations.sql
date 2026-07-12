create schema if not exists doxagent;

create table if not exists doxagent.raw_tool_results (
    run_id text not null,
    task_id text not null,
    tool_call_id text not null,
    tool_name text not null,
    status text not null,
    input_payload jsonb not null default '{}'::jsonb,
    output_payload jsonb not null default '{}'::jsonb,
    raw_payload jsonb,
    output_summary text,
    created_at timestamptz not null default now(),
    primary key (run_id, task_id, tool_call_id)
);

create table if not exists doxagent.observation_blocks (
    run_id text not null,
    task_id text not null,
    block_id text not null,
    tool_call_id text not null,
    parent_block_id text,
    locator text not null,
    content jsonb not null,
    context_envelope jsonb not null default '{}'::jsonb,
    content_hash text not null,
    block_type text not null,
    metadata jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    primary key (run_id, task_id, block_id),
    foreign key (run_id, task_id, tool_call_id)
        references doxagent.raw_tool_results (run_id, task_id, tool_call_id)
        on delete cascade
);

create table if not exists doxagent.citation_annotations (
    annotation_id text primary key,
    run_id text not null,
    task_id text not null,
    result_id text not null,
    payload_path text not null,
    text_hash text not null,
    span_start integer not null check (span_start >= 0),
    span_end integer not null check (span_end >= span_start),
    observation_block_id text not null,
    created_at timestamptz not null,
    foreign key (run_id, task_id, observation_block_id)
        references doxagent.observation_blocks (run_id, task_id, block_id)
        on delete cascade
);

create table if not exists doxagent.time_annotations (
    annotation_id text primary key,
    run_id text not null,
    task_id text not null,
    result_id text not null,
    payload_path text not null,
    text_hash text not null,
    span_start integer not null check (span_start >= 0),
    span_end integer not null check (span_end >= span_start),
    occurred_at text,
    published_at text,
    created_at timestamptz not null,
    check (occurred_at is not null or published_at is not null)
);

create index if not exists idx_raw_tool_results_run_task
    on doxagent.raw_tool_results (run_id, task_id);
create index if not exists idx_observation_blocks_tool_call
    on doxagent.observation_blocks (run_id, task_id, tool_call_id);
create index if not exists idx_citation_annotations_result
    on doxagent.citation_annotations (run_id, task_id, result_id);
create index if not exists idx_time_annotations_result
    on doxagent.time_annotations (run_id, task_id, result_id);

alter table doxagent.raw_tool_results enable row level security;
alter table doxagent.observation_blocks enable row level security;
alter table doxagent.citation_annotations enable row level security;
alter table doxagent.time_annotations enable row level security;

revoke all on table doxagent.raw_tool_results from anon, authenticated;
revoke all on table doxagent.observation_blocks from anon, authenticated;
revoke all on table doxagent.citation_annotations from anon, authenticated;
revoke all on table doxagent.time_annotations from anon, authenticated;

grant select, insert, update on table doxagent.raw_tool_results to service_role;
grant select, insert, update on table doxagent.observation_blocks to service_role;
grant select, insert, update on table doxagent.citation_annotations to service_role;
grant select, insert, update on table doxagent.time_annotations to service_role;
