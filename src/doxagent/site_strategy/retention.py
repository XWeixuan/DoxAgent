"""Bounded access-owner retention, independent of browser prewarm/probe loops."""

import asyncio
import sqlite3
import time
from datetime import timedelta

from .schema import utc_now


def migrate_retention(connection):
    connection.executescript("""
        create index if not exists access_events_utc_time on access_events(julianday(occurred_at));
        create index if not exists body_outcomes_utc_time on body_outcomes(julianday(completed_at));
        create table if not exists access_hourly_summaries (
            site_id text not null, identity_id text not null, hour text not null,
            outcome text not null, count integer not null,
            primary key(site_id,identity_id,hour,outcome));
        create table if not exists access_failure_shapes (
            fingerprint text primary key, scope_id text not null, identity_id text not null,
            payload_json text not null, first_seen text not null, last_seen text not null,
            seen_count integer not null);
        create index if not exists access_failure_shape_time on access_failure_shapes(last_seen);
        create index if not exists owned_target_retention_utc
            on owned_browser_targets(julianday(updated_at)) where state in ('CLOSED','LOST');
        create index if not exists owned_target_active_instance
            on owned_browser_targets(identity_id,browser_instance_id)
            where state not in ('CLOSED','LOST');
        create index if not exists legacy_success_retention_utc
            on access_events(julianday(occurred_at)) where category='ACCESS_SUCCEEDED';
        create index if not exists operation_retention_utc
            on maintenance_operations(julianday(updated_at))
            where state in ('DONE','FAILED','CANCELLED','INTERRUPTED');
        create index if not exists challenge_retention_utc
            on challenge_tasks(julianday(updated_at))
            where state='RESOLVED' or superseded_by is not null;
        create index if not exists session_retention_utc
            on maintenance_sessions(julianday(updated_at)) where state='CLOSED';
        create index if not exists failure_shape_retention_utc
            on access_failure_shapes(julianday(last_seen));
        create index if not exists hourly_summary_retention_utc
            on access_hourly_summaries(julianday(hour));
        create index if not exists success_event_retention_utc
            on access_events(julianday(occurred_at)) where category='ACCESS_RESULT'
            and json_extract(payload_json,'$.disposition')='SUCCESS';
        create index if not exists live_challenge_allocation
            on challenge_tasks(json_extract(data_json,'$.allocation_id'))
            where state<>'RESOLVED' and superseded_by is null;
        create index if not exists live_recovery_allocation
            on browser_recoveries(json_extract(data_json,'$.original_allocation_id'))
            where phase<>'RESOLVED';
    """)


