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

create table if not exists doxagent.stocktwits_ticker_states (
    symbol text primary key,
    enabled boolean not null default true,
    target_cadence_seconds integer not null default 300 check (target_cadence_seconds >= 30),
    hot_cadence_seconds integer not null default 90 check (hot_cadence_seconds >= 30),
    next_due_at timestamptz not null,
    last_successful_crawl_at timestamptz,
    last_seen_message_id text,
    last_seen_message_created_at timestamptz,
    current_mode text not null default 'normal'
        check (current_mode in ('normal', 'hot', 'paused')),
    latest_coverage_status text
        check (latest_coverage_status in (
            'complete', 'likely_complete', 'incomplete', 'gap_detected', 'failed'
        )),
    consecutive_gap_count integer not null default 0,
    consecutive_complete_count integer not null default 0,
    hot_started_at timestamptz,
    hot_until timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    page_size integer not null default 30 check (page_size >= 1),
    max_pages_per_crawl integer not null default 10 check (max_pages_per_crawl >= 1),
    hot_message_threshold integer not null default 80 check (hot_message_threshold >= 1),
    hot_cooldown_successes integer not null default 3 check (hot_cooldown_successes >= 1),
    bootstrap_event_policy text not null default 'live_only'
        check (bootstrap_event_policy in ('live_only', 'publish_all', 'suppress_initial'))
);

alter table if exists doxagent.stocktwits_ticker_states
    add column if not exists page_size integer not null default 30 check (page_size >= 1),
    add column if not exists max_pages_per_crawl integer not null default 10
        check (max_pages_per_crawl >= 1),
    add column if not exists hot_message_threshold integer not null default 80
        check (hot_message_threshold >= 1),
    add column if not exists hot_cooldown_successes integer not null default 3
        check (hot_cooldown_successes >= 1),
    add column if not exists bootstrap_event_policy text not null default 'live_only'
        check (bootstrap_event_policy in ('live_only', 'publish_all', 'suppress_initial'));

drop trigger if exists set_stocktwits_ticker_states_updated_at
on doxagent.stocktwits_ticker_states;
create trigger set_stocktwits_ticker_states_updated_at
before update on doxagent.stocktwits_ticker_states
for each row execute function doxagent.set_updated_at();

create table if not exists doxagent.stocktwits_messages (
    message_id text primary key,
    body text,
    created_at timestamptz,
    user_id text,
    username text,
    user_name text,
    user_avatar_url text,
    sentiment text,
    symbols jsonb not null default '[]'::jsonb,
    source_url text,
    raw_payload jsonb not null,
    first_seen_at timestamptz not null default now(),
    last_seen_at timestamptz not null default now(),
    inserted_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

drop trigger if exists set_stocktwits_messages_updated_at
on doxagent.stocktwits_messages;
create trigger set_stocktwits_messages_updated_at
before update on doxagent.stocktwits_messages
for each row execute function doxagent.set_updated_at();

create table if not exists doxagent.stocktwits_message_symbols (
    message_id text not null references doxagent.stocktwits_messages(message_id) on delete cascade,
    symbol text not null,
    first_seen_at timestamptz not null default now(),
    last_seen_at timestamptz not null default now(),
    primary key (message_id, symbol)
);

create table if not exists doxagent.stocktwits_crawl_runs (
    run_id text primary key,
    symbol text not null,
    started_at timestamptz not null,
    finished_at timestamptz,
    status text not null check (status in ('succeeded', 'failed', 'skipped')),
    fetched_count integer not null default 0,
    inserted_count integer not null default 0,
    duplicate_count integer not null default 0,
    request_count integer not null default 0,
    pages_fetched integer not null default 0,
    newest_message_id text,
    newest_message_time timestamptz,
    oldest_message_time timestamptz,
    checkpoint_message_id text,
    checkpoint_found boolean not null default false,
    coverage_status text not null check (
        coverage_status in ('complete', 'likely_complete', 'incomplete', 'gap_detected', 'failed')
    ),
    gap_reason text,
    error_code text,
    error_message text,
    mode text not null check (mode in ('normal', 'hot', 'paused')),
    rate_limited boolean not null default false,
    metadata jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now()
);

create index if not exists stocktwits_ticker_states_due_idx
on doxagent.stocktwits_ticker_states (enabled, next_due_at);

create index if not exists stocktwits_messages_created_at_idx
on doxagent.stocktwits_messages (created_at desc);

create index if not exists stocktwits_message_symbols_symbol_seen_idx
on doxagent.stocktwits_message_symbols (symbol, last_seen_at desc);

create index if not exists stocktwits_crawl_runs_symbol_started_idx
on doxagent.stocktwits_crawl_runs (symbol, started_at desc);

create index if not exists stocktwits_crawl_runs_coverage_idx
on doxagent.stocktwits_crawl_runs (coverage_status, started_at desc);
