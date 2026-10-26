"""Bounded empty Fork inventory using the existing durable lifecycle protocol."""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import closing
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from doxagent.persistent_runtime_v2.w3_research_store import ENTRY, compact, sha

from .schema import W3PrepareRequest, WorkerRunRequest
from .startup_admission import startup_scope


class ReadySlotError(ValueError):
    def __init__(self, code, message, retryable=False):
        super().__init__(message)
        self.code, self.retryable = code, retryable


class W3ReadySlots:
    def __init__(self, jobs, workspaces, settings, mcp):
        self.jobs, self.workspaces, self.settings, self.mcp = jobs, workspaces, settings, mcp
        self.lock = asyncio.Lock()
        self.configs, self.tasks = {}, {}
        self.closed = False
        self._retire_cursor = ""
        self.retirement_status = {}
        with closing(jobs.store.connect()) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS w3_ready_slots (
                id TEXT PRIMARY KEY, scope TEXT NOT NULL, config TEXT NOT NULL,
                state TEXT NOT NULL, run_id TEXT NOT NULL, thread_id TEXT,
                case_id TEXT, record_id TEXT, error TEXT)""")
            columns = {r[1] for r in db.execute("PRAGMA table_info(w3_ready_slots)")}
            for column in (
                "created_at",
                "ready_at",
                "bound_at",
                "capsule_id",
                "generation",
                "claim_kind",
                "failed_at",
                "ready_recovery_count",
                "recovery_error",
                "prepare_deadline_at",
                "retire_next_at",
                "retire_checked_at",
                "retire_error",
            ):
                if column not in columns:
                    db.execute(f"ALTER TABLE w3_ready_slots ADD COLUMN {column} TEXT")
            # Old unclaimed slots lost their resident SDK on Worker restart.
            db.execute(
                "UPDATE w3_ready_slots SET state='RETIRING' WHERE state IN ('READY','BUILDING')"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS w3_ready_retirement ON w3_ready_slots(id) "
                "WHERE (state='RETIRING' AND case_id IS NULL) OR state='ABANDONED'"
            )

    async def configure(self, config):
        key = sha(compact([config["parent_thread_id"], config["profile"], ENTRY]))
        if not config.get("active_public", True) and key in self.configs:
            return key
        for old, previous in list(self.configs.items()):
            if (
                config.get("active_public", True)
                and old != key
                and previous["profile"]["model"] == config["profile"]["model"]
            ):
                self.configs.pop(old)
                with closing(self.jobs.store.connect()) as db, db:
                    db.execute(
                        "UPDATE w3_ready_slots SET state='RETIRING' "
                        "WHERE scope=? AND case_id IS NULL",
                        (old,),
                    )
        self.configs[key] = config
        # Count these as the existing warm inventory, not in addition to it.
        self.jobs._runtime.w3_warm_target = 1
        await self.fill(key)
        return key

    async def fill(self, key):
        admission = getattr(self.jobs._runtime, "admission", None)
        if admission is not None and not admission.allow_speculation():
            return
        async with self.lock:
            self.reconcile_residents()
            config = self.configs[key]
            if not config.get("active_public", True):
                return
            target = (
                self.settings.w3_ready_sol
                if config["profile"]["model"] in {"gpt-6-sol", "gpt-6.1-sol"}
                else self.settings.w3_ready_luna
            )
            with closing(self.jobs.store.connect()) as db, db:
                failures = db.execute(
                    "SELECT error,failed_at FROM w3_ready_slots "
                    "WHERE scope=? AND error IS NOT NULL AND failed_at IS NOT NULL "
                    "AND failed_at > COALESCE((SELECT max(ready_at) FROM w3_ready_slots "
                    "WHERE scope=?), '') ORDER BY failed_at DESC LIMIT 2",
                    (key, key),
                ).fetchall()
                if failures:
                    error, failed_at = failures[0]
                    if any(
                        word in error.lower()
                        for word in (
                            "invalid transport",
                            "invalid configuration",
                            "authentication",
                            "unauthorized",
                        )
                    ):
                        # A changed configuration has a different scope.
                        return
                    cooldown = 60 if len(failures) > 1 else 30
                    if datetime.now(UTC) < datetime.fromisoformat(failed_at) + timedelta(
                        seconds=cooldown
                    ):
                        return
                count = db.execute(
                    "SELECT count(*) FROM w3_ready_slots "
                    "WHERE scope=? AND state IN ('BUILDING','READY')",
                    (key,),
                ).fetchone()[0]
                building = db.execute(
                    "SELECT 1 FROM w3_ready_slots WHERE scope=? AND state='BUILDING' "
                    "AND case_id IS NULL LIMIT 1",
                    (key,),
                ).fetchone()
                if count < target and not building:
                    self._insert(db, key, config)

    def reconcile_residents(self):
        """Resource safety may reap an unused SDK; never advertise that slot ready."""
        pool = getattr(self.jobs._runtime, "pool", None)
        if pool is None:
            return
        live = {
            c.thread_id
            for c in pool
            if c.healthy and c.process is not None and c.process.returncode is None
        }
        with closing(self.jobs.store.connect()) as db, db:
            for identity, thread in db.execute(
                "SELECT id,thread_id FROM w3_ready_slots WHERE state='READY'"
            ).fetchall():
                if thread not in live:
                    db.execute(
                        "UPDATE w3_ready_slots SET state='RETIRING',error='resident reaped' "
                        "WHERE id=?",
                        (identity,),
                    )

    def _insert(self, db, key, config):
        identity = uuid4().hex
        db.execute(
            "INSERT INTO w3_ready_slots(id,scope,config,state,run_id,created_at,generation) "
            "VALUES(?,?,?,'BUILDING',?,?,?)",
            (
                identity,
                key,
                compact(config),
                "w3-ready-" + identity,
                datetime.now(UTC).isoformat(),
                getattr(self.jobs, "generation", None),
            ),
        )
        self.tasks[identity] = asyncio.create_task(self.build(identity, config))
        return identity

    async def build(self, identity, config):
        retry = False
        run_id = "w3-ready-" + identity
        try:
            await asyncio.to_thread(
                self.workspaces.materialize_case, run_id, {}, config["instructions"]
            )
            profile = config["profile"] | {
                "ticker": "GLOBAL",
                "data_mcp_enabled": "false",
                "research_assets_mcp_enabled": "true",
                "w3_entry_version": ENTRY,
                "web_search_mode": config.get("web_search_mode", "live"),
            }
            request = W3PrepareRequest(
                operation_key="w3-slot-fork:" + identity,
                record_id=identity,
                source_case_id=None,
                parent_thread_id=config["parent_thread_id"],
                run_id=run_id,
                attempt_id="prepared",
                research_pack_id="",
                reference_pack_id="",
                case_overlay_sha=sha(compact(config["instructions"])),
                profile=profile,
                capability_contract_version=ENTRY,
                cutoff_at=datetime.now(UTC),
            )
            with closing(self.jobs.store.connect()) as db:
                original = db.execute(
                    "SELECT request FROM w3_thread_operations WHERE operation_key=?",
                    (request.operation_key,),
                ).fetchone()
            if original:
                # Includes the original cutoff and profile. Never regenerate an
                # operation payload after restart or a lost binding response.
                request = W3PrepareRequest.model_validate_json(original[0])
            operation = await self.jobs.thread_lifecycle.prepare(request)
            while not self.closed and operation["status"] not in {"COMPLETED", "FAILED"}:
                await asyncio.sleep(0.1)
                operation = self.jobs.thread_lifecycle.operation(request.operation_key)
                if operation.get("step") == "RECONCILING":
                    with closing(self.jobs.store.connect()) as db:
                        bound = db.execute(
                            "SELECT case_id FROM w3_ready_slots WHERE id=?", (identity,)
                        ).fetchone()
                    if not bound or not bound[0]:
                        raise RuntimeError(
                            operation.get("last_error") or "speculative prepare failed"
                        )
                    operation = await self.jobs.thread_lifecycle.prepare(request)
            if operation["status"] != "COMPLETED":
                raise RuntimeError(operation.get("error_message") or "prepared Fork failed")
            thread_id = operation["thread_id"]
            capsule = next(
                (c for c in getattr(self.jobs._runtime, "pool", []) if c.thread_id == thread_id),
                None,
            )
            runtime = self.jobs._runtime
            if capsule is None and hasattr(runtime, "pool"):
                # The durable Fork survived a lost owner/receipt. Prepare that
                # exact child; never Fork another child for this operation.
                async def resume():
                    return await runtime.prepare_resume(
                        WorkerRunRequest(
                            workflow_version="persistent_runtime_w3_v1",
                            research_lane="persistent_runtime",
                            ticker="GLOBAL",
                            node="persistent_runtime_w3",
                            agent_role="w3_duty_trading_expert",
                            run_id=run_id,
                            attempt_id="prepared",
                            cutoff_at=request.cutoff_at,
                            prompt="",
                            output_schema={},
                            thread_id=thread_id,
                            model=profile["model"],
                            model_provider=profile.get("provider"),
                            effort=profile["effort"],
                            read_only=True,
                            data_mcp_enabled=False,
                            research_assets_mcp_enabled=True,
                            w3_entry_version=ENTRY,
                            web_search_mode=profile["web_search_mode"],
                        ),
                        self.workspaces.ensure_run(run_id),
                    )

                op_key = request.operation_key
                with closing(self.jobs.store.connect()) as db:
                    bound = db.execute(
                        "SELECT prepare_deadline_at,case_id FROM w3_ready_slots WHERE id=?",
                        (identity,),
                    ).fetchone()
                deadline = float(bound[0]) if bound and bound[0] else time.time() + 150
                work_deadline = deadline - 15
                previous_owner = self.jobs.thread_lifecycle.operation(op_key).get("owner_token")
                owner = uuid4().hex
                self.jobs.thread_lifecycle._operation_update(
                    op_key,
                    owner=previous_owner,
                    owner_token=owner,
                    execution_generation=getattr(self.jobs, "generation", None),
                )

                def resume_progress(stage, **values):
                    operation = self.jobs.thread_lifecycle.operation(op_key)
                    if operation.get("owner_token") != owner:
                        raise asyncio.CancelledError("resume owner replaced")
                    update = {"step": stage}
                    if stage == "BOOT_ATTEMPT":
                        count = int(operation.get("startup_attempts") or 0)
                        if count >= 2:
                            raise ValueError("prepare exhausted two physical boot attempts")
                        update["startup_attempts"] = str(count + 1)
                    self.jobs.thread_lifecycle._operation_update(op_key, owner=owner, **update)

                with startup_scope(
                    key=op_key,
                    priority=0 if bound and bound[1] else 1,
                    deadline=work_deadline,
                    progress=resume_progress,
                ):
                    async with asyncio.timeout(max(0.001, work_deadline - time.time())):
                        await resume()
                capsule = next(c for c in runtime.pool if c.thread_id == thread_id)
            if capsule:
                # tools/list must come from this resident SDK, not a second client.
                with closing(self.jobs.store.connect()) as db:
                    bound_deadline = db.execute(
                        "SELECT prepare_deadline_at FROM w3_ready_slots WHERE id=?", (identity,)
                    ).fetchone()[0]
                for _ in range(100):
                    if capsule.identity in self.mcp.ready:
                        break
                    if bound_deadline and time.time() >= float(bound_deadline) - 15:
                        raise TimeoutError("bound MCP readiness budget exhausted")
                    await asyncio.sleep(0.05)
                else:
                    raise RuntimeError("research MCP tools not ready in prepared capsule")
                capsule.ready_slot = True
                capsule.ready_profile = profile["model"]
            with closing(self.jobs.store.connect()) as db, db:
                db.execute(
                    "UPDATE w3_ready_slots SET ready_at=?,capsule_id=? WHERE id=?",
                    (
                        datetime.now(UTC).isoformat(),
                        capsule.identity if capsule else None,
                        identity,
                    ),
                )
                db.execute(
                    "UPDATE w3_ready_slots SET thread_id=?,state=CASE "
                    "WHEN state IN ('RETIRING','ABANDONED') THEN state "
                    "WHEN case_id IS NULL THEN 'READY' "
                    "ELSE 'BOUND' END WHERE id=?",
                    (thread_id, identity),
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            operation = self.jobs.thread_lifecycle.operation("w3-slot-fork:" + identity)
            with closing(self.jobs.store.connect()) as db, db:
                bound = db.execute(
                    "SELECT case_id,prepare_deadline_at FROM w3_ready_slots WHERE id=?", (identity,)
                ).fetchone()
                retry = bool(
                    bound
                    and bound[0]
                    and operation
                    and operation["status"] != "FAILED"
                    and not isinstance(exc, ValueError)
                    and int(operation.get("startup_attempts") or 0) < 2
                    and float(bound[1] or 0) > time.time() + 15
                    and getattr(exc, "code", None) != "CAPSULE_CLEANUP_QUARANTINED"
                )
                db.execute(
                    "UPDATE w3_ready_slots SET state=?,error=?,recovery_error=?,failed_at=? "
                    "WHERE id=?",
                    (
                        "BOUND" if retry else "RETIRING",
                        None if retry else str(exc),
                        str(exc),
                        datetime.now(UTC).isoformat(),
                        identity,
                    ),
                )
            if (
                operation
                and not retry
                and (bound and bound[0] or operation.get("fork_dispatched_at") == "")
            ):
                self.jobs.thread_lifecycle._operation_update(
                    "w3-slot-fork:" + identity,
                    owner=operation.get("owner_token"),
                    status="FAILED",
                    last_error=str(exc),
                    error_message=str(exc),
                )
        finally:
            self.tasks.pop(identity, None)
            if retry and not self.closed:

                async def resume_build():
                    await asyncio.sleep(1)
                    await self.build(identity, config)

                self.tasks[identity] = asyncio.create_task(resume_build())
            elif not self.closed:
                key = sha(compact([config["parent_thread_id"], config["profile"], ENTRY]))
                if key in self.configs:
                    await self.fill(key)

    async def retire(self, *, dry_run=False):
        if not getattr(self.jobs, "_processes_recovered", True):
            return []
        with closing(self.jobs.store.connect()) as db:
            db.row_factory = __import__("sqlite3").Row
            rows = db.execute(
                "SELECT id,run_id,thread_id,case_id,retire_next_at FROM w3_ready_slots "
                "WHERE ((state='RETIRING' AND case_id IS NULL) OR state='ABANDONED') "
                "AND id>? ORDER BY id LIMIT 4",
                (self._retire_cursor,),
            ).fetchall()
            if not rows and self._retire_cursor:
                self._retire_cursor = ""
                rows = db.execute(
                    "SELECT id,run_id,thread_id,case_id,retire_next_at FROM w3_ready_slots "
                    "WHERE (state='RETIRING' AND case_id IS NULL) OR state='ABANDONED' "
                    "ORDER BY id LIMIT 4"
                ).fetchall()
        preview = [dict(row) for row in rows]
        for value in preview:
            key = "w3-slot-fork:" + value["id"]
            op = self.jobs.thread_lifecycle.operation(key) or {}
            task = self.jobs.thread_lifecycle.preparations.get(key)
            build = self.tasks.get(value["id"])
            value.update(
                operation_status=op.get("status"),
                original_child=op.get("thread_id"),
                fork_dispatched_at=op.get("fork_dispatched_at"),
                owner_alive=(task is not None and not task.done())
                or (build is not None and not build.done()),
            )
        if dry_run:
            return preview
        # A bounded preview precedes each batch; no prompt/research bodies retained.
        if preview:
            self.jobs.store.metadata("w3_retirement_preview", preview)
        retired = deferred = 0
        for row in rows:
            identity, thread_id = row["id"], row["thread_id"]
            self._retire_cursor = identity
            task = self.tasks.get(identity)
            if (task is not None and not task.done()) or float(
                row["retire_next_at"] or 0
            ) > time.time():
                deferred += 1
                continue
            key = "w3-slot-fork:" + identity
            operation = self.jobs.thread_lifecycle.operation(key)
            owner = self.jobs.thread_lifecycle.preparations.get(key)
            if owner is not None and not owner.done():
                deferred += 1
                continue
            try:
                if operation and not (
                    operation.get("fork_dispatched_at") == ""
                    and not operation.get("thread_id")
                    and not thread_id
                ):
                    with startup_scope(
                        key="retire:" + identity, priority=2, deadline=time.time() + 15
                    ):
                        async with asyncio.timeout(15):
                            thread_id = await self.jobs.thread_lifecycle.retiring_child(
                                key, row["run_id"], thread_id, case=bool(row["case_id"])
                            )
                            await self.jobs.thread_lifecycle.delete(thread_id)
                elif thread_id:
                    await self.jobs.thread_lifecycle.delete(thread_id)
                else:
                    if operation:
                        self.jobs.thread_lifecycle._operation_update(
                            key,
                            owner=operation.get("owner_token"),
                            status="FAILED",
                            step="RETIRED",
                            error_code="W3_RETIRED_BEFORE_FORK",
                            error_message="Unowned inventory retired before Fork dispatch",
                        )
                    await asyncio.to_thread(
                        self.workspaces.retire_w3_ready_workspace,
                        row["run_id"],
                        preserve_evidence=bool(row["case_id"]),
                    )
            except Exception as exc:
                with closing(self.jobs.store.connect()) as db, db:
                    db.execute(
                        "UPDATE w3_ready_slots SET retire_next_at=?,retire_checked_at=?,"
                        "retire_error=? WHERE id=?",
                        (
                            str(time.time() + 60),
                            datetime.now(UTC).isoformat(),
                            str(exc)[:500],
                            identity,
                        ),
                    )
                deferred += 1
                continue
            with closing(self.jobs.store.connect()) as db, db:
                db.execute(
                    "UPDATE w3_ready_slots SET state='RETIRED',thread_id=?,retire_checked_at=?,"
                    "retire_error=NULL,retire_next_at=NULL WHERE id=?",
                    (thread_id, datetime.now(UTC).isoformat(), identity),
                )
            retired += 1
        with closing(self.jobs.store.connect()) as db:
            count, oldest = db.execute(
                "SELECT count(*),min(created_at) FROM w3_ready_slots "
                "WHERE (state='RETIRING' AND case_id IS NULL) OR state='ABANDONED'"
            ).fetchone()
        self.retirement_status = dict(
            pending=count,
            oldest_created_at=oldest,
            scanned=len(rows),
            retired=retired,
            deferred=deferred,
            scanned_unowned_creating=sum(
                value["operation_status"] == "CREATING" and not value["owner_alive"]
                for value in preview
            ),
            scanned_needs_confirmation=sum(
                not value["owner_alive"]
                and not value["original_child"]
                and not value["thread_id"]
                and value["fork_dispatched_at"] != ""
                for value in preview
            ),
        )
        return preview

    async def claim(self, request):
        key = sha(compact([request["parent_thread_id"], request["profile"], ENTRY]))
        async with self.lock:
            self.reconcile_residents()
            with closing(self.jobs.store.connect()) as db, db:
                db.row_factory = __import__("sqlite3").Row
                db.execute("BEGIN IMMEDIATE")
                row = db.execute(
                    "SELECT * FROM w3_ready_slots WHERE case_id=? AND record_id=?",
                    (request["case_id"], request["record_id"]),
                ).fetchone()
                if row and row["scope"] != key:
                    raise ReadySlotError(
                        "w3_ready_identity_conflict", "ready-slot claim parameters changed"
                    )
                if row and row["error"]:
                    operation = self.jobs.thread_lifecycle.operation("w3-slot-fork:" + row["id"])
                    if not operation or operation["status"] == "FAILED":
                        raise ReadySlotError(
                            "w3_ready_prepare_failed",
                            "ready-slot preparation failed: " + row["error"],
                        )
                    # Transient operations retain their identity and physical startup budget.
                    db.execute(
                        "UPDATE w3_ready_slots SET state='BOUND',error=NULL WHERE id=?",
                        (row["id"],),
                    )
                    row = db.execute(
                        "SELECT * FROM w3_ready_slots WHERE id=?", (row["id"],)
                    ).fetchone()
                if row and not row["thread_id"] and row["id"] not in self.tasks:
                    # Restart/lost claim response: continue this original durable
                    # operation, even if its old Template is no longer the default.
                    self.tasks[row["id"]] = asyncio.create_task(
                        self.build(row["id"], json.loads(row["config"]))
                    )
                if row is None:
                    row = db.execute(
                        "SELECT * FROM w3_ready_slots WHERE scope=? "
                        "AND state IN ('READY','BUILDING') AND case_id IS NULL "
                        "ORDER BY state DESC LIMIT 1",
                        (key,),
                    ).fetchone()
                    if row is None:
                        config = self.configs.get(key)
                        if config is None:
                            raise ReadySlotError(
                                "w3_ready_unconfigured",
                                "public Template has not configured this profile",
                            )
                        identity = self._insert(db, key, config)
                    else:
                        identity = row["id"]
                    db.execute(
                        "UPDATE w3_ready_slots SET state='BOUND',case_id=?,record_id=?,"
                        "bound_at=?,claim_kind=? WHERE id=?",
                        (
                            request["case_id"],
                            request["record_id"],
                            datetime.now(UTC).isoformat(),
                            "POOL_HIT" if row and row["state"] == "READY" else "POOL_MISS",
                            identity,
                        ),
                    )
                    row = db.execute(
                        "SELECT * FROM w3_ready_slots WHERE id=?", (identity,)
                    ).fetchone()
                if not row["prepare_deadline_at"]:
                    db.execute(
                        "UPDATE w3_ready_slots SET prepare_deadline_at=? WHERE id=?",
                        (
                            str(
                                time.time()
                                + getattr(self.settings, "w3_prepare_timeout_seconds", 180)
                            ),
                            row["id"],
                        ),
                    )
                if row["thread_id"]:
                    db.execute(
                        "UPDATE w3_thread_registry SET kind='CASE' WHERE thread_id=?",
                        (row["thread_id"],),
                    )
                result = dict(
                    status="COMPLETED" if row["thread_id"] else "CREATING",
                    thread_id=row["thread_id"],
                    run_id=row["run_id"],
                    slot_id=row["id"],
                    error=row["error"],
                    capsule_id=row["capsule_id"],
                    claim_kind=row["claim_kind"],
                    bind_operation_key="w3-slot-bind:"
                    + request["case_id"]
                    + ":"
                    + request["record_id"],
                )
        if key in self.configs:
            await self.fill(key)
        return result

    async def close(self):
        self.closed = True
        for task in self.tasks.values():
            task.cancel()
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)
