"""Short, owner-only transactions for source scopes and browser ownership.

Schema creation is explicitly called by the Registry's startup migration, never
by inventory readers or a browser control worker.
"""

from contextlib import nullcontext
from datetime import datetime, timedelta
from urllib.parse import urlsplit, urlunsplit

from .governance_models import (
    BrowserRecovery,
    ChallengeTask,
    MaintenanceOperation,
    MaintenanceSession,
    OwnedBrowserTarget,
    SourceAccessScope,
)
from .schema import utc_now


def migrate_governance(connection):
    connection.executescript("""
        create table if not exists source_access_scopes (
            scope_id text primary key, source_id text unique, revision integer not null,
            data_json text not null, updated_at text not null);
        create table if not exists scope_identity_owners (
            identity_id text primary key, scope_id text not null,
            foreign key(scope_id) references source_access_scopes(scope_id));
        create table if not exists egress_regions (
            egress_id text primary key, observed_ip text not null,
            country_code text not null, observed_at text not null);
        create table if not exists challenge_tasks (
            task_id text primary key, identity_id text not null, state text not null,
            superseded_by text, data_json text not null, updated_at text not null);
        create unique index if not exists challenge_current_identity
            on challenge_tasks(identity_id) where state <> 'RESOLVED' and superseded_by is null;
        create index if not exists challenge_state_time on challenge_tasks(state, updated_at);
        create table if not exists owned_browser_targets (
            allocation_id text primary key, identity_id text not null,
            browser_instance_id text not null, target_id text,
            owner_kind text not null, state text not null,
            data_json text not null, updated_at text not null);
        create unique index if not exists owned_target_instance
            on owned_browser_targets(browser_instance_id, target_id) where target_id is not null;
        create index if not exists owned_target_cleanup on owned_browser_targets(state,updated_at);
        create table if not exists maintenance_sessions (
            session_id text primary key, identity_id text not null, state text not null,
            data_json text not null, updated_at text not null);
        create unique index if not exists maintenance_viewer_seat
            on maintenance_sessions((1)) where state='OPEN';
        create table if not exists maintenance_operations (
            operation_id text primary key, idempotency_key text not null unique,
            session_id text, state text not null, data_json text not null,
            updated_at text not null);
        create unique index if not exists maintenance_session_mutation
            on maintenance_operations(session_id)
            where session_id is not null and state in ('QUEUED','RUNNING');
        create index if not exists maintenance_operation_retention
            on maintenance_operations(state,updated_at);
        create table if not exists browser_recoveries (
            identity_id text primary key, scope_id text not null, phase text not null,
            next_due_at text, generation integer not null, data_json text not null,
            updated_at text not null);
        create index if not exists browser_recovery_due on browser_recoveries(phase,next_due_at);
        create table if not exists site_auto_login_attempts (
            credential_id text primary key, attempted_at text not null, result text not null);
        create table if not exists site_recovery_migrations (
            name text primary key, applied_at text not null);
        create table if not exists datadome_solver_jobs (
            job_id text primary key, recovery_id text not null, identity_id text not null,
            created_at text not null, updated_at text not null, data_json text not null);
        create index if not exists datadome_solver_incident on datadome_solver_jobs(recovery_id,created_at);
    """)


def safe_display_url(url: str) -> str:
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.hostname or "", parsed.path, "", ""))


def capability_fingerprint(repository, scope, identity, egress):
    from .schema import digest_json

    site = repository.get_strategy(scope.owner_site_id)
    return digest_json(
        [
            identity.model_dump(mode="json"),
            egress.generation,
            egress.observed_ip,
            scope.revision,
            site.revision if site else 0,
            scope.verification_plan.get("contract", "body_v2.2/listing_v1"),
            repository.get_identity_runtime(identity.identity_id).session_revision,
        ]
    )


