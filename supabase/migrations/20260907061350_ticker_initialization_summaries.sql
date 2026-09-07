-- Low-frequency, server-only projection. SQLite remains the execution truth.
create table if not exists public.ticker_initialization_summaries (
    initialization_id text primary key check (length(initialization_id) <= 160),
    ticker text not null check (length(ticker) <= 32),
    state_seq bigint not null check (state_seq >= 0),
    status text not null check (status in ('QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED')),
    phase text not null check (length(phase) <= 64),
    created_at timestamptz not null,
    updated_at timestamptz not null,
    manual_resume_required boolean not null,
    has_error boolean not null,
    operation_kind text not null default 'INITIALIZE' check (length(operation_kind) <= 64),
    failed_nodes jsonb not null default '[]' check (octet_length(failed_nodes::text) <= 2048),
    diagnostics_count integer not null default 0 check (diagnostics_count >= 0),
    activation_manifest jsonb not null default '{}' check (octet_length(activation_manifest::text) <= 4096),
    last_operator_action jsonb not null default '{}' check (octet_length(last_operator_action::text) <= 1024)
);
create index if not exists ticker_initialization_summaries_ticker_updated
    on public.ticker_initialization_summaries (ticker, updated_at desc);
alter table public.ticker_initialization_summaries enable row level security;
revoke all on public.ticker_initialization_summaries from public, anon, authenticated;
grant select, insert, update on public.ticker_initialization_summaries to service_role;

create or replace function public.upsert_ticker_initialization_summary(summary jsonb)
returns void language sql security invoker set search_path = '' as $$
    insert into public.ticker_initialization_summaries (
        initialization_id, ticker, state_seq, status, phase, created_at, updated_at,
        manual_resume_required, has_error, operation_kind, failed_nodes, diagnostics_count,
        activation_manifest, last_operator_action
    ) values (
        summary->>'initialization_id', summary->>'ticker', (summary->>'state_seq')::bigint,
        summary->>'status', summary->>'phase', (summary->>'created_at')::timestamptz,
        (summary->>'updated_at')::timestamptz,
        (summary->>'manual_resume_required')::boolean, (summary->>'has_error')::boolean,
        coalesce(summary->>'operation_kind', 'INITIALIZE'), coalesce(summary->'failed_nodes', '[]'),
        coalesce((summary->>'diagnostics_count')::integer, 0),
        coalesce(summary->'activation_manifest', '{}'), coalesce(summary->'last_operator_action', '{}')
    ) on conflict (initialization_id) do update set
        state_seq = excluded.state_seq, status = excluded.status, phase = excluded.phase,
        updated_at = excluded.updated_at, manual_resume_required = excluded.manual_resume_required,
        has_error = excluded.has_error, operation_kind = excluded.operation_kind,
        failed_nodes = excluded.failed_nodes, diagnostics_count = excluded.diagnostics_count,
        activation_manifest = excluded.activation_manifest, last_operator_action = excluded.last_operator_action
    where public.ticker_initialization_summaries.state_seq < excluded.state_seq;
$$;
revoke all on function public.upsert_ticker_initialization_summary(jsonb)
    from public, anon, authenticated;
grant execute on function public.upsert_ticker_initialization_summary(jsonb) to service_role;
