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

create table if not exists doxagent.run_summaries (
    run_id text primary key references doxagent.blackboard_runs(run_id) on delete cascade,
    ticker text not null,
    workflow_state text not null,
    latest_checkpoint_id text,
    latest_checkpoint_status text,
    latest_checkpoint_next_node text,
    latest_checkpoint_created_at timestamptz,
    completed_nodes jsonb not null default '[]'::jsonb,
    stable_document_types jsonb not null default '[]'::jsonb,
    working_memory_count integer not null default 0,
    commit_count integer not null default 0,
    unresolved_objection_count integer not null default 0,
    blocking_delegation_count integer not null default 0,
    evidence_ref_count integer not null default 0,
    last_error_code text,
    last_error_message_preview text,
    full_payload_ref jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

drop trigger if exists set_run_summaries_updated_at on doxagent.run_summaries;
create trigger set_run_summaries_updated_at
before update on doxagent.run_summaries
for each row execute function doxagent.set_updated_at();

create index if not exists run_summaries_ticker_updated_at_idx
on doxagent.run_summaries (ticker, updated_at desc);

create index if not exists run_summaries_checkpoint_status_idx
on doxagent.run_summaries (latest_checkpoint_status, updated_at desc);

create or replace function doxagent.prune_workflow_checkpoint_history(
    max_checkpoints_per_run integer default 3
)
returns integer
language plpgsql
as $$
declare
    deleted_count integer;
begin
    if max_checkpoints_per_run < 1 then
        raise exception 'max_checkpoints_per_run must be >= 1';
    end if;

    with ranked as (
        select
            checkpoint_id,
            row_number() over (
                partition by run_id
                order by is_latest desc, created_at desc, checkpoint_id desc
            ) as rn
        from doxagent.workflow_checkpoints
    ),
    deleted as (
        delete from doxagent.workflow_checkpoints wc
        using ranked r
        where wc.checkpoint_id = r.checkpoint_id
          and r.rn > max_checkpoints_per_run
        returning wc.checkpoint_id
    )
    select count(*) into deleted_count from deleted;

    return deleted_count;
end;
$$;

