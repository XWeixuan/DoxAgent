"""Promote a proven trial Profile without deleting the old Challenge or touching Backup.

Prepare the two affected identities through identities:apply with owner_scope_id=None
(which drains only their CDP controllers). scope_identity_owners remains authoritative.
This explicit, transactional migration retains the old identity in a maintenance scope;
normal scopes:apply intentionally refuses to orphan live pages. No browser is stopped.
"""
import argparse
import json
from pathlib import Path

from doxagent.site_strategy.governance_repository import GovernanceRepository
from doxagent.site_strategy.governance_models import SourceAccessScope
from doxagent.site_strategy.repository import SiteStrategyRepository
from doxagent.site_strategy.schema import utc_now


SOURCE = "source:reuters_site_search"
TRIAL = "publisher:reuters-backup2"
ARCHIVE = "publisher:reuters-retained-main-20261116"


def restore(repo, snapshot, *, source_revision, apply=False):
    """Undo an unsuccessful trial without discarding either retained scene."""
    store = GovernanceRepository(repo)
    original = {v['scope_id']: SourceAccessScope.model_validate(v) for v in snapshot['scopes']}
    with repo.transaction() as db:
        source, archive = store.get_scope(SOURCE), store.get_scope(ARCHIVE)
        before, trial = original[SOURCE], original[TRIAL]
        if (not source or not archive or source.revision != source_revision
                or source.primary_identity_id != trial.primary_identity_id
                or archive.primary_identity_id != before.primary_identity_id
                or source.fallback_identity_id != before.fallback_identity_id
                or store.get_scope(TRIAL)):
            raise ValueError('promotion state changed')
        members = [before.primary_identity_id, trial.primary_identity_id]
        for member in members:
            r = store.get_recovery(member)
            if r and r.lease_owner and r.lease_until and r.lease_until > utc_now():
                raise ValueError('recovery is in flight')
            if db.execute("select 1 from maintenance_sessions where identity_id=? and state='OPEN'", (member,)).fetchone():
                raise ValueError('Viewer is in use')
            for raw, in db.execute('select data_json from datadome_solver_jobs where identity_id=?', (member,)):
                if json.loads(raw).get('status') in {'PENDING','SUBMITTING','APPLYING'}:
                    raise ValueError('paid result is in flight')
        result = {'restored_primary': before.primary_identity_id,
                  'backup_unchanged': before.fallback_identity_id,
                  'retained_trial': trial.primary_identity_id, 'apply':apply}
        if not apply:
            return result
        plan = dict(before.verification_plan)
        plan['_routing_capability_revision'] = source.verification_plan.get('_routing_capability_revision', source.revision)
        plan['identity_trial_rollback'] = {**result, 'at':utc_now().isoformat()}
        before = before.model_copy(update={'revision':source.revision+1, 'verification_plan':plan})
        db.execute('delete from scope_identity_owners where identity_id in (?,?)', members)
        db.execute('delete from source_access_scopes where scope_id=?', (ARCHIVE,))
        for scope in (before, trial):
            db.execute('insert into source_access_scopes values(?,?,?,?,?) on conflict(scope_id) do update set '
                       'revision=excluded.revision,data_json=excluded.data_json,updated_at=excluded.updated_at',
                       (scope.scope_id, scope.source_id, scope.revision, scope.model_dump_json(), utc_now().isoformat()))
            member = scope.primary_identity_id
            db.execute('insert into scope_identity_owners values(?,?)', (member, scope.scope_id))
            r = store.get_recovery(member)
            if r:
                value = r.model_copy(update={'scope_id':scope.scope_id, 'generation':r.generation+1,
                    'automation_active':scope.scope_id==SOURCE, 'updated_at':utc_now()})
                db.execute('update browser_recoveries set scope_id=?,generation=?,data_json=?,updated_at=? where identity_id=?',
                    (scope.scope_id, value.generation, value.model_dump_json(), value.updated_at.isoformat(), member))
            for raw, in db.execute("select data_json from challenge_tasks where identity_id=? and state<>'RESOLVED'", (member,)):
                v=json.loads(raw);v['scope_id']=scope.scope_id
                db.execute('update challenge_tasks set data_json=? where task_id=?', (json.dumps(v),v['task_id']))
            for session in store.list_sessions():
                if session.identity_id == member and session.state != 'CLOSED':
                    v=session.model_copy(update={'scope_id':scope.scope_id})
                    db.execute('update maintenance_sessions set data_json=? where session_id=?', (v.model_dump_json(),v.session_id))
        return result