class GovernanceRepository:
    def __init__(self, repository):
        self.repository = repository

    def claim_auto_login(self, credential_id):
        """One account attempt per 30 minutes, shared by its primary/fallback."""
        now = utc_now()
        with self.repository.transaction() as connection:
            row = connection.execute(
                "select attempted_at from site_auto_login_attempts where credential_id=?",
                (credential_id,),
            ).fetchone()
            if row and datetime.fromisoformat(row[0]) > now - timedelta(minutes=30):
                return False
            connection.execute(
                "insert into site_auto_login_attempts values(?,?,?) on conflict(credential_id) "
                "do update set attempted_at=excluded.attempted_at,result=excluded.result",
                (credential_id, now.isoformat(), "STARTED"),
            )
            return True

    def finish_auto_login(self, credential_id, result):
        with self.repository.transaction() as connection:
            connection.execute(
                "update site_auto_login_attempts set result=? where credential_id=?",
                (result, credential_id),
            )

    def _read(self, table, key, value, model):
        with self.repository._lock:
            row = self.repository._connection.execute(
                f"select data_json from {table} where {key}=?", (value,)
            ).fetchone()
        return model.model_validate_json(row[0]) if row else None

    def get_scope(self, scope_id: str) -> SourceAccessScope | None:
        return self._read("source_access_scopes", "scope_id", scope_id, SourceAccessScope)

    def maintenance_scope(self, scope_id):
        """Reader-first maintenance compatibility before source routing is activated."""
        configured = self.get_scope(scope_id)
        if configured or self.list_scopes() or not scope_id.startswith("publisher:"):
            return configured
        site_id = scope_id.removeprefix("publisher:")
        spec = self.repository.get_strategy(site_id)
        if not spec:
            return None
        members = list(
            dict.fromkeys(
                c.identity_id or c.profile_id
                for c in sorted(spec.access.combinations, key=lambda c: c.priority)
                if c.enabled and self.repository.get_identity(c.identity_id or c.profile_id or "")
            )
        )
        if not members:
            return None
        return SourceAccessScope(
            scope_id=scope_id,
            owner_site_id=site_id,
            revision=spec.revision,
            primary_identity_id=members[0],
            fallback_identity_id=members[1] if len(members) > 1 else None,
            verification_plan={
                "legacy_runtime_key": site_id,
                "body_url": spec.auth.verification_url,
            },
        )

    def list_scopes(self) -> list[SourceAccessScope]:
        with self.repository._lock:
            rows = self.repository._connection.execute(
                "select data_json from source_access_scopes order by scope_id"
            ).fetchall()
        return [SourceAccessScope.model_validate_json(row[0]) for row in rows]

    def egress_region(self, egress):
        with self.repository._lock:
            row = self.repository._connection.execute(
                "select country_code from egress_regions where egress_id=? and observed_ip=?",
                (egress.egress_id, egress.observed_ip),
            ).fetchone()
        return row[0] if row else None

    def apply_scope(self, scope: SourceAccessScope, expected_revision: int | None):
        members = [scope.primary_identity_id]
        if scope.fallback_identity_id:
            members.append(scope.fallback_identity_id)
        with self.repository.transaction() as connection:
            row = connection.execute(
                "select revision from source_access_scopes where scope_id=?", (scope.scope_id,)
            ).fetchone()
            current = row[0] if row else None
            if current != expected_revision:
                raise RuntimeError("source scope revision conflict")
            previous_scope = self.get_scope(scope.scope_id)
            previous_members = (
                {previous_scope.primary_identity_id, previous_scope.fallback_identity_id}
                if previous_scope
                else set()
            )
            for removed in previous_members - set(members) - {None}:
                live = connection.execute(
                    "select 1 from owned_browser_targets where identity_id=? "
                    "and state not in ('CLOSED','LOST') limit 1",
                    (removed,),
                ).fetchone()
                if (
                    live
                    or self.current_challenge(removed)
                    or any(session.identity_id == removed for session in self.list_sessions())
                ):
                    raise ValueError(
                        "cannot transfer an identity with live owned pages or sessions"
                    )
            identities = [self.repository.get_identity(member) for member in members]
            if any(item is None for item in identities):
                raise ValueError("scope references an unknown identity")
            primary = identities[0]
            primary_egress = self.repository.get_egress(primary.egress_id)
            for identity in identities:
                if identity.owner_scope_id not in (None, scope.scope_id):
                    raise ValueError("identity belongs to another scope")
                if (
                    identity.runtime_kind,
                    identity.browser_release,
                    identity.environment,
                    identity.access,
                ) != (
                    primary.runtime_kind,
                    primary.browser_release,
                    primary.environment,
                    primary.access,
                ):
                    raise ValueError("fallback must use the same runtime and environment")
                egress = self.repository.get_egress(identity.egress_id)
                if (
                    primary_egress
                    and egress
                    and ((primary_egress.node_ref == "DIRECT") != (egress.node_ref == "DIRECT"))
                ):
                    raise ValueError("fallback must preserve the proxy/direct access class")
            if len({item.profile_id for item in identities}) != len(members):
                raise ValueError("scope members must use independent profiles")
            for member in members:
                owner = connection.execute(
                    "select scope_id from scope_identity_owners where identity_id=?", (member,)
                ).fetchone()
                if owner and owner[0] != scope.scope_id:
                    raise ValueError(f"identity {member} already owned by {owner[0]}")
            stored = scope.model_copy(update={"revision": (current or 0) + 1})
            connection.execute(
                "insert into source_access_scopes values(?,?,?,?,?) "
                "on conflict(scope_id) do update set source_id=excluded.source_id,"
                "revision=excluded.revision,data_json=excluded.data_json,updated_at=excluded.updated_at",
                (
                    stored.scope_id,
                    stored.source_id,
                    stored.revision,
                    stored.model_dump_json(),
                    utc_now().isoformat(),
                ),
            )
            connection.execute(
                "delete from scope_identity_owners where scope_id=?", (scope.scope_id,)
            )
            connection.executemany(
                "insert into scope_identity_owners values(?,?)",
                [(member, scope.scope_id) for member in members],
            )
        return stored

    def save_target(self, target: OwnedBrowserTarget):
        with self.repository.transaction() as connection:
            old = connection.execute(
                "select data_json from owned_browser_targets where allocation_id=?",
                (target.allocation_id,),
            ).fetchone()
            if old:
                previous = OwnedBrowserTarget.model_validate_json(old[0])
                if (previous.identity_id, previous.browser_instance_id, previous.worker_epoch) != (
                    target.identity_id,
                    target.browser_instance_id,
                    target.worker_epoch,
                ):
                    raise ValueError("target allocation instance/epoch is immutable")
                if previous.target_id and target.target_id != previous.target_id:
                    raise ValueError("target id is immutable after binding")
                if previous.owner_kind != "BUSINESS" and target.owner_kind == "BUSINESS":
                    raise ValueError("human-owned targets cannot return to business cleanup")
            self._write_target(connection, target)
        return target

    @staticmethod
    def _write_target(connection, target):
        connection.execute(
            "insert into owned_browser_targets values(?,?,?,?,?,?,?,?) "
            "on conflict(allocation_id) do update set target_id=excluded.target_id,"
            "owner_kind=excluded.owner_kind,state=excluded.state,data_json=excluded.data_json,"
            "updated_at=excluded.updated_at",
            (
                target.allocation_id,
                target.identity_id,
                target.browser_instance_id,
                target.target_id,
                target.owner_kind,
                target.state,
                target.model_dump_json(),
                utc_now().isoformat(),
            ),
        )

    def get_target(self, allocation_id):
        return self._read(
            "owned_browser_targets", "allocation_id", allocation_id, OwnedBrowserTarget
        )

    def mark_instance_lost(self, identity_id, instance_id):
        """Only called after independent proof that this native instance is gone."""
        if not instance_id:
            return
        with self.repository.transaction() as connection:
            rows = connection.execute(
                "select data_json from owned_browser_targets where identity_id=? "
                "and browser_instance_id=? and state not in ('CLOSED','LOST')",
                (identity_id, instance_id),
            ).fetchall()
            for row in rows:
                target = OwnedBrowserTarget.model_validate_json(row[0])
                self._write_target(connection, target.model_copy(update={"state": "LOST"}))
            task = self.current_challenge(identity_id)
            if task and task.browser_instance_id == instance_id:
                self._write_task(connection, task.model_copy(update={"state": "TARGET_LOST"}))

    def current_challenge(self, identity_id):
        with self.repository._lock:
            row = self.repository._connection.execute(
                "select data_json from challenge_tasks where identity_id=? "
                "and state<>'RESOLVED' and superseded_by is null",
                (identity_id,),
            ).fetchone()
        return ChallengeTask.model_validate_json(row[0]) if row else None

    def get_challenge(self, task_id):
        return self._read("challenge_tasks", "task_id", task_id, ChallengeTask)

    def handoff_login(self, allocation_id, token, site_id):
        """Commit the login reference before acknowledging a newly visible human page."""
        with self.repository.transaction() as connection:
            target = self.get_target(allocation_id)
            if not target or target.owner_kind == "CHALLENGE":
                raise ValueError("login target ownership mismatch")
            session = next((s for s in self.list_sessions() if s.control_token == token), None)
            if not session:
                scope = next(
                    (
                        s
                        for s in self.list_scopes()
                        if target.identity_id in (s.primary_identity_id, s.fallback_identity_id)
                    ),
                    None,
                )
                session = MaintenanceSession(
                    kind="LOGIN",
                    identity_id=target.identity_id,
                    scope_id=scope.scope_id if scope else f"publisher:{site_id}",
                    site_id=site_id,
                    control_token=token,
                )
            if session.identity_id != target.identity_id or session.site_id != site_id:
                raise ValueError("login session identity mismatch")
            self._write_target(
                connection,
                target.model_copy(
                    update={
                        "owner_kind": "LOGIN",
                        "owner_ref": session.session_id,
                    }
                ),
            )
            session = self._save_session(
                connection,
                session.model_copy(
                    update={
                        "allocation_id": target.allocation_id,
                        "target_id": target.target_id,
                        "browser_instance_id": target.browser_instance_id,
                    }
                ),
            )
        return target.model_copy(update={"owner_kind": "LOGIN", "owner_ref": session.session_id})

    def list_challenges(self, *, offset=0, limit=100, include_resolved=False):
        with self.repository._lock:
            rows = self.repository._connection.execute(
                "select data_json from challenge_tasks "
                + ("" if include_resolved else "where state<>'RESOLVED' and superseded_by is null ")
                + "order by updated_at desc limit ? offset ?",
                (min(limit, 200), max(offset, 0)),
            ).fetchall()
        return [ChallengeTask.model_validate_json(row[0]) for row in rows]

    def handoff_challenge(self, task: ChallengeTask, *, session=None, _connection=None):
        """Task + ownership commit together, before a business permit is released."""
        with (
            nullcontext(_connection) if _connection is not None else self.repository.transaction()
        ) as connection:
            existing = self.current_challenge(task.identity_id)
            if existing and existing.state != "TARGET_LOST":
                return existing
            target = self.get_target(task.allocation_id)
            if target is None or (
                target.identity_id,
                target.browser_instance_id,
                target.target_id,
            ) != (task.identity_id, task.browser_instance_id, task.original_target_id):
                raise ValueError("challenge does not own its original target")
            if target.state != "LIVE" or target.owner_kind not in {"BUSINESS", "LOGIN", "RECOVERY"}:
                raise RuntimeError("OWNERSHIP_HANDOFF_FAILED")
            stored = task.model_copy(update={"original_url": safe_display_url(task.original_url)})
            if existing:
                self._write_task(
                    connection, existing.model_copy(update={"superseded_by": stored.task_id})
                )
                stored = stored.model_copy(update={"predecessor_task_id": existing.task_id})
            self._write_target(
                connection,
                target.model_copy(
                    update={
                        "owner_kind": "CHALLENGE",
                        "owner_ref": stored.task_id,
                        "updated_at": utc_now(),
                    }
                ),
            )
            self._write_task(connection, stored)
            identity = self.repository.get_identity(stored.identity_id)
            profile = self.repository.get_profile(identity.profile_id) if identity else None
            if profile is not None:
                from .schema import ProfileOperationalState

                # A queued human Task is not an active Viewer. Shared recovery
                # independently gates each unproven purpose; do not freeze the
                # entire Profile (and its already-proven crawler) on escalation.
                passive_recovery = bool(
                    task.recovery_id
                    and not session
                    and not any(
                        s.identity_id == stored.identity_id and s.state == "OPEN"
                        for s in self.list_sessions()
                    )
                )
                blocked = profile.model_copy(
                    update={
                        "operational_state": (
                            ProfileOperationalState.AVAILABLE
                            if passive_recovery
                            else ProfileOperationalState.MAINTENANCE
                        ),
                        "operational_revision": profile.operational_revision + 1,
                        "maintenance_session_id": session.control_token
                        if session
                        else None
                        if passive_recovery
                        else stored.task_id,
                        # Compatibility projection, not an independently editable task.
                        "challenge_url": task.original_url,
                        "challenge_site_id": stored.site_id,
                        "challenge_identity_id": stored.identity_id,
                        "challenge_combination_id": stored.combination_id or stored.identity_id,
                        "challenge_target_id": stored.original_target_id,
                        "challenge_verified": False,
                        "updated_at": utc_now(),
                    }
                )
                connection.execute(
                    "update browser_profiles set data_json=?,updated_at=? where profile_id=?",
                    (blocked.model_dump_json(), utc_now().isoformat(), profile.profile_id),
                )
            if session:
                session = session.model_copy(
                    update={"kind": "CHALLENGE", "task_id": stored.task_id}
                )
                connection.execute(
                    "update maintenance_sessions set data_json=?,updated_at=? where session_id=?",
                    (session.model_dump_json(), utc_now().isoformat(), session.session_id),
                )
        return stored

    @staticmethod
    def _write_task(connection, task):
        connection.execute(
            "insert into challenge_tasks values(?,?,?,?,?,?) "
            "on conflict(task_id) do update set state=excluded.state,"
            "superseded_by=excluded.superseded_by,data_json=excluded.data_json,"
            "updated_at=excluded.updated_at",
            (
                task.task_id,
                task.identity_id,
                task.state,
                task.superseded_by,
                task.model_dump_json(),
                utc_now().isoformat(),
            ),
        )

    def save_challenge(self, task):
        with self.repository.transaction() as connection:
            previous = self._read("challenge_tasks", "task_id", task.task_id, ChallengeTask)
            if previous and (
                (previous.state == "RESOLVED" and task.state != "RESOLVED")
                or (
                    previous.superseded_by is not None
                    and task.superseded_by != previous.superseded_by
                )
            ):
                raise RuntimeError("stale challenge observation cannot reopen a terminal task")
            if previous is None or (
                previous.original_target_id,
                previous.browser_instance_id,
                previous.allocation_id,
            ) != (task.original_target_id, task.browser_instance_id, task.allocation_id):
                raise ValueError("original challenge ownership is immutable")
            self._write_task(connection, task)
        return task

    def replace_lost_challenge(self, previous, replacement):
        from .schema import ProfileOperationalState

        with self.repository.transaction() as connection:
            old = self.current_challenge(previous.identity_id)
            if old is None or old.task_id != previous.task_id or old.state != "TARGET_LOST":
                raise RuntimeError("only the current lost challenge can be replaced")
            target = self.get_target(replacement.allocation_id)
            if target is None or target.identity_id != old.identity_id:
                raise ValueError("replacement target belongs to another identity")
            if target.target_id != replacement.original_target_id:
                raise ValueError("replacement target id mismatch")
            self._write_task(
                connection, old.model_copy(update={"superseded_by": replacement.task_id})
            )
            replacement = replacement.model_copy(
                update={
                    "predecessor_task_id": old.task_id,
                    "original_url": safe_display_url(replacement.original_url),
                }
            )
            self._write_target(
                connection,
                target.model_copy(
                    update={
                        "owner_kind": "CHALLENGE",
                        "owner_ref": replacement.task_id,
                    }
                ),
            )
            self._write_task(connection, replacement)
            identity = self.repository.get_identity(old.identity_id)
            profile = self.repository.get_profile(identity.profile_id)
            profile = profile.model_copy(
                update={
                    "maintenance_session_id": replacement.task_id,
                    "operational_state": ProfileOperationalState.MAINTENANCE,
                    "challenge_url": replacement.original_url,
                    "challenge_target_id": replacement.original_target_id,
                    "challenge_site_id": replacement.site_id,
                    "challenge_identity_id": replacement.identity_id,
                    "challenge_verified": False,
                }
            )
            connection.execute(
                "update browser_profiles set data_json=?,updated_at=? where profile_id=?",
                (profile.model_dump_json(), utc_now().isoformat(), profile.profile_id),
            )
        return replacement

    def save_session(self, session: MaintenanceSession):
        with self.repository.transaction() as connection:
            return self._save_session(connection, session)

    def _save_session(self, connection, session):
        if session.state == "OPEN":
            occupied = connection.execute(
                "select data_json from maintenance_sessions where state='OPEN' and session_id<>?",
                (session.session_id,),
            ).fetchone()
            if occupied:
                active = MaintenanceSession.model_validate_json(occupied[0])
                raise ValueError(
                    f"Viewer is in use by {active.identity_id}. Choose Later / Release Viewer "
                    "before opening another identity; its page will be preserved."
                )
        previous = self.get_session(session.session_id)
        if previous:
            if previous.generation != session.generation:
                raise RuntimeError("stale maintenance session generation")
            changed = (previous.state, previous.target_id, previous.browser_instance_id) != (
                session.state,
                session.target_id,
                session.browser_instance_id,
            )
            session = session.model_copy(
                update={
                    "generation": previous.generation + int(changed),
                    "updated_at": utc_now(),
                }
            )
        connection.execute(
            "insert into maintenance_sessions values(?,?,?,?,?) "
            "on conflict(session_id) do update set state=excluded.state,"
            "data_json=excluded.data_json,updated_at=excluded.updated_at",
            (
                session.session_id,
                session.identity_id,
                session.state,
                session.model_dump_json(),
                utc_now().isoformat(),
            ),
        )
        return session

    def get_session(self, session_id):
        return self._read("maintenance_sessions", "session_id", session_id, MaintenanceSession)

    def list_sessions(self):
        with self.repository._lock:
            rows = self.repository._connection.execute(
                "select data_json from maintenance_sessions where state<>'CLOSED'"
            ).fetchall()
        return [MaintenanceSession.model_validate_json(row[0]) for row in rows]

    def save_operation(self, operation: MaintenanceOperation):
        with self.repository.transaction() as connection:
            existing = self._read(
                "maintenance_operations",
                "idempotency_key",
                operation.idempotency_key,
                MaintenanceOperation,
            )
            if existing and existing.operation_id != operation.operation_id:
                requested = operation.model_dump(
                    include=set(MaintenanceOperation.__bases__[0].model_fields)
                )
                previous = existing.model_dump(
                    include=set(MaintenanceOperation.__bases__[0].model_fields)
                )
                if requested != previous:
                    raise ValueError("idempotency key reused for a different command")
                return existing
            connection.execute(
                "insert into maintenance_operations values(?,?,?,?,?,?) "
                "on conflict(operation_id) do update set state=excluded.state,"
                "data_json=excluded.data_json,updated_at=excluded.updated_at",
                (
                    operation.operation_id,
                    operation.idempotency_key,
                    operation.session_id,
                    operation.state,
                    operation.model_dump_json(),
                    utc_now().isoformat(),
                ),
            )
        return operation

    def get_operation(self, operation_id):
        return self._read(
            "maintenance_operations", "operation_id", operation_id, MaintenanceOperation
        )

    def commit_verification(
        self,
        scope,
        identity_id,
        report,
        *,
        session=None,
        credential_change=False,
        passive_task=None,
        _connection=None,
        recovering_purpose=None,
    ):
        """Proof, relevant gates and current Task resolve in one short owner transaction."""
        from .schema import (
            AuthState,
            CombinationRuntime,
            SiteIdentityAuth,
            digest_json,
        )

        now = utc_now()
        with (
            nullcontext(_connection) if _connection is not None else self.repository.transaction()
        ) as connection:
            current_scope = self.maintenance_scope(scope.scope_id)
            if current_scope is None or current_scope.revision != scope.revision:
                raise RuntimeError("scope changed during verification")
            if passive_task is not None:
                current = self.current_challenge(identity_id)
                target = self.get_target(passive_task.allocation_id)
                sessions = self.list_sessions()
                if (
                    scope.source_id != "investing_ticker_news"
                    or not current_scope.enabled
                    or passive_task.purpose != "CRAWLER"
                    or not current
                    or (
                        current.task_id,
                        current.browser_instance_id,
                        current.original_target_id,
                        current.allocation_id,
                        current.original_url,
                    )
                    != (
                        passive_task.task_id,
                        passive_task.browser_instance_id,
                        passive_task.original_target_id,
                        passive_task.allocation_id,
                        passive_task.original_url,
                    )
                    or current.state != "WAITING_HUMAN"
                    or current.scope_id != scope.scope_id
                    or current.site_id != scope.owner_site_id
                    or not target
                    or target.state != "LIVE"
                    or target.owner_kind != "CHALLENGE"
                    or target.owner_ref != current.task_id
                    or (target.identity_id, target.browser_instance_id, target.target_id)
                    != (identity_id, current.browser_instance_id, current.original_target_id)
                    or report.runtime_instance_id != current.browser_instance_id
                    or report.challenge != "CLEARED"
                    or report.crawler != "READY"
                    or report.original_page_kind != "listing"
                    or report.verification_url != current.original_url
                    or any(s.identity_id == identity_id and s.state == "OPEN" for s in sessions)
                ):
                    raise RuntimeError("passive challenge proof became stale")
                session = next(
                    (s for s in sessions if s.task_id == current.task_id and s.state == "RELEASED"),
                    None,
                )
                if session is None:
                    session = self._save_session(
                        connection,
                        MaintenanceSession(
                            kind="CHALLENGE",
                            state="RELEASED",
                            scope_id=scope.scope_id,
                            site_id=current.site_id,
                            identity_id=identity_id,
                            task_id=current.task_id,
                            control_token=current.task_id,
                            allocation_id=target.allocation_id,
                            target_id=target.target_id,
                            browser_instance_id=target.browser_instance_id,
                        ),
                    )
            if session:
                current_session = self.get_session(session.session_id)
                if (
                    not current_session
                    or current_session.generation != session.generation
                    or current_session.state == "CLOSED"
                ):
                    raise RuntimeError("maintenance session changed during verification")
            identity = self.repository.get_identity(identity_id)
            egress = self.repository.get_egress(identity.egress_id)
            runtime = self.repository.get_identity_runtime(identity_id)
            if credential_change and session and not session.credential_event_committed:
                runtime.session_revision += 1
                runtime.generation += 1
                connection.execute(
                    "insert into identity_runtime values(?,?,?,?) "
                    "on conflict(identity_id) do update set "
                    "data_json=excluded.data_json,generation=excluded.generation,updated_at=excluded.updated_at",
                    (identity_id, runtime.model_dump_json(), runtime.generation, now.isoformat()),
                )
                session = session.model_copy(update={"credential_event_committed": True})
                connection.execute(
                    "update maintenance_sessions set data_json=? where session_id=?",
                    (session.model_dump_json(), session.session_id),
                )
            fingerprint = capability_fingerprint(self.repository, scope, identity, egress)
            runtime_key = scope.verification_plan.get(
                "legacy_runtime_key", f"scope:{scope.scope_id}"
            )
            combination_id = identity_id
            if scope.verification_plan.get("legacy_runtime_key"):
                spec = self.repository.get_strategy(scope.owner_site_id)
                combination_id = next(
                    (
                        c.combination_id
                        for c in spec.access.combinations
                        if (c.identity_id or c.profile_id) == identity_id
                    ),
                    identity_id,
                )
            state = self.repository.get_runtime(runtime_key)
            member = state.combinations.setdefault(combination_id, CombinationRuntime())
            for purpose, result in (("CRAWLER", report.crawler), ("BODY", report.body)):
                if result != "NOT_TESTED":
                    previous = member.capabilities.get(purpose, {})
                    if previous.get("fingerprint") not in {None, fingerprint}:
                        previous = {}
                    samples = list(previous.get("article_samples", []))
                    subscription_samples = list(previous.get("subscription_samples", []))
                    if (
                        purpose == "BODY"
                        and result in {"FULL", "SHORT_FULL"}
                        and report.verification_url
                    ):
                        sample = digest_json(safe_display_url(report.verification_url))
                        if sample not in samples:
                            samples = [*samples, sample][-2:]
                        if report.authentication == "VALID" and sample not in subscription_samples:
                            subscription_samples = [*subscription_samples, sample][-2:]
                    member.capabilities[purpose] = {
                        "result": result,
                        "authentication": report.authentication,
                        "fingerprint": fingerprint,
                        "checked_at": now.isoformat(),
                        "contract": scope.verification_plan.get("contract", "body_v2.2/listing_v1"),
                        "article_samples": samples,
                        "subscription_samples": subscription_samples,
                        "instance_id": report.runtime_instance_id,
                        "last_url": safe_display_url(report.verification_url)
                        if report.verification_url
                        else previous.get("last_url"),
                        "state": "VERIFIED_WARM"
                        if result in {"READY", "FULL", "SHORT_FULL"}
                        else "UNVERIFIED",
                    }
            if report.challenge == "CLEARED":
                task = self.current_challenge(identity_id)
                task_cleared = (
                    task is None
                    or session is None
                    or (
                        session.task_id == task.task_id
                        and session.target_id == task.original_target_id
                        and session.browser_instance_id == task.browser_instance_id
                    )
                )
                if (
                    task
                    and recovering_purpose
                    and task.purpose not in {recovering_purpose, "LOGIN"}
                ):
                    task_cleared = False
                # LOGIN and CHALLENGE are separate workflows. A lost historical
                # page cannot be inspected again, but new same-scope business
                # proof can supersede its blocker. Never dismiss a live original,
                # a different scope, or a failed/untested affected capability.
                if task and session and session.kind == "LOGIN" and task.state == "TARGET_LOST":
                    recovered_purpose = (
                        report.body in {"FULL", "SHORT_FULL"}
                        if task.purpose == "BODY"
                        else report.crawler == "READY"
                        if task.purpose == "CRAWLER"
                        else report.authentication in {"VALID", "NOT_REQUIRED"}
                    )
                    task_cleared = task_cleared or (
                        task.scope_id == scope.scope_id
                        and task.site_id == scope.owner_site_id
                        and session.site_id == scope.owner_site_id
                        and session.identity_id == task.identity_id == identity_id
                        and session.scope_id == scope.scope_id
                        and bool(report.runtime_instance_id)
                        and report.runtime_instance_id == session.browser_instance_id
                        and recovered_purpose
                    )
                if task and task_cleared:
                    self._write_task(
                        connection,
                        task.model_copy(
                            update={
                                "state": "RESOLVED",
                                "resolved_at": now,
                                "last_check": now,
                            }
                        ),
                    )
                if task_cleared:
                    member.manual_attention_required = False
                if task_cleared and member.last_browser_result == "ACCESS_CHALLENGE":
                    member.state = "READY"
                    member.cooldown_until = None
                if task and not task_cleared:
                    report.challenge_task_id = task.task_id
                    if session and session.kind == "LOGIN" and task.state == "TARGET_LOST":
                        report.reason = "original_task_recovery_proof_incomplete"
            if report.crawler == "READY":
                member.purpose_rejected_until.pop("CRAWLER", None)
                member.purpose_denials.pop("CRAWLER", None)
            if report.body in {"FULL", "SHORT_FULL"}:
                member.purpose_rejected_until.pop("BODY", None)
                member.purpose_denials.pop("BODY", None)
            state.generation += 1
            state.updated_at = now
            connection.execute(
                "insert into site_runtime values(?,?,?,?) on conflict(runtime_key) do update set "
                "data_json=excluded.data_json,generation=excluded.generation,updated_at=excluded.updated_at",
                (state.runtime_key, state.model_dump_json(), state.generation, now.isoformat()),
            )
            old_auth = self.repository.get_site_identity_auth(scope.owner_site_id, identity_id)
            auth_state = (
                AuthState(report.authentication)
                if report.authentication
                in {
                    "VALID",
                    "REAUTH_REQUIRED",
                    "ENTITLEMENT_MISSING",
                }
                else old_auth.auth_state
            )
            auth = SiteIdentityAuth(
                site_id=scope.owner_site_id,
                identity_id=identity_id,
                auth_state=auth_state,
                verified_at=(
                    now
                    if report.authentication in {"VALID", "REAUTH_REQUIRED", "ENTITLEMENT_MISSING"}
                    else old_auth.verified_at
                ),
                reason_code=report.reason,
                observed_session_revision=runtime.session_revision,
                last_check_at=now,
                last_check_result=report.authentication,
                last_check_reason=report.reason,
            )
            connection.execute(
                "insert into site_identity_auth values(?,?,?,?) on conflict(site_id,identity_id) "
                "do update set data_json=excluded.data_json,updated_at=excluded.updated_at",
                (auth.site_id, auth.identity_id, auth.model_dump_json(), now.isoformat()),
            )
            profile = self.repository.get_profile(identity.profile_id)
            if profile:
                profile = profile.model_copy(
                    update={
                        "auth_state": auth_state,
                        "session_revision": runtime.session_revision,
                        "challenge_verified": report.challenge == "CLEARED",
                        "updated_at": now,
                    }
                )
                connection.execute(
                    "update browser_profiles set data_json=?,updated_at=? where profile_id=?",
                    (profile.model_dump_json(), now.isoformat(), profile.profile_id),
                )
            if session and report.challenge == "CLEARED" and task_cleared:
                self._commit_manual_recovery(
                    connection, scope, identity_id, report, fingerprint, runtime
                )
        report.health_applied = True
        return report

    def _commit_manual_recovery(self, connection, scope, identity_id, report, fingerprint, runtime):
        """Manual business proof releases the same gate without a second network probe."""
        from .recovery import context_key
        from .schema import AccessEvent, canonical_json

        current = self.get_recovery(identity_id)
        if not current or current.phase == "RESOLVED" or current.scope_id != scope.scope_id:
            return
        if not report.runtime_instance_id or (
            runtime.instance_id and report.runtime_instance_id != runtime.instance_id
        ):
            return
        proven = set()
        if report.crawler == "READY":
            proven.add("CRAWLER")
        if report.body in {"FULL", "SHORT_FULL"}:
            proven.add("BODY")
        if not proven:
            return
        covered = [c for c in current.affected_contexts if c["purpose"] in proven]
        if not covered:
            return
        remaining = [c for c in current.affected_contexts if c["purpose"] not in proven]
        now = utc_now()
        target = (
            self.get_target(current.original_allocation_id)
            if current.original_allocation_id
            else None
        )
        value = current.model_copy(
            update={
                "phase": "BACKOFF" if remaining else "RESOLVED",
                "generation": current.generation + 1,
                "fingerprint": fingerprint,
                "verified_contexts": list(
                    dict.fromkeys(
                        [
                            *current.verified_contexts,
                            *(context_key(c) for c in covered),
                        ]
                    )
                )[-8:],
                "affected_contexts": remaining or current.affected_contexts,
                "resolved_at": None if remaining else now,
                "resolved_by": "manual_verification",
                "lease_owner": None,
                "lease_until": None,
                "last_error": None,
                "next_due_at": now + timedelta(seconds=60),
                "updated_at": now,
                "cleanup_pending": bool(
                    not remaining and target and target.state not in {"CLOSED", "LOST"}
                ),
            }
        )
        self._write_recovery(connection, value)
        self._record_recovered_window(connection, current, runtime)
        event = AccessEvent(
            operation_id=value.recovery_id,
            site_id=value.site_id,
            combination_id=identity_id,
            category="RECOVERY_RESOLVED" if not remaining else "CAPABILITY_RECOVERED",
            payload={
                "recovery_id": value.recovery_id,
                "resolved_by": "manual_verification",
                "purposes": sorted(proven),
            },
        )
        connection.execute(
            "insert or ignore into access_events(event_id,operation_id,site_id,combination_id,"
            "occurred_at,category,payload_json) values(?,?,?,?,?,?,?)",
            (
                event.event_id,
                event.operation_id,
                event.site_id,
                event.combination_id,
                event.occurred_at.isoformat(),
                event.category,
                canonical_json(event.payload),
            ),
        )

    def get_recovery(self, identity_id):
        return self._read("browser_recoveries", "identity_id", identity_id, BrowserRecovery)

    def list_recoveries(self, *, active_only=True):
        with self.repository._lock:
            rows = self.repository._connection.execute(
                "select data_json from browser_recoveries "
                + ("where phase<>'RESOLVED' " if active_only else "")
                + "order by next_due_at,identity_id"
            ).fetchall()
        return [BrowserRecovery.model_validate_json(r[0]) for r in rows]

    def _write_recovery(self, connection, value):
        connection.execute(
            "insert into browser_recoveries values(?,?,?,?,?,?,?) on conflict(identity_id) "
            "do update set scope_id=excluded.scope_id,phase=excluded.phase,"
            "next_due_at=excluded.next_due_at,generation=excluded.generation,"
            "data_json=excluded.data_json,updated_at=excluded.updated_at",
            (
                value.identity_id,
                value.scope_id,
                value.phase,
                value.next_due_at.isoformat(),
                value.generation,
                value.model_dump_json(),
                value.updated_at.isoformat(),
            ),
        )
        return value

    def save_recovery(self, value, *, expected_generation):
        with self.repository.transaction() as connection:
            old = self.get_recovery(value.identity_id)
            if (old.generation if old else None) != expected_generation:
                raise RuntimeError("stale_recovery_generation")
            linked = (
                self.get_challenge(value.challenge_task_id) if value.challenge_task_id else None
            )
            if (
                old
                and value.handling_mode == "HUMAN"
                and (old.handling_mode == "AUTO" or value.manual_review_reason)
                and (not linked or linked.state == "RESOLVED")
                and not any(
                    s.identity_id == value.identity_id and s.state == "OPEN"
                    for s in self.list_sessions()
                )
            ):
                target = self.get_target(value.original_allocation_id or "")
                if target and target.state == "LIVE" and value.affected_contexts:
                    context = value.affected_contexts[0]
                    task = self.handoff_challenge(
                        ChallengeTask(
                            scope_id=value.scope_id,
                            identity_id=value.identity_id,
                            site_id=value.site_id,
                            purpose=context["purpose"],
                            operation_id=context["operation_id"],
                            original_url=value.original_url or context["url"],
                            original_target_id=target.target_id,
                            browser_instance_id=target.browser_instance_id,
                            allocation_id=target.allocation_id,
                            reason=value.manual_review_reason
                            or "device_check_escalated_to_interactive",
                            recovery_id=value.recovery_id,
                        ),
                        _connection=connection,
                    )
                    value.challenge_task_id = task.task_id
                    scope = self.maintenance_scope(value.scope_id)
                    key = scope.verification_plan.get(
                        "legacy_runtime_key", f"scope:{scope.scope_id}"
                    )
                    state = self.repository.get_runtime(key)
                    member = state.combinations.get(value.identity_id)
                    if member:
                        member.manual_attention_required = True
                        state.generation += 1
                        connection.execute(
                            "update site_runtime set data_json=?,generation=?,updated_at=? where runtime_key=?",
                            (state.model_dump_json(), state.generation, utc_now().isoformat(), key),
                        )
                elif target and value.manual_review_reason and value.affected_contexts:
                    context = value.affected_contexts[0]
                    task = ChallengeTask(
                        scope_id=value.scope_id,
                        identity_id=value.identity_id,
                        site_id=value.site_id,
                        purpose=context["purpose"],
                        operation_id=context["operation_id"],
                        original_url=value.original_url or context["url"],
                        original_target_id=target.target_id,
                        browser_instance_id=target.browser_instance_id,
                        allocation_id=target.allocation_id,
                        state="TARGET_LOST",
                        reason=value.manual_review_reason,
                        recovery_id=value.recovery_id,
                    )
                    self._write_task(connection, task)
                    value.challenge_task_id = task.task_id
            if old and old.handling_mode == "HUMAN" and value.handling_mode == "AUTO":
                # Reclassification releases only the obsolete manual projection.
                # BrowserRecovery remains authoritative for every affected purpose.
                scope = self.maintenance_scope(value.scope_id)
                key = scope.verification_plan.get("legacy_runtime_key", f"scope:{scope.scope_id}")
                state = self.repository.get_runtime(key)
                member = state.combinations.get(value.identity_id)
                if member:
                    member.manual_attention_required = False
                    if member.last_browser_result == "ACCESS_CHALLENGE":
                        member.state, member.cooldown_until = "READY", None
                    state.generation += 1
                    connection.execute(
                        "update site_runtime set data_json=?,generation=?,updated_at=? where runtime_key=?",
                        (state.model_dump_json(), state.generation, utc_now().isoformat(), key),
                    )
                if not any(
                    s.identity_id == value.identity_id and s.state == "OPEN"
                    for s in self.list_sessions()
                ):
                    identity = self.repository.get_identity(value.identity_id)
                    profile = self.repository.get_profile(identity.profile_id)
                    if (
                        profile.maintenance_session_id
                        and profile.maintenance_session_id == value.challenge_task_id
                    ):
                        from .schema import ProfileOperationalState

                        profile = profile.model_copy(
                            update={
                                "operational_state": ProfileOperationalState.AVAILABLE,
                                "maintenance_session_id": None,
                                "updated_at": utc_now(),
                            }
                        )
                        connection.execute(
                            "update browser_profiles set data_json=?,updated_at=? where profile_id=?",
                            (profile.model_dump_json(), utc_now().isoformat(), profile.profile_id),
                        )
            if value.handling_mode == "AUTO":
                self._retire_automatic_task(connection, value)
            elif value.manual_review_reason and value.challenge_task_id:
                current_task = self.get_challenge(value.challenge_task_id)
                if (
                    current_task
                    and current_task.state != "RESOLVED"
                    and current_task.reason != value.manual_review_reason
                ):
                    self._write_task(
                        connection,
                        current_task.model_copy(update={"reason": value.manual_review_reason}),
                    )
            return self._write_recovery(connection, value)

    def _retire_automatic_task(self, connection, value):
        """Retire human work, not business recovery; keep its token as physical-page history."""
        task = self.get_challenge(value.challenge_task_id) if value.challenge_task_id else None
        if (
            not task
            or task.state == "RESOLVED"
            or task.scope_id != value.scope_id
            or task.identity_id != value.identity_id
            or any(
                s.identity_id == value.identity_id and s.state == "OPEN"
                for s in self.list_sessions()
            )
        ):
            return
        self._write_task(
            connection,
            task.model_copy(
                update={
                    "state": "RESOLVED",
                    "resolved_at": utc_now(),
                    "resolution_reason": "RECLASSIFIED_AUTOMATIC_VERIFICATION",
                    "resolved_by": "classification",
                }
            ),
        )
        identity = self.repository.get_identity(value.identity_id)
        profile = self.repository.get_profile(identity.profile_id)
        retired_tokens = {
            task.task_id,
            *(
                s.control_token
                for s in self.list_sessions()
                if s.identity_id == value.identity_id
                and s.task_id == task.task_id
                and s.state != "OPEN"
            ),
        }
        if profile.maintenance_session_id and profile.maintenance_session_id in retired_tokens:
            from .schema import ProfileOperationalState

            profile = profile.model_copy(
                update={
                    "operational_state": ProfileOperationalState.AVAILABLE,
                    "maintenance_session_id": None,
                    "updated_at": utc_now(),
                }
            )
            connection.execute(
                "update browser_profiles set data_json=?,updated_at=? where profile_id=?",
                (profile.model_dump_json(), utc_now().isoformat(), profile.profile_id),
            )
        target = self.get_target(task.allocation_id)
        if (
            target
            and target.state == "LIVE"
            and target.owner_kind == "CHALLENGE"
            and target.owner_ref == task.task_id
        ):
            self._write_target(
                connection,
                target.model_copy(
                    update={
                        "owner_kind": "RECOVERY",
                        "owner_ref": value.recovery_id,
                        "updated_at": utc_now(),
                    }
                ),
            )

    def begin_recovery(self, value):
        """Commit a retained page and its policy before business cleanup can run."""
        value = BrowserRecovery.model_validate(value)
        with self.repository.transaction() as connection:
            identity = self.repository.get_identity(value.identity_id)
            if not identity:
                raise ValueError("unknown recovery identity")
            scope = self.maintenance_scope(value.scope_id)
            if not scope or value.identity_id not in {
                scope.primary_identity_id,
                scope.fallback_identity_id,
            }:
                raise ValueError("recovery identity is outside scope")
            previous = self.get_recovery(value.identity_id)
            if not previous or previous.phase == "RESOLVED":
                runtime = self.repository.get_identity_runtime(value.identity_id)
                if (
                    runtime.recovered_at
                    and runtime.last_recovery_id != value.recovery_id
                    and (utc_now() - runtime.recovered_at).total_seconds() <= 300
                ):
                    relapse = {
                        "previous_recovery_id": runtime.last_recovery_id,
                        "recovery_id": value.recovery_id,
                        "at": utc_now().isoformat(),
                    }
                    updated = runtime.model_copy(
                        update={
                            "recent_relapses": [*runtime.recent_relapses, relapse][-8:],
                        }
                    )
                    self._write_identity_window(connection, updated)
                    from .datadome_solver import mark_solver_outcome

                    mark_solver_outcome(
                        self.repository, value.identity_id, "recurrence_at", _connection=connection
                    )
            if previous and previous.phase != "RESOLVED":
                from .recovery import context_key

                contexts = {context_key(c): c for c in previous.affected_contexts}
                contexts.update({context_key(c): c for c in value.affected_contexts})
                # Repeated ticker operation IDs must not evict another capability.
                representatives = {}
                for context in contexts.values():
                    representatives.setdefault(context["purpose"], context)
                bounded = list(representatives.values())
                bounded += [c for c in contexts.values() if c not in bounded]
                existing_target = self.get_target(previous.original_allocation_id or "")
                keep = (
                    existing_target
                    and existing_target.state == "LIVE"
                    and not (previous.handling_mode == "AUTO" and value.handling_mode == "HUMAN")
                )
                value = previous.model_copy(
                    update={
                        "affected_contexts": bounded[:8],
                        "updated_at": utc_now(),
                        **(
                            {}
                            if keep
                            else {
                                "original_allocation_id": value.original_allocation_id,
                                "original_target_id": value.original_target_id,
                                "original_instance_id": value.original_instance_id,
                                "original_url": value.original_url,
                                "page_status": value.page_status,
                                "assessment": value.assessment,
                                "handling_mode": "HUMAN"
                                if previous.manual_review_reason
                                else value.handling_mode,
                            }
                        ),
                    }
                )
                if keep:
                    return self._write_recovery(connection, value)
            target = self.get_target(value.original_allocation_id or "")
            if (
                not target
                or target.state != "LIVE"
                or target.owner_kind != "BUSINESS"
                or (target.identity_id, target.browser_instance_id, target.target_id)
                != (value.identity_id, value.original_instance_id, value.original_target_id)
            ):
                raise RuntimeError("OWNERSHIP_HANDOFF_FAILED")
            egress = self.repository.get_egress(identity.egress_id)
            value = value.model_copy(
                update={
                    "fingerprint": capability_fingerprint(self.repository, scope, identity, egress)
                }
            )
            if value.handling_mode == "HUMAN":
                context = value.affected_contexts[0]
                task = self.handoff_challenge(
                    ChallengeTask(
                        scope_id=value.scope_id,
                        identity_id=value.identity_id,
                        site_id=value.site_id,
                        purpose=context["purpose"],
                        operation_id=context["operation_id"],
                        original_url=value.original_url or context["url"],
                        original_target_id=target.target_id,
                        browser_instance_id=target.browser_instance_id,
                        allocation_id=target.allocation_id,
                        reason="interactive_challenge",
                        recovery_id=value.recovery_id,
                    ),
                    _connection=connection,
                )
                value = value.model_copy(update={"challenge_task_id": task.task_id})
            else:
                self._retire_automatic_task(connection, value)
                self._write_target(
                    connection,
                    target.model_copy(
                        update={
                            "owner_kind": "RECOVERY",
                            "owner_ref": value.recovery_id,
                            "updated_at": utc_now(),
                        }
                    ),
                )
            value = value.model_copy(update={"page_status": "PRESENT"})
            self._write_recovery(connection, value)
            state = self.repository.get_runtime(
                f"scope:{scope.scope_id}"
                if not scope.verification_plan.get("legacy_runtime_key")
                else scope.verification_plan["legacy_runtime_key"]
            )
            from .schema import CombinationRuntime

            member = state.combinations.setdefault(value.identity_id, CombinationRuntime())
            member.manual_attention_required = value.handling_mode == "HUMAN"
            state.generation += 1
            connection.execute(
                "insert into site_runtime values(?,?,?,?) on conflict(runtime_key) do update set "
                "data_json=excluded.data_json,generation=excluded.generation,updated_at=excluded.updated_at",
                (
                    state.runtime_key,
                    state.model_dump_json(),
                    state.generation,
                    utc_now().isoformat(),
                ),
            )
            return value

    def claim_recovery(self, identity_id, *, owner, now=None):
        now = now or utc_now()
        with self.repository.transaction() as connection:
            old = self.get_recovery(identity_id)
            if (
                not old
                or old.phase == "RESOLVED"
                or old.next_due_at > now
                or (old.lease_until and old.lease_until > now)
            ):
                return None
            scope = self.maintenance_scope(old.scope_id)
            identity = self.repository.get_identity(identity_id)
            spec = self.repository.get_strategy(old.site_id)
            if (
                not scope
                or not scope.enabled
                or not identity
                or not identity.enabled
                or not spec.challenge.recovery_enabled
            ):
                return None
            if old.probe_id and old.probe_started_at and not old.probe_finished_at:
                from .schema import AccessEvent, digest_json

                event = AccessEvent(
                    event_id="probe_finish:" + digest_json(old.probe_id),
                    operation_id=old.recovery_id,
                    site_id=old.site_id,
                    category="PROBE_FINISHED",
                    payload={
                        "recovery_id": old.recovery_id,
                        "identity_id": identity_id,
                        "generation": old.generation,
                        "probe_id": old.probe_id,
                        "result": "INTERRUPTED",
                        "stage": old.probe_stage,
                        "request_sent": "unknown",
                    },
                )
                from .schema import canonical_json

                connection.execute(
                    "insert or ignore into access_events values(?,?,?,?,?,?,?)",
                    (
                        event.event_id,
                        event.operation_id,
                        event.site_id,
                        event.combination_id,
                        event.occurred_at.isoformat(),
                        event.category,
                        canonical_json(event.payload),
                    ),
                )
            value = old.model_copy(
                update={
                    "generation": old.generation + 1,
                    "phase": "PROBING",
                    "lease_owner": owner,
                    "lease_until": now + timedelta(seconds=100),
                    "updated_at": now,
                    "probe_finished_at": now if old.probe_id else None,
                }
            )
            return self._write_recovery(connection, value)

    def promote_recovery(self, value):
        """A newly visible interactive requirement retains the same original."""
        with self.repository.transaction() as connection:
            current = self.get_recovery(value.identity_id)
            target = self.get_target(value.original_allocation_id or "")
            if (
                not current
                or current.generation != value.generation
                or not target
                or target.state != "LIVE"
            ):
                raise RuntimeError("stale_recovery_generation")
            context = value.affected_contexts[0]
            task = self.handoff_challenge(
                ChallengeTask(
                    scope_id=value.scope_id,
                    identity_id=value.identity_id,
                    site_id=value.site_id,
                    purpose=context["purpose"],
                    operation_id=context["operation_id"],
                    original_url=context["url"],
                    original_target_id=target.target_id,
                    browser_instance_id=target.browser_instance_id,
                    allocation_id=target.allocation_id,
                    reason="interactive_requirement_observed",
                    recovery_id=value.recovery_id,
                ),
                _connection=connection,
            )
            value = value.model_copy(
                update={"challenge_task_id": task.task_id, "handling_mode": "HUMAN"}
            )
            return self._write_recovery(connection, value)

    def finish_recovery(self, claimed, report, *, context, resolved_by):
        """A lease cannot clear another incident, identity or configuration."""
        from .recovery import context_key
        from .schema import ProfileOperationalState

        with self.repository.transaction() as connection:
            current = self.get_recovery(claimed.identity_id)
            if (
                not current
                or (current.recovery_id, current.generation, current.lease_owner)
                != (claimed.recovery_id, claimed.generation, claimed.lease_owner)
                or not current.lease_until
                or current.lease_until <= utc_now()
            ):
                raise RuntimeError("stale_recovery_generation")
            scope = self.maintenance_scope(current.scope_id)
            identity = self.repository.get_identity(current.identity_id)
            fingerprint = capability_fingerprint(
                self.repository, scope, identity, self.repository.get_egress(identity.egress_id)
            )
            runtime = self.repository.get_identity_runtime(current.identity_id)
            if (
                current.fingerprint != fingerprint
                or not report.runtime_instance_id
                or (runtime.instance_id and runtime.instance_id != report.runtime_instance_id)
            ):
                raise RuntimeError("stale_recovery_browser_or_configuration")
            if report.challenge != "CLEARED":
                raise RuntimeError("recovery_requires_business_proof")
            verified = list(dict.fromkeys([*current.verified_contexts, context_key(context)]))[-8:]
            proven_purposes = {context["purpose"]}
            remaining = [
                c
                for c in current.affected_contexts
                if c["purpose"] not in proven_purposes and context_key(c) not in verified
            ]
            report = self.commit_verification(
                scope,
                current.identity_id,
                report,
                _connection=connection,
                recovering_purpose=context["purpose"],
            )
            route_key = scope.verification_plan.get("legacy_runtime_key", f"scope:{scope.scope_id}")
            route = self.repository.get_runtime(route_key)
            member = route.combinations[current.identity_id]
            # Pending purposes are guarded by Recovery; a cleared WAF gate must
            # not keep an independently proven purpose in global COOLDOWN.
            member.state, member.cooldown_until = "READY", None
            member.manual_attention_required = False
            route.generation += 1
            connection.execute(
                "update site_runtime set data_json=?,generation=?,updated_at=? where runtime_key=?",
                (route.model_dump_json(), route.generation, utc_now().isoformat(), route_key),
            )
            task = self.current_challenge(current.identity_id)
            # commit_verification may already have resolved this exact identity's task.
            if not task and current.challenge_task_id:
                task = self.get_challenge(current.challenge_task_id)
                if task and task.state == "RESOLVED":
                    self._write_task(
                        connection,
                        task.model_copy(
                            update={
                                "resolution_reason": "BUSINESS_RECOVERED_ON_NEW_PAGE"
                                if resolved_by == "fresh_probe"
                                else "ORIGINAL_DOCUMENT_RECOVERED",
                                "resolved_by": resolved_by,
                            }
                        ),
                    )
            now = utc_now()
            self._record_recovered_window(connection, current, runtime)
            value = current.model_copy(
                update={
                    "phase": "BACKOFF" if remaining else "RESOLVED",
                    "verified_contexts": verified,
                    "affected_contexts": remaining or current.affected_contexts,
                    "resolved_at": None if remaining else now,
                    "resolved_by": resolved_by,
                    "lease_owner": None,
                    "lease_until": None,
                    "last_error": None,
                    "next_due_at": now + timedelta(seconds=60),
                    "updated_at": now,
                }
            )
            self._write_recovery(connection, value)
            if not any(
                s.identity_id == current.identity_id and s.state == "OPEN"
                for s in self.list_sessions()
            ):
                profile = self.repository.get_profile(identity.profile_id)
                sessions = {
                    s.control_token
                    for s in self.list_sessions()
                    if s.identity_id == identity.identity_id and s.kind == "CHALLENGE"
                }
                if profile.maintenance_session_id in {current.challenge_task_id, *sessions}:
                    profile = profile.model_copy(
                        update={
                            "operational_state": ProfileOperationalState.AVAILABLE,
                            "maintenance_session_id": None,
                            "challenge_url": None,
                            "challenge_target_id": None,
                            "updated_at": now,
                        }
                    )
                    connection.execute(
                        "update browser_profiles set data_json=?,updated_at=? where profile_id=?",
                        (profile.model_dump_json(), now.isoformat(), profile.profile_id),
                    )
            return value

    def _record_recovered_window(self, connection, recovery, runtime):
        if runtime.last_recovery_id == recovery.recovery_id:
            return
        now = utc_now()
        policy = self.repository.get_strategy(recovery.site_id).challenge
        seconds = policy.probation_seconds if recovery.site_id == "reuters" else 0
        if (
            seconds
            and len(runtime.recent_relapses) >= 2
            and all(
                now - datetime.fromisoformat(r["at"]) <= timedelta(minutes=30)
                for r in runtime.recent_relapses[-2:]
            )
        ):
            seconds = max(seconds, 900)
        self._write_identity_window(
            connection,
            runtime.model_copy(
                update={
                    "recovered_at": now,
                    "probation_until": now + timedelta(seconds=seconds) if seconds else None,
                    "probation_interval_ms": policy.probation_interval_ms if seconds else 0,
                    "last_recovery_id": recovery.recovery_id,
                    "natural_success_at": None,
                }
            ),
        )

    @staticmethod
    def _write_identity_window(connection, value):
        connection.execute(
            "insert into identity_runtime values(?,?,?,?) on conflict(identity_id) do update set "
            "data_json=excluded.data_json,generation=excluded.generation,updated_at=excluded.updated_at",
            (value.identity_id, value.model_dump_json(), value.generation, utc_now().isoformat()),
        )

    def record_capability(
        self,
        scope,
        identity_id,
        purpose,
        result,
        *,
        url,
        instance_id=None,
        cold_verified=False,
        subscription_verified=False,
    ):
        """A real business canary is evidence, not a route-generation change."""
        from .schema import CombinationRuntime, digest_json

        now = utc_now()
        with self.repository.transaction() as connection:
            identity = self.repository.get_identity(identity_id)
            egress = self.repository.get_egress(identity.egress_id)
            state = self.repository.get_runtime(f"scope:{scope.scope_id}")
            member = state.combinations.setdefault(identity_id, CombinationRuntime())
            previous = member.capabilities.get(purpose, {})
            fingerprint = capability_fingerprint(self.repository, scope, identity, egress)
            if previous.get("fingerprint") not in {None, fingerprint}:
                previous = {}
            samples = list(previous.get("article_samples", []))
            subscription_samples = list(previous.get("subscription_samples", []))
            sample = digest_json(safe_display_url(url))
            if purpose == "BODY" and result in {"FULL", "SHORT_FULL"} and sample not in samples:
                samples = [*samples, sample][-2:]
            if purpose == "BODY" and subscription_verified and sample not in subscription_samples:
                subscription_samples = [*subscription_samples, sample][-2:]
            member.capabilities[purpose] = {
                "result": result,
                "checked_at": now.isoformat(),
                "fingerprint": fingerprint,
                "instance_id": instance_id,
                "article_samples": samples,
                "subscription_samples": subscription_samples,
                "last_url": safe_display_url(url),
                "state": "VERIFIED_COLD"
                if cold_verified
                else previous.get("state", "VERIFIED_WARM"),
                "contract": scope.verification_plan.get("contract", "body_v2.2/listing_v1"),
                "authenticated": self.repository.get_site_identity_auth(
                    scope.owner_site_id,
                    identity_id,
                ).auth_state.value
                == "VALID",
            }
            connection.execute(
                "insert into site_runtime values(?,?,?,?) on conflict(runtime_key) do update set "
                "data_json=excluded.data_json,updated_at=excluded.updated_at",
                (state.runtime_key, state.model_dump_json(), state.generation, now.isoformat()),
            )
        return member.capabilities[purpose]
