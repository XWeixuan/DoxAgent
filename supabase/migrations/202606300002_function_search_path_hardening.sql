-- Harden DoxAgent helper functions flagged by Supabase security advisors.
-- This keeps function name resolution stable without changing table shape.

do $$
begin
    if to_regprocedure('doxagent.set_updated_at()') is not null then
        execute 'alter function doxagent.set_updated_at() set search_path = doxagent, pg_temp';
    end if;

    if to_regprocedure('doxagent.prune_workflow_checkpoint_history(integer)') is not null then
        execute 'alter function doxagent.prune_workflow_checkpoint_history(integer) set search_path = doxagent, pg_temp';
    end if;
end
$$;