def promote(repo, *, source_revision, trial_revision, apply=False):
    store = GovernanceRepository(repo)
    with repo.transaction() as db:
        source, trial = store.get_scope(SOURCE), store.get_scope(TRIAL)
        if not source or not trial or (source.revision, trial.revision) != (source_revision, trial_revision):
            raise ValueError("scope revision changed")
        if (source.owner_site_id != "reuters" or trial.owner_site_id != "reuters"
                or trial.source_id or trial.fallback_identity_id or store.get_scope(ARCHIVE)):
            raise ValueError("expected one independent Reuters trial and no archive")
        old, new, backup = source.primary_identity_id, trial.primary_identity_id, source.fallback_identity_id
        if len({old, new, backup}) != 3 or not backup:
            raise ValueError("three independent identities required")
        for member, owner in ((old, SOURCE), (new, TRIAL)):
            identity = repo.get_identity(member)
            if identity is None or identity.owner_scope_id is not None:
                raise ValueError("drain affected controllers through identities:apply first")
            row = db.execute("select scope_id from scope_identity_owners where identity_id=?", (member,)).fetchone()
            if not row or row[0] != owner:
                raise ValueError("identity ownership changed")
            if db.execute("select 1 from maintenance_sessions where identity_id=? and state='OPEN'", (member,)).fetchone():
                raise ValueError("Viewer is in use")
            if db.execute("select 1 from maintenance_operations where state in ('QUEUED','RUNNING') "
                          "and json_extract(data_json,'$.identity_id')=?", (member,)).fetchone():
                raise ValueError("maintenance is in flight")
            recovery = store.get_recovery(member)
            if recovery and recovery.lease_owner and recovery.lease_until and recovery.lease_until > utc_now():
                raise ValueError("recovery is in flight; retry after it finishes")
            if member == new and recovery and recovery.phase != "RESOLVED":
                raise ValueError("trial is still blocked")
            for (raw,) in db.execute("select data_json from datadome_solver_jobs where identity_id=?", (member,)):
                if json.loads(raw).get("status") in {"PENDING", "SUBMITTING", "APPLYING"}:
                    raise ValueError("paid result is in flight")
        new_spec, backup_spec = repo.get_identity(new), repo.get_identity(backup)
        new_exit, backup_exit = repo.get_egress(new_spec.egress_id), repo.get_egress(backup_spec.egress_id)
        if (new_spec.runtime_kind != backup_spec.runtime_kind
                or new_spec.browser_release != backup_spec.browser_release
                or new_spec.environment != backup_spec.environment
                or not new_exit or not backup_exit
                or not new_exit.observed_ip or new_exit.observed_ip == backup_exit.observed_ip
                or not store.egress_region(new_exit)
                or store.egress_region(new_exit) != store.egress_region(backup_exit)):
            raise ValueError("trial must preserve runtime/environment and use a different same-region exit")
        result = {"old_primary": old, "new_primary": new, "backup_unchanged": backup,
                  "archive": ARCHIVE, "apply": apply}
        if not apply:
            return result
        now = utc_now().isoformat()
        plan = dict(source.verification_plan)
        # Keep Backup's existing proofs valid; new Primary must earn its own proof.
        plan.setdefault("_routing_capability_revision", source.revision)
        plan["identity_promotion"] = {**result, "at": now}
        promoted = source.model_copy(update={"primary_identity_id": new,
            "revision": source.revision + 1, "verification_plan": plan})
        archive_plan = {k: v for k, v in source.verification_plan.items()
                        if k not in {"crawler_routing", "_routing_capability_revision"}}
        archive_plan.update(default_publisher=False, retired_from=SOURCE)
        archive = trial.model_copy(update={"scope_id": ARCHIVE, "primary_identity_id": old,
            "revision": 1, "verification_plan": archive_plan})
        db.execute("delete from scope_identity_owners where identity_id in (?,?)", (old, new))
        db.execute("delete from source_access_scopes where scope_id=?", (TRIAL,))
        for scope in (promoted, archive):
            db.execute("insert into source_access_scopes values(?,?,?,?,?) on conflict(scope_id) do update set "
                       "revision=excluded.revision,data_json=excluded.data_json,updated_at=excluded.updated_at",
                       (scope.scope_id, scope.source_id, scope.revision, scope.model_dump_json(), now))
        db.executemany("insert into scope_identity_owners values(?,?)", [(old, ARCHIVE), (new, SOURCE)])
        for member, scope_id in ((old, ARCHIVE), (new, SOURCE)):
            recovery = store.get_recovery(member)
            if recovery:
                # Fence a recovery observation queued before the ownership change.
                update = {"scope_id": scope_id, "generation": recovery.generation + 1,
                          "updated_at": utc_now()}
                if member == old:
                    update["automation_active"] = False  # Retained scene, not a third polling member.
                value = recovery.model_copy(update=update)
                db.execute("update browser_recoveries set scope_id=?,generation=?,data_json=?,updated_at=? where identity_id=?",
                           (scope_id, value.generation, value.model_dump_json(), now, member))
            for (raw,) in db.execute("select data_json from challenge_tasks where identity_id=? and state<>'RESOLVED'", (member,)):
                value = json.loads(raw)
                value["scope_id"] = scope_id
                db.execute("update challenge_tasks set data_json=? where task_id=?", (json.dumps(value), value["task_id"]))
            for session in store.list_sessions():
                if session.identity_id == member and session.state != "CLOSED":
                    value = session.model_copy(update={"scope_id": scope_id})
                    db.execute("update maintenance_sessions set data_json=? where session_id=?", (value.model_dump_json(), value.session_id))
        return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sqlite", required=True)
    parser.add_argument("--source-revision", required=True, type=int)
    parser.add_argument("--trial-revision", type=int)
    parser.add_argument("--rollback-snapshot")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    repo = SiteStrategyRepository(args.sqlite)
    try:
        result = (restore(repo, json.loads(Path(args.rollback_snapshot).read_text()),
                  source_revision=args.source_revision, apply=args.apply)
                  if args.rollback_snapshot else promote(repo, source_revision=args.source_revision,
                  trial_revision=args.trial_revision, apply=args.apply))
        print(json.dumps(result))
    finally:
        repo.close()
