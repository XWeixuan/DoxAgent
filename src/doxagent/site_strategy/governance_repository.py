"""Short, owner-only transactions for source scopes and browser ownership.

Schema creation is explicitly called by the Registry's startup migration, never
by inventory readers or a browser control worker.
"""

from urllib.parse import urlsplit, urlunsplit

from .governance_models import (
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
        ]
    )


class GovernanceRepository:
    def __init__(self, repository):
        self.repository = repository

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

    def handoff_challenge(self, task: ChallengeTask, *, session=None):
        """Task + ownership commit together, before a business permit is released."""
        with self.repository.transaction() as connection:
            existing = self.current_challenge(task.identity_id)
            if existing:
                return existing
            target = self.get_target(task.allocation_id)
            if target is None or (
                target.identity_id,
                target.browser_instance_id,
                target.target_id,
            ) != (task.identity_id, task.browser_instance_id, task.original_target_id):
                raise ValueError("challenge does not own its original target")
            stored = task.model_copy(update={"original_url": safe_display_url(task.original_url)})
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

                blocked = profile.model_copy(
                    update={
                        "operational_state": ProfileOperationalState.MAINTENANCE,
                        "operational_revision": profile.operational_revision + 1,
                        "maintenance_session_id": session.control_token
                        if session
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
        self, scope, identity_id, report, *, session=None, credential_change=False
    ):
        """Proof, relevant gates and current Task resolve in one short owner transaction."""
        from .schema import (
            AuthState,
            CombinationRuntime,
            SiteIdentityAuth,
            digest_json,
        )

        now = utc_now()
        with self.repository.transaction() as connection:
            current_scope = self.maintenance_scope(scope.scope_id)
            if current_scope is None or current_scope.revision != scope.revision:
                raise RuntimeError("scope changed during verification")
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
                if task:
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
                member.manual_attention_required = False
                if member.last_browser_result == "ACCESS_CHALLENGE":
                    member.state = "READY"
                    member.cooldown_until = None
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
                verified_at=now,
                reason_code=report.reason,
                observed_session_revision=runtime.session_revision,
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
        report.health_applied = True
        return report

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
