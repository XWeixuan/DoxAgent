"""SQLite persistence for Site Strategy configuration, health and outcomes."""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .schema import (
    AccessEvent,
    BodyOutcome,
    BrowserIdentityHead,
    BrowserIdentityRuntime,
    BrowserIdentitySpec,
    BrowserProfile,
    ProxyEgress,
    SiteIdentityAuth,
    SiteRuntimeState,
    SiteStrategyHead,
    SiteStrategySpec,
    canonical_json,
    digest_json,
    utc_now,
)


class SiteStrategyRepository:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.path, check_same_thread=False, timeout=5)
        self._connection.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._connection.execute("pragma journal_mode=WAL")
            self._connection.execute("pragma foreign_keys=ON")
            self._connection.execute("pragma busy_timeout=5000")
        self.ensure_schema()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            connection = self._connection
            connection.execute("begin immediate")
            try:
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def backup_to(self, destination: str | Path) -> Path:
        """Create a transactionally consistent SQLite backup."""
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            output = sqlite3.connect(target)
            try:
                self._connection.backup(output)
            finally:
                output.close()
        return target

    def ensure_schema(self) -> None:
        with self.transaction() as connection:
            connection.executescript(
                """
                create table if not exists site_strategy_revisions (
                    site_id text not null,
                    revision integer not null,
                    spec_json text not null,
                    digest text not null,
                    created_at text not null,
                    actor text not null,
                    primary key(site_id, revision)
                );
                create table if not exists site_strategy_heads (
                    site_id text primary key,
                    active_revision integer not null,
                    enabled integer not null,
                    updated_at text not null,
                    foreign key(site_id, active_revision)
                        references site_strategy_revisions(site_id, revision)
                );
                create table if not exists proxy_egresses (
                    egress_id text primary key,
                    data_json text not null,
                    updated_at text not null
                );
                create table if not exists browser_profiles (
                    profile_id text primary key,
                    site_id text not null,
                    bound_egress_id text not null,
                    directory_key text not null,
                    data_json text not null,
                    updated_at text not null,
                    foreign key(bound_egress_id) references proxy_egresses(egress_id)
                );
                create unique index if not exists browser_profiles_directory_key
                    on browser_profiles(directory_key);
                create table if not exists browser_identity_revisions (
                    identity_id text not null,
                    revision integer not null,
                    spec_json text not null,
                    digest text not null,
                    created_at text not null,
                    actor text not null,
                    primary key(identity_id, revision)
                );
                create table if not exists browser_identity_heads (
                    identity_id text primary key,
                    active_revision integer not null,
                    enabled integer not null,
                    updated_at text not null,
                    foreign key(identity_id, active_revision)
                        references browser_identity_revisions(identity_id, revision)
                );
                create table if not exists identity_runtime (
                    identity_id text primary key,
                    data_json text not null,
                    generation integer not null,
                    updated_at text not null
                );
                create table if not exists site_identity_auth (
                    site_id text not null,
                    identity_id text not null,
                    data_json text not null,
                    updated_at text not null,
                    primary key(site_id, identity_id)
                );
                create table if not exists site_runtime (
                    runtime_key text primary key,
                    data_json text not null,
                    generation integer not null,
                    updated_at text not null
                );
                create table if not exists access_events (
                    event_id text primary key,
                    operation_id text not null,
                    site_id text not null,
                    combination_id text,
                    occurred_at text not null,
                    category text not null,
                    payload_json text not null
                );
                create index if not exists access_events_site_time
                    on access_events(site_id, occurred_at);
                create index if not exists access_events_operation
                    on access_events(operation_id);
                create table if not exists body_outcomes (
                    job_id text primary key,
                    final_site_id text not null,
                    strategy_ref text not null,
                    combination_id text,
                    outcome text not null,
                    reason text,
                    completed_at text not null,
                    payload_json text not null
                );
                create index if not exists body_outcomes_site_time
                    on body_outcomes(final_site_id, completed_at);
                """
            )

    def apply_identity(
        self,
        spec: BrowserIdentitySpec,
        *,
        expected_revision: int | None,
        actor: str,
        enabled: bool | None = None,
    ) -> BrowserIdentitySpec:
        """CAS apply for a Browser Identity, including immutable binding checks."""
        now = utc_now()
        with self.transaction() as connection:
            row = connection.execute(
                "select active_revision from browser_identity_heads where identity_id=?",
                (spec.identity_id,),
            ).fetchone()
            current = int(row["active_revision"]) if row else None
            if current != expected_revision:
                raise RuntimeError(
                    "browser identity revision conflict: "
                    f"expected {expected_revision}, current {current}"
                )
            duplicate = connection.execute(
                """select r.spec_json from browser_identity_heads h
                   join browser_identity_revisions r on r.identity_id=h.identity_id
                    and r.revision=h.active_revision
                   where h.identity_id<>? and h.enabled=1""",
                (spec.identity_id,),
            ).fetchall()
            for item in duplicate:
                other = BrowserIdentitySpec.model_validate_json(item["spec_json"])
                if other.profile_id == spec.profile_id:
                    raise ValueError(
                        f"profile {spec.profile_id} is already owned by identity "
                        f"{other.identity_id}"
                    )
            revision = (current or 0) + 1
            stored = spec.with_revision(revision)
            payload = stored.model_dump(mode="json")
            connection.execute(
                """insert into browser_identity_revisions(
                       identity_id,revision,spec_json,digest,created_at,actor
                   ) values(?,?,?,?,?,?)""",
                (
                    stored.identity_id,
                    revision,
                    canonical_json(payload),
                    digest_json(payload),
                    now.isoformat(),
                    actor,
                ),
            )
            connection.execute(
                """insert into browser_identity_heads(
                       identity_id,active_revision,enabled,updated_at)
                   values(?,?,?,?) on conflict(identity_id) do update set
                   active_revision=excluded.active_revision,enabled=excluded.enabled,
                   updated_at=excluded.updated_at""",
                (
                    stored.identity_id,
                    revision,
                    int(stored.enabled if enabled is None else enabled),
                    now.isoformat(),
                ),
            )
        return stored

    def get_identity(
        self, identity_id: str, revision: int | None = None
    ) -> BrowserIdentitySpec | None:
        with self._lock:
            if revision is None:
                row = self._connection.execute(
                    """select r.spec_json from browser_identity_revisions r
                       join browser_identity_heads h on h.identity_id=r.identity_id
                        and h.active_revision=r.revision where r.identity_id=?""",
                    (identity_id,),
                ).fetchone()
            else:
                row = self._connection.execute(
                    """select spec_json from browser_identity_revisions
                       where identity_id=? and revision=?""",
                    (identity_id, revision),
                ).fetchone()
        return BrowserIdentitySpec.model_validate_json(row["spec_json"]) if row else None

    def get_identity_head(self, identity_id: str) -> BrowserIdentityHead | None:
        with self._lock:
            row = self._connection.execute(
                "select * from browser_identity_heads where identity_id=?", (identity_id,)
            ).fetchone()
        return (
            BrowserIdentityHead(
                identity_id=row["identity_id"],
                active_revision=row["active_revision"],
                enabled=bool(row["enabled"]),
                updated_at=row["updated_at"],
            )
            if row
            else None
        )

    def list_active_identities(self) -> list[tuple[BrowserIdentityHead, BrowserIdentitySpec]]:
        with self._lock:
            rows = self._connection.execute(
                """select h.identity_id,h.active_revision,h.enabled,h.updated_at,r.spec_json
                   from browser_identity_heads h join browser_identity_revisions r
                    on r.identity_id=h.identity_id and r.revision=h.active_revision
                   order by h.identity_id"""
            ).fetchall()
        return [
            (
                BrowserIdentityHead(
                    identity_id=row["identity_id"],
                    active_revision=row["active_revision"],
                    enabled=bool(row["enabled"]),
                    updated_at=row["updated_at"],
                ),
                BrowserIdentitySpec.model_validate_json(row["spec_json"]),
            )
            for row in rows
        ]

    def list_identity_revisions(self, identity_id: str) -> list[dict[str, object]]:
        with self._lock:
            rows = self._connection.execute(
                """select revision,digest,created_at,actor from browser_identity_revisions
                   where identity_id=? order by revision desc""",
                (identity_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_identity_runtime(self, identity_id: str) -> BrowserIdentityRuntime:
        with self._lock:
            row = self._connection.execute(
                "select data_json from identity_runtime where identity_id=?", (identity_id,)
            ).fetchone()
        return (
            BrowserIdentityRuntime.model_validate_json(row["data_json"])
            if row
            else BrowserIdentityRuntime(identity_id=identity_id)
        )

    def save_identity_runtime(
        self, value: BrowserIdentityRuntime, *, expected_generation: int
    ) -> BrowserIdentityRuntime:
        stored = value.model_copy(update={"updated_at": utc_now()})
        with self.transaction() as connection:
            row = connection.execute(
                "select generation from identity_runtime where identity_id=?",
                (value.identity_id,),
            ).fetchone()
            current = int(row["generation"]) if row else 0
            if current != expected_generation:
                raise RuntimeError("identity runtime generation conflict")
            connection.execute(
                """insert into identity_runtime(identity_id,data_json,generation,updated_at)
                   values(?,?,?,?) on conflict(identity_id) do update set
                   data_json=excluded.data_json,generation=excluded.generation,
                   updated_at=excluded.updated_at""",
                (
                    stored.identity_id,
                    stored.model_dump_json(),
                    stored.generation,
                    stored.updated_at.isoformat(),
                ),
            )
        return stored

    def get_site_identity_auth(self, site_id: str, identity_id: str) -> SiteIdentityAuth:
        with self._lock:
            row = self._connection.execute(
                "select data_json from site_identity_auth where site_id=? and identity_id=?",
                (site_id, identity_id),
            ).fetchone()
        return (
            SiteIdentityAuth.model_validate_json(row["data_json"])
            if row
            else SiteIdentityAuth(site_id=site_id, identity_id=identity_id)
        )

    def save_site_identity_auth(self, value: SiteIdentityAuth) -> None:
        with self.transaction() as connection:
            connection.execute(
                """insert into site_identity_auth(site_id,identity_id,data_json,updated_at)
                   values(?,?,?,?) on conflict(site_id,identity_id) do update set
                   data_json=excluded.data_json,updated_at=excluded.updated_at""",
                (
                    value.site_id,
                    value.identity_id,
                    value.model_dump_json(),
                    utc_now().isoformat(),
                ),
            )

    def list_site_identity_auth(
        self, *, site_id: str | None = None, identity_id: str | None = None
    ) -> list[SiteIdentityAuth]:
        clauses: list[str] = []
        params: list[object] = []
        if site_id:
            clauses.append("site_id=?")
            params.append(site_id)
        if identity_id:
            clauses.append("identity_id=?")
            params.append(identity_id)
        sql = "select data_json from site_identity_auth"
        if clauses:
            sql += " where " + " and ".join(clauses)
        sql += " order by site_id,identity_id"
        with self._lock:
            rows = self._connection.execute(sql, tuple(params)).fetchall()
        return [SiteIdentityAuth.model_validate_json(row["data_json"]) for row in rows]

    def apply_strategy(
        self,
        spec: SiteStrategySpec,
        *,
        expected_revision: int | None,
        actor: str,
        enabled: bool = True,
    ) -> SiteStrategySpec:
        now = utc_now()
        with self.transaction() as connection:
            row = connection.execute(
                "select active_revision from site_strategy_heads where site_id=?", (spec.site_id,)
            ).fetchone()
            current = int(row["active_revision"]) if row else None
            if current != expected_revision:
                detail = f"expected {expected_revision}, current {current}"
                raise RuntimeError(f"site strategy revision conflict: {detail}")
            revision = (current or 0) + 1
            stored = spec.with_revision(revision)
            payload = stored.model_dump(mode="json", by_alias=True)
            connection.execute(
                """insert into site_strategy_revisions(
                       site_id,revision,spec_json,digest,created_at,actor
                   ) values(?,?,?,?,?,?)""",
                (
                    stored.site_id,
                    revision,
                    canonical_json(payload),
                    digest_json(payload),
                    now.isoformat(),
                    actor,
                ),
            )
            connection.execute(
                """insert into site_strategy_heads(site_id,active_revision,enabled,updated_at)
                   values(?,?,?,?)
                   on conflict(site_id) do update set active_revision=excluded.active_revision,
                       enabled=excluded.enabled,updated_at=excluded.updated_at""",
                (stored.site_id, revision, int(enabled), now.isoformat()),
            )
        return stored

    def rollback_strategy(
        self, site_id: str, target_revision: int, *, expected_revision: int, actor: str
    ) -> SiteStrategySpec:
        target = self.get_strategy(site_id, target_revision)
        if target is None:
            raise KeyError(f"site strategy revision not found: {site_id}@{target_revision}")
        return self.apply_strategy(target, expected_revision=expected_revision, actor=actor)

    def set_site_enabled(self, site_id: str, enabled: bool, *, expected_revision: int) -> None:
        with self.transaction() as connection:
            changed = connection.execute(
                """update site_strategy_heads set enabled=?,updated_at=?
                   where site_id=? and active_revision=?""",
                (int(enabled), utc_now().isoformat(), site_id, expected_revision),
            ).rowcount
            if changed != 1:
                raise RuntimeError("site strategy revision conflict")

    def get_strategy(self, site_id: str, revision: int | None = None) -> SiteStrategySpec | None:
        with self._lock:
            if revision is None:
                row = self._connection.execute(
                    """select r.spec_json from site_strategy_revisions r
                       join site_strategy_heads h on h.site_id=r.site_id
                         and h.active_revision=r.revision where r.site_id=?""",
                    (site_id,),
                ).fetchone()
            else:
                row = self._connection.execute(
                    "select spec_json from site_strategy_revisions where site_id=? and revision=?",
                    (site_id, revision),
                ).fetchone()
        return SiteStrategySpec.model_validate_json(row["spec_json"]) if row else None

    def get_head(self, site_id: str) -> SiteStrategyHead | None:
        with self._lock:
            row = self._connection.execute(
                "select * from site_strategy_heads where site_id=?", (site_id,)
            ).fetchone()
        return (
            SiteStrategyHead(
                site_id=row["site_id"],
                active_revision=row["active_revision"],
                enabled=bool(row["enabled"]),
                updated_at=row["updated_at"],
            )
            if row
            else None
        )

    def list_active_strategies(self) -> list[tuple[SiteStrategyHead, SiteStrategySpec]]:
        with self._lock:
            rows = self._connection.execute(
                """select h.site_id,h.active_revision,h.enabled,h.updated_at,r.spec_json
                   from site_strategy_heads h join site_strategy_revisions r
                     on r.site_id=h.site_id and r.revision=h.active_revision
                   order by h.site_id"""
            ).fetchall()
        return [
            (
                SiteStrategyHead(
                    site_id=row["site_id"],
                    active_revision=row["active_revision"],
                    enabled=bool(row["enabled"]),
                    updated_at=row["updated_at"],
                ),
                SiteStrategySpec.model_validate_json(row["spec_json"]),
            )
            for row in rows
        ]

    def list_revisions(self, site_id: str) -> list[dict[str, object]]:
        with self._lock:
            rows = self._connection.execute(
                """select revision,digest,created_at,actor from site_strategy_revisions
                   where site_id=? order by revision desc""",
                (site_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def upsert_egress(self, value: ProxyEgress) -> None:
        now = utc_now().isoformat()
        with self.transaction() as connection:
            connection.execute(
                """insert into proxy_egresses(egress_id,data_json,updated_at) values(?,?,?)
                   on conflict(egress_id) do update set data_json=excluded.data_json,
                       updated_at=excluded.updated_at""",
                (value.egress_id, value.model_dump_json(), now),
            )

    def get_egress(self, egress_id: str) -> ProxyEgress | None:
        with self._lock:
            row = self._connection.execute(
                "select data_json from proxy_egresses where egress_id=?", (egress_id,)
            ).fetchone()
        return ProxyEgress.model_validate_json(row["data_json"]) if row else None

    def list_egresses(self) -> list[ProxyEgress]:
        with self._lock:
            rows = self._connection.execute(
                "select data_json from proxy_egresses order by egress_id"
            ).fetchall()
        return [ProxyEgress.model_validate_json(row["data_json"]) for row in rows]

    def save_profile(self, value: BrowserProfile) -> None:
        with self.transaction() as connection:
            existing = connection.execute(
                "select data_json from browser_profiles where profile_id=?", (value.profile_id,)
            ).fetchone()
            if existing:
                prior = BrowserProfile.model_validate_json(existing["data_json"])
                if (
                    prior.site_id != value.site_id
                    or prior.bound_egress_id != value.bound_egress_id
                    or prior.directory_key != value.directory_key
                ):
                    raise ValueError("profile site, egress and directory bindings are immutable")
            connection.execute(
                """insert into browser_profiles(
                       profile_id,site_id,bound_egress_id,directory_key,data_json,updated_at
                   ) values(?,?,?,?,?,?)
                   on conflict(profile_id) do update set data_json=excluded.data_json,
                       updated_at=excluded.updated_at""",
                (
                    value.profile_id,
                    value.site_id,
                    value.bound_egress_id,
                    value.directory_key,
                    value.model_dump_json(),
                    utc_now().isoformat(),
                ),
            )

    def get_profile(self, profile_id: str) -> BrowserProfile | None:
        with self._lock:
            row = self._connection.execute(
                "select data_json from browser_profiles where profile_id=?", (profile_id,)
            ).fetchone()
        return BrowserProfile.model_validate_json(row["data_json"]) if row else None

    def list_profiles(self, site_id: str | None = None) -> list[BrowserProfile]:
        sql = "select data_json from browser_profiles"
        params: tuple[object, ...] = ()
        if site_id:
            sql += " where site_id=?"
            params = (site_id,)
        sql += " order by profile_id"
        with self._lock:
            rows = self._connection.execute(sql, params).fetchall()
        return [BrowserProfile.model_validate_json(row["data_json"]) for row in rows]

    def get_runtime(self, runtime_key: str) -> SiteRuntimeState:
        with self._lock:
            row = self._connection.execute(
                "select data_json from site_runtime where runtime_key=?", (runtime_key,)
            ).fetchone()
        return (
            SiteRuntimeState.model_validate_json(row["data_json"])
            if row
            else SiteRuntimeState(runtime_key=runtime_key)
        )

    def save_runtime(self, state: SiteRuntimeState, *, expected_generation: int) -> None:
        value = state.model_copy(update={"updated_at": utc_now()})
        with self.transaction() as connection:
            row = connection.execute(
                "select generation from site_runtime where runtime_key=?", (state.runtime_key,)
            ).fetchone()
            current = int(row["generation"]) if row else 0
            if current != expected_generation:
                raise RuntimeError("site runtime generation conflict")
            connection.execute(
                """insert into site_runtime(runtime_key,data_json,generation,updated_at)
                   values(?,?,?,?) on conflict(runtime_key) do update set
                   data_json=excluded.data_json,generation=excluded.generation,
                   updated_at=excluded.updated_at""",
                (
                    value.runtime_key,
                    value.model_dump_json(),
                    value.generation,
                    value.updated_at.isoformat(),
                ),
            )

    def append_event(self, event: AccessEvent) -> None:
        with self.transaction() as connection:
            connection.execute(
                """insert or ignore into access_events(
                       event_id,operation_id,site_id,combination_id,occurred_at,category,payload_json
                   ) values(?,?,?,?,?,?,?)""",
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

    def list_events(
        self, *, site_id: str | None = None, since: datetime | None = None, limit: int = 200
    ) -> list[AccessEvent]:
        clauses: list[str] = []
        params: list[object] = []
        if site_id:
            clauses.append("site_id=?")
            params.append(site_id)
        if since:
            clauses.append("occurred_at>=?")
            params.append(since.astimezone(UTC).isoformat())
        sql = "select * from access_events"
        if clauses:
            sql += " where " + " and ".join(clauses)
        sql += " order by occurred_at desc limit ?"
        params.append(max(1, min(limit, 5000)))
        with self._lock:
            rows = self._connection.execute(sql, tuple(params)).fetchall()
        return [
            AccessEvent(
                event_id=row["event_id"],
                operation_id=row["operation_id"],
                site_id=row["site_id"],
                combination_id=row["combination_id"],
                occurred_at=row["occurred_at"],
                category=row["category"],
                payload=json.loads(row["payload_json"]),
            )
            for row in rows
        ]

    def save_body_outcome(self, value: BodyOutcome) -> None:
        with self.transaction() as connection:
            connection.execute(
                """insert into body_outcomes(
                       job_id,final_site_id,strategy_ref,combination_id,outcome,reason,
                       completed_at,payload_json
                   ) values(?,?,?,?,?,?,?,?) on conflict(job_id) do update set
                   final_site_id=excluded.final_site_id,strategy_ref=excluded.strategy_ref,
                   combination_id=excluded.combination_id,outcome=excluded.outcome,
                   reason=excluded.reason,completed_at=excluded.completed_at,
                   payload_json=excluded.payload_json""",
                (
                    value.job_id,
                    value.final_site_id,
                    value.strategy_ref,
                    value.combination_id,
                    value.outcome,
                    value.reason,
                    value.completed_at.isoformat(),
                    canonical_json(value.payload),
                ),
            )

    def body_stats(self, *, since: datetime, site_id: str | None = None) -> list[dict[str, object]]:
        params: list[object] = [since.astimezone(UTC).isoformat()]
        site_clause = ""
        if site_id:
            site_clause = " and final_site_id=?"
            params.append(site_id)
        with self._lock:
            rows = self._connection.execute(
                f"""select final_site_id,strategy_ref,coalesce(combination_id,'') combination_id,
                           outcome,coalesce(reason,'') reason,count(*) count
                    from body_outcomes where completed_at>=?{site_clause}
                    group by final_site_id,strategy_ref,combination_id,outcome,reason
                    order by final_site_id,count desc""",
                tuple(params),
            ).fetchall()
        return [dict(row) for row in rows]

    def access_stats(
        self, *, since: datetime, site_id: str | None = None
    ) -> list[dict[str, object]]:
        params: list[object] = [since.astimezone(UTC).isoformat()]
        site_clause = ""
        if site_id:
            site_clause = " and site_id=?"
            params.append(site_id)
        with self._lock:
            rows = self._connection.execute(
                f"""select site_id,
                           coalesce(json_extract(payload_json,'$.strategy_ref'),'') strategy_ref,
                           coalesce(combination_id,'') combination_id,category,
                           coalesce(json_extract(payload_json,'$.purpose'),'') purpose,
                           coalesce(json_extract(payload_json,'$.disposition'),'') disposition,
                           coalesce(json_extract(payload_json,'$.reason'),'') reason,
                           count(*) count
                    from access_events where occurred_at>=?{site_clause}
                    group by site_id,strategy_ref,combination_id,category,purpose,disposition,reason
                    order by site_id,count desc""",
                tuple(params),
            ).fetchall()
        return [dict(row) for row in rows]

    def prune_diagnostics(self, *, now: datetime | None = None) -> tuple[int, int]:
        instant = now or utc_now()
        with self.transaction() as connection:
            events = connection.execute(
                "delete from access_events where occurred_at<?",
                ((instant - timedelta(days=30)).isoformat(),),
            ).rowcount
            outcomes = connection.execute(
                "delete from body_outcomes where completed_at<?",
                ((instant - timedelta(days=90)).isoformat(),),
            ).rowcount
        return events, outcomes


__all__ = ["SiteStrategyRepository"]
