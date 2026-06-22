alter table if exists doxagent.objections
    drop constraint if exists objections_pkey;

alter table if exists doxagent.objections
    add constraint objections_pkey primary key (run_id, objection_id);

create index if not exists objections_objection_id_idx
on doxagent.objections (objection_id);
