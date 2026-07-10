create schema if not exists doxagent;

create table if not exists doxagent.model_usage_events (
    event_id text primary key,
    created_at timestamptz not null,
    ticker text,
    run_id text,
    source_message_id text,
    execution_id text,
    workflow_node text,
    runtime_node text,
    agent_name text,
    task_type text,
    provider text not null,
    model text not null,
    status text not null,
    input_tokens bigint not null default 0 check (input_tokens >= 0),
    output_tokens bigint not null default 0 check (output_tokens >= 0),
    total_tokens bigint not null default 0 check (total_tokens >= 0),
    retry_count integer not null default 0 check (retry_count >= 0),
    fallback_used boolean not null default false,
    latency_seconds double precision check (latency_seconds >= 0),
    error_code text,
    error_message text
);

create index if not exists idx_model_usage_events_ticker_created
    on doxagent.model_usage_events (ticker, created_at desc);
create index if not exists idx_model_usage_events_model_created
    on doxagent.model_usage_events (model, created_at desc);
create index if not exists idx_model_usage_events_status_created
    on doxagent.model_usage_events (status, created_at desc);
create index if not exists idx_model_usage_events_run_id
    on doxagent.model_usage_events (run_id);
create index if not exists idx_model_usage_events_source_message_id
    on doxagent.model_usage_events (source_message_id);
create index if not exists idx_model_usage_events_execution_id
    on doxagent.model_usage_events (execution_id);

alter table doxagent.model_usage_events enable row level security;
revoke all on table doxagent.model_usage_events from anon, authenticated;
grant select, insert, update on table doxagent.model_usage_events to service_role;
