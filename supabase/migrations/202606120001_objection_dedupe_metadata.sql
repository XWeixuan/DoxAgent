alter table if exists doxagent.objections
    add column if not exists taxonomy text not null default 'general',
    add column if not exists dedupe_hash text,
    add column if not exists target_path text,
    add column if not exists merged_objection_ids jsonb not null default '[]'::jsonb;

create index if not exists objections_dedupe_lookup_idx
on doxagent.objections (run_id, status, target_path, taxonomy, dedupe_hash);