class RetentionWorker:
    def __init__(self, repository, *, batch_size=2000, seconds=2):
        self.repository = repository
        self.batch_size = min(2000, max(1, batch_size))
        self.seconds = min(2, max(0.01, seconds))
        self.metrics = {}
        self.resource_cleanup = None

    def _delete(self, connection, table, column, condition, threshold, deadline):
        connection.set_progress_handler(lambda:time.monotonic()>=deadline,1000)
        try:
            return connection.execute(
                f"delete from {table} where rowid in (select rowid from {table} "
                f"where julianday({column})<julianday(?) and ({condition}) limit ?)",
                (threshold,self.batch_size)).rowcount
        finally:
            connection.set_progress_handler(None,0)

    def run_once(self, now=None):
        instant = now or utc_now()
        started = time.monotonic()
        deleted = {}
        rules = [
            ("browser_recoveries", "updated_at", 30, "phase='RESOLVED'"),
            (
                "datadome_solver_jobs",
                "updated_at",
                30,
                "not exists (select 1 from browser_recoveries r where "
                "json_extract(r.data_json,'$.recovery_id')=datadome_solver_jobs.recovery_id "
                "and (r.phase<>'RESOLVED' or julianday(r.updated_at)>julianday('now','-7 days'))) "
                "and not exists (select 1 from challenge_tasks t where t.state<>'RESOLVED' "
                "and t.superseded_by is null and json_extract(t.data_json,'$.recovery_id')=datadome_solver_jobs.recovery_id) "
                "and coalesce(json_extract(data_json,'$.status'),'') not in ('PENDING','SUBMITTING','APPLYING')",
            ),
            ("access_failure_shapes", "last_seen", 7, "1=1"),
            ("access_hourly_summaries", "hour", 90, "1=1"),
            (
                "owned_browser_targets",
                "updated_at",
                7,
                "state in ('CLOSED','LOST') and not exists (select 1 from challenge_tasks t "
                "where t.state<>'RESOLVED' and t.superseded_by is null "
                "and json_extract(t.data_json,'$.allocation_id')=owned_browser_targets.allocation_id) "
                "and not exists (select 1 from maintenance_sessions s where s.state<>'CLOSED' "
                "and json_extract(s.data_json,'$.allocation_id')=owned_browser_targets.allocation_id) "
                "and not exists (select 1 from browser_recoveries r where r.phase<>'RESOLVED' "
                "and json_extract(r.data_json,'$.original_allocation_id')=owned_browser_targets.allocation_id) "
                "and not exists (select 1 from datadome_solver_jobs j where "
                "json_extract(j.data_json,'$.allocation_id')=owned_browser_targets.allocation_id "
                "and json_extract(j.data_json,'$.status') in ('PENDING','SUBMITTING','APPLYING'))",
            ),
            (
                "maintenance_operations",
                "updated_at",
                7,
                "state in ('DONE','FAILED','CANCELLED','INTERRUPTED')",
            ),
            ("challenge_tasks", "updated_at", 30, "state='RESOLVED' or superseded_by is not null"),
            ("maintenance_sessions", "updated_at", 7, "state='CLOSED'"),
            ("body_outcomes", "completed_at", 90, "1=1"),
        ]
        # Summarize only the rows removed in this transaction, preventing double counts.
        with self.repository.transaction() as connection:
            threshold = (instant - timedelta(days=7)).isoformat()
            rows = connection.execute(
                "select rowid,site_id,occurred_at,payload_json from access_events "
                "where julianday(occurred_at)<julianday(?) and category='ACCESS_RESULT' "
                "and json_extract(payload_json,'$.disposition')='SUCCESS' "
                "order by julianday(occurred_at) limit ?",
                (threshold, self.batch_size),
            ).fetchall()
            if rows:
                import json
                from datetime import UTC, datetime

                for row in rows:
                    payload = json.loads(row["payload_json"])
                    hour = (
                        datetime.fromisoformat(row["occurred_at"])
                        .astimezone(UTC)
                        .strftime(
                            "%Y-%m-%dT%H:00:00Z",
                        )
                    )
                    connection.execute(
                        "insert into access_hourly_summaries values(?,?,?,?,1) "
                        "on conflict(site_id,identity_id,hour,outcome) do update set count=count+1",
                        (row["site_id"], payload.get("identity_id") or "", hour, "SUCCESS"),
                    )
                connection.executemany(
                    "delete from access_events where rowid=?", [(row["rowid"],) for row in rows]
                )
            deleted["success_events"] = len(rows)
        with self.repository.transaction() as connection:
            legacy_done=connection.execute("SELECT 1 FROM site_recovery_migrations "
                "WHERE name='legacy-success-retention-complete'").fetchone()
        if not legacy_done:
            rules.insert(0, ("access_events", "occurred_at", 7, "category='ACCESS_SUCCEEDED'"))
        rules.append(
            (
                "access_events",
                "occurred_at",
                30,
                "category<>'ACCESS_RESULT' or "
                "coalesce(json_extract(payload_json,'$.disposition'),'')<>'SUCCESS'",
            )
        )
        pending = {}
        for table, column, days, condition in rules:
            if time.monotonic() - started >= self.seconds:
                break
            threshold = (instant - timedelta(days=days)).isoformat()
            with self.repository.transaction() as connection:
                # julianday normalizes old ISO UTC offsets instead of comparing strings.
                try:
                    count=self._delete(connection,table,column,condition,threshold,started+self.seconds)
                except sqlite3.OperationalError as exc:
                    if getattr(exc,'sqlite_errorcode',None)!=sqlite3.SQLITE_INTERRUPT:raise
                    break
                deleted[table] = count
                pending[f"{table}:{days}"] = {
                    "rows_at_least": 0,
                    "oldest_eligible": None,
                    "truncated": count == self.batch_size,
                }
                if condition == "category='ACCESS_SUCCEEDED'" and count < self.batch_size:
                    if not connection.execute("SELECT 1 FROM access_events WHERE category='ACCESS_SUCCEEDED' LIMIT 1").fetchone():
                        connection.execute("INSERT OR IGNORE INTO site_recovery_migrations VALUES(?,?)",
                            ('legacy-success-retention-complete',instant.isoformat()))
        self.metrics = {
            "at": instant.isoformat(),
            "deleted": deleted,
            "backlog": pending,
            "seconds": time.monotonic() - started,
            "physical_space_reclaimed": False,
        }
        return self.metrics

    async def run(self):
        while True:
            try:
                await asyncio.to_thread(self.run_once)
                if self.resource_cleanup:
                    await self.resource_cleanup()
            except Exception:
                self.metrics = {**self.metrics, "error": "retention_failed"}
            await asyncio.sleep(600)
