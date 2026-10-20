"""Bounded empty Fork inventory using the existing durable lifecycle protocol."""

from __future__ import annotations

import asyncio
import json
from contextlib import closing
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from doxagent.persistent_runtime_v2.w3_research_store import ENTRY, compact, sha

from .schema import W3PrepareRequest, WorkerRunRequest


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
            ):
                if column not in columns:
                    db.execute(f"ALTER TABLE w3_ready_slots ADD COLUMN {column} TEXT")
            # Old unclaimed slots lost their resident SDK on Worker restart.
            db.execute(
                "UPDATE w3_ready_slots SET state='RETIRING' WHERE state IN ('READY','BUILDING')"
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
        async with self.lock:
            self.reconcile_residents()
            config = self.configs[key]
            if not config.get("active_public", True):
                return
            target = (
                self.settings.w3_ready_sol
                if config["profile"]["model"] == "gpt-6-sol"
                else self.settings.w3_ready_luna
            )
            with closing(self.jobs.store.connect()) as db, db:
                failed = db.execute(
                    "SELECT error FROM w3_ready_slots WHERE scope=? AND failed_at>? "
                    "AND error IS NOT NULL ORDER BY failed_at DESC LIMIT 1",
                    (key, (datetime.now(UTC) - timedelta(seconds=60)).isoformat()),
                ).fetchone()
                if failed and any(
                    x in failed[0].lower()
                    for x in (
                        "invalid transport",
                        "invalid configuration",
                        "authentication",
                        "unauthorized",
                    )
                ):
                    return
                count = db.execute(
                    "SELECT count(*) FROM w3_ready_slots "
                    "WHERE scope=? AND state IN ('BUILDING','READY')",
                    (key,),
                ).fetchone()[0]
                for _ in range(max(0, target - count)):
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
                await runtime.prepare_resume(
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
                capsule = next(c for c in runtime.pool if c.thread_id == thread_id)
            if capsule:
                # tools/list must come from this resident SDK, not a second client.
                for _ in range(100):
                    if capsule.identity in self.mcp.ready:
                        break
                    await asyncio.sleep(0.05)
                else:
                    raise RuntimeError("research MCP tools not ready in prepared capsule")
                capsule.ready_slot = True
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
            with closing(self.jobs.store.connect()) as db, db:
                db.execute(
                    "UPDATE w3_ready_slots SET state='RETIRING',error=?,failed_at=? WHERE id=?",
                    (str(exc), datetime.now(UTC).isoformat(), identity),
                )
        finally:
            self.tasks.pop(identity, None)

    async def retire(self):
        with closing(self.jobs.store.connect()) as db:
            rows = db.execute(
                "SELECT id,thread_id FROM w3_ready_slots "
                "WHERE (state='RETIRING' AND case_id IS NULL) OR state='ABANDONED' LIMIT 4"
            ).fetchall()
        for identity, thread_id in rows:
            if identity in self.tasks:
                continue
            if not thread_id:
                operation = self.jobs.thread_lifecycle.operation("w3-slot-fork:" + identity)
                if operation and operation["status"] == "CREATING":
                    continue
                thread_id = operation.get("thread_id") if operation else None
            if thread_id:
                try:
                    await self.jobs.thread_lifecycle.delete(thread_id)
                except Exception:
                    continue
            else:
                await asyncio.to_thread(
                    self.workspaces.retire_w3_ready_workspace,
                    "w3-ready-" + identity,
                    preserve_evidence=False,
                )
            with closing(self.jobs.store.connect()) as db, db:
                db.execute("UPDATE w3_ready_slots SET state='RETIRED' WHERE id=?", (identity,))

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
                    op_key = "w3-slot-fork:" + row["id"]
                    operation = self.jobs.thread_lifecycle.operation(op_key)
                    has_job = db.execute(
                        "SELECT 1 FROM jobs WHERE json_extract(request,'$.run_id')=? LIMIT 1",
                        (row["run_id"],),
                    ).fetchone()
                    retryable = (
                        operation
                        and operation["status"] in {"CREATING", "FAILED"}
                        and operation.get("error_code") in {"TimeoutError", "CancelledError", None}
                        and not row["thread_id"]
                        and not operation.get("thread_id")
                        and not has_job
                        and int(row["ready_recovery_count"] or 0) < 1
                    )
                    if not retryable:
                        raise ReadySlotError(
                            "w3_ready_prepare_failed",
                            "ready-slot preparation failed: " + row["error"],
                        )
                    if row["id"] in self.tasks:
                        return dict(status="CREATING", thread_id=None, slot_id=row["id"])
                    # Resume the exact durable request; lifecycle reconciles the original cwd
                    # and SDK receipt before considering a Fork. Never replace an accepted turn.
                    db.execute(
                        "UPDATE w3_thread_operations SET status='CREATING',step='RECONCILING',"
                        "recovery_count='0' WHERE operation_key=? AND status='FAILED'",
                        (op_key,),
                    )
                    db.execute(
                        "UPDATE w3_ready_slots SET state='BOUND',recovery_error=error,error=NULL,"
                        "ready_recovery_count='1' WHERE id=?",
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
