"""Opt-in paid DataDome recovery. Durable task IDs, short ticks, no success without business proof."""

import json
from contextlib import nullcontext
from datetime import datetime, timedelta
from urllib.parse import quote

import httpx

from .datadome_browser import NativeChallenge
from .schema import digest_json, new_id, utc_now


def mark_solver_outcome(
    repository,
    identity_id,
    field,
    *,
    recovery_id=None,
    purpose=None,
    probe_id=None,
    _connection=None,
):
    """Link only the current identity's exact incident; never infer cross-incident success."""
    from .governance_repository import GovernanceRepository

    current = GovernanceRepository(repository).get_recovery(identity_id)
    if not current or (recovery_id and current.recovery_id != recovery_id):
        return
    with nullcontext(_connection) if _connection is not None else repository.transaction() as db:
        rows = db.execute(
            "select data_json from datadome_solver_jobs where recovery_id=?", (current.recovery_id,)
        ).fetchall()
        for row in rows:
            job = json.loads(row[0])
            if job.get("status") != "APPLIED":
                continue
            if field == "natural_success_at" and purpose not in job.get("verified_purposes", []):
                continue
            if (
                field == "route_activated_at"
                and job.get("verification_state") != "BUSINESS_VERIFIED"
            ):
                continue
            now = utc_now().isoformat()
            job.setdefault(field, now)
            if field == "business_verified_at":
                job.update(
                    verification_state="BUSINESS_VERIFIED",
                    business_probe_id=probe_id,
                    verified_purposes=list(
                        dict.fromkeys([*job.get("verified_purposes", []), purpose])
                    ),
                )
                job["verification_reason"] = (
                    "late_recovery"
                    if not job.get("applied_at")
                    or (utc_now() - datetime.fromisoformat(job["applied_at"])).total_seconds() > 120
                    else "confirmation_window_business_proof"
                )
            job["updated_at"] = now
            db.execute(
                "update datadome_solver_jobs set updated_at=?,data_json=? where job_id=?",
                (now, json.dumps(job), job["job_id"]),
            )


class SolverError(RuntimeError):
    pass


def scene_key(scene):
    # Presentation state may change while the same paid challenge is processing.
    return digest_json({key: scene.get(key) for key in ("url", "website", "user_agent")})


class TwoCaptcha:
    async def request(self, key, method, **payload):
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=12) as client:
                response = await client.post(
                    "https://api.2captcha.com/" + method, json={"clientKey": key, **payload}
                )
                response.raise_for_status()
                data = response.json()
        except (httpx.HTTPError, ValueError):
            raise SolverError("PROVIDER_TRANSPORT_UNCERTAIN") from None
        if data.get("errorId"):
            code = str(data.get("errorCode", "PROVIDER_ERROR"))
            raise SolverError(
                code if code.isascii() and code.replace("_", "").isalnum() else "PROVIDER_ERROR"
            )
        return data


class DataDomeSolver:
    def __init__(self, coordinator):
        self.coordinator = coordinator
        self.provider = TwoCaptcha()
        self.browser_factory = NativeChallenge
        self.provider_ticks = set()

    def config(self):
        credentials = getattr(self.coordinator.service, "credential_store", None)
        path = credentials.root / "datadome-solver.json" if credentials else None
        if not path or not path.is_file():
            return {}
        try:
            if path.stat().st_size > 65536:
                return {}
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def jobs(self, recovery_id):
        repo = self.coordinator.service.repository
        with repo._lock:
            return [
                json.loads(r[0])
                for r in repo._connection.execute(
                    "select data_json from datadome_solver_jobs where recovery_id=? order by created_at",
                    (recovery_id,),
                )
            ]

    def normalize_legacy_jobs(self):
        """Additive metadata only. Never resets a paid reservation or claims recovery."""
        repo = self.coordinator.service.repository
        with repo.transaction() as db:
            rows = db.execute(
                "select data_json from datadome_solver_jobs where "
                "(json_extract(data_json,'$.status')='APPLIED' and "
                "json_extract(data_json,'$.verification_state') is null) or "
                "(json_extract(data_json,'$.status')='FAILED' and "
                "coalesce(json_extract(data_json,'$.verification_state'),'PENDING')='PENDING') limit 200"
            ).fetchall()
            for row in rows:
                job = json.loads(row[0])
                job.update(
                    verification_state="STILL_BLOCKED"
                    if job.get("reason") == "ERROR_CAPTCHA_UNSOLVABLE"
                    else "INDETERMINATE",
                    verification_reason=job.get("reason") or "legacy_application_without_proof",
                )
                if job.get("cost") not in {None, "unknown"}:
                    job["provider_cost"] = job["cost"]
                db.execute(
                    "update datadome_solver_jobs set data_json=? where job_id=?",
                    (json.dumps(job), job["job_id"]),
                )

    def save(self, job):
        job["updated_at"] = utc_now().isoformat()
        with self.coordinator.service.repository.transaction() as db:
            db.execute(
                "insert into datadome_solver_jobs values(?,?,?,?,?,?) "
                "on conflict(job_id) do update set updated_at=excluded.updated_at,data_json=excluded.data_json",
                (
                    job["job_id"],
                    job["recovery_id"],
                    job["identity_id"],
                    job["created_at"],
                    job["updated_at"],
                    json.dumps(job),
                ),
            )
        return job

    async def collect_orphan_results(self):
        """Bounded billing collection after manual recovery/replacement. Never applies a solution."""
        repo = self.coordinator.service.repository
        config = self.config()
        if not config.get("enabled"):
            return
        with repo._lock:
            rows = repo._connection.execute(
                "select j.data_json from datadome_solver_jobs j where "
                "json_extract(j.data_json,'$.status') in ('PENDING','SUBMITTING','APPLYING') and not exists ("
                "select 1 from browser_recoveries r where r.phase<>'RESOLVED' and "
                "json_extract(r.data_json,'$.recovery_id')=j.recovery_id) limit 2"
            ).fetchall()
        for row in rows:
            job = json.loads(row[0])
            now = utc_now()
            if job["status"] != "PENDING":
                job.update(
                    status="FAILED",
                    verification_state="INDETERMINATE",
                    reason="INTERRUPTED_INCIDENT_ENDED",
                )
                self.save(job)
                continue
            if job.get("last_provider_poll_at") and now - datetime.fromisoformat(
                job["last_provider_poll_at"]
            ) < timedelta(seconds=15):
                continue
            if now - datetime.fromisoformat(job.get("submitted_at", job["created_at"])) > timedelta(
                seconds=240
            ):
                job.update(
                    status="FAILED",
                    verification_state="INDETERMINATE",
                    reason="SOLVER_DEADLINE_EXCEEDED",
                )
                self.save(job)
                continue
            job["last_provider_poll_at"] = now.isoformat()
            self.save(job)
            try:
                data = await self.provider.request(
                    config["api_key"], "getTaskResult", taskId=job["task_id"]
                )
                if data.get("status") == "ready":
                    job.update(
                        status="FAILED",
                        provider_ready_at=utc_now().isoformat(),
                        provider_cost=data.get("cost"),
                        cost=data.get("cost"),
                        verification_state="SUPERSEDED",
                        reason="INCIDENT_ENDED_RESULT_NOT_APPLIED",
                    )
                    self.save(job)
            except SolverError:
                pass  # The same durable task remains eligible until its bounded deadline.

    def new_job(self, recovery, scene, binding):
        now = utc_now().isoformat()
        return self.save(
            {
                "job_id": new_id("dd"),
                "recovery_id": recovery.recovery_id,
                "identity_id": recovery.identity_id,
                "created_at": now,
                "status": "PREPARING",
                "scene": scene_key(scene),
                "binding": binding,
                "task_id": None,
                "verification_state": "PENDING",
                "allocation_id": recovery.original_allocation_id,
            }
        )

    async def egress_matches(self, binding, egress):
        if (
            binding.get("egress_id") != egress.egress_id
            or not egress.observed_ip
            or not egress.observed_at
        ):
            return False
        if utc_now() - egress.observed_at > timedelta(hours=1):
            return False
        proxy = (
            "http://"
            + quote(binding["proxyLogin"], safe="")
            + ":"
            + quote(binding["proxyPassword"], safe="")
            + "@"
            + binding["localAddress"]
            + ":"
            + str(binding["proxyPort"])
        )
        try:
            async with httpx.AsyncClient(proxy=proxy, trust_env=False, timeout=8) as client:
                response = await client.get("https://api.ipify.org")
                response.raise_for_status()
                return response.text.strip() == egress.observed_ip
        except httpx.HTTPError:
            return False

    async def tick(self, recovery, scope, identity, egress):
        """Return True while a task/retry owns the page, False for normal recovery/manual fallback."""
        # Bound the per-tick signal to the two concurrently leased identities.
        self.provider_ticks = {
            key
            for key in self.provider_ticks
            if any(
                r.recovery_id == key and r.phase != "RESOLVED"
                for r in self.coordinator.store.list_recoveries()
            )
        }
        self.provider_ticks.discard(recovery.recovery_id)
        config = self.config()
        if not config.get("enabled") or recovery.site_id not in config.get("sites", ["reuters"]):
            return False
        if (
            recovery.assessment.get("provider") != "DATADOME"
            or identity.runtime_kind.value != "external_chrome"
        ):
            return False
        binding = config.get("bindings", {}).get(identity.identity_id)
        jobs = self.jobs(recovery.recovery_id)
        active = jobs[-1] if jobs else None
        if not active and utc_now() - recovery.opened_at > timedelta(days=7):
            return False  # Historic GC may have removed an already-paid tombstone.
        if active and active["status"] in {"FAILED", "APPLIED", "SUBMITTING", "APPLYING"}:
            # SUBMITTING after interruption is ambiguous. Never silently buy another task.
            if active["status"] == "SUBMITTING":
                self.fail(active, recovery, "SUBMISSION_OUTCOME_UNKNOWN")
            elif active["status"] == "APPLYING":
                active.update(
                    status="APPLIED",
                    verification_state="INDETERMINATE",
                    verification_reason="application_interrupted",
                )
                self.save(active)
            elif active["status"] == "APPLIED" and not active.get("verification_state"):
                active.update(
                    verification_state="INDETERMINATE",
                    verification_reason="legacy_application_without_proof",
                )
                self.save(active)
            return False
        if active and active["status"] == "PENDING":
            if utc_now() - datetime.fromisoformat(
                active.get("submitted_at", active["created_at"])
            ) > timedelta(seconds=240):
                self.fail(active, recovery, "SOLVER_DEADLINE_EXCEEDED")
                return False
        data = None
        try:
            # Collect a paid result even if the page/egress has changed or a user
            # owns the Viewer. Billing does not require a Profile writer.
            if active and active["status"] == "PENDING":
                self.provider_ticks.add(recovery.recovery_id)
                data = await self.provider.request(
                    config["api_key"], "getTaskResult", taskId=active["task_id"]
                )
                if data.get("status") == "processing":
                    return True
                if data.get("status") != "ready":
                    self.fail(active, recovery, "INVALID_PROVIDER_RESPONSE")
                    return False
                active.setdefault("provider_ready_at", utc_now().isoformat())
                active.update(
                    provider_cost=data.get("cost", active.get("provider_cost")),
                    cost=data.get("cost", active.get("cost")),
                    verification_state="PENDING",
                )
                self.save(active)  # Persist the cost BEFORE any browser/ownership work.
            if not binding or recovery.page_status == "LOST":
                if data:
                    self.fail(active, recovery, "PAID_RESULT_ORIGINAL_UNAVAILABLE")
                return False
            async with self.coordinator.service.profile_use.recovery(
                identity, recovery.recovery_id, recovery.generation
            ):
                browser = self.browser_factory(self.coordinator, recovery, identity)
                scene = await browser.inspect()
                if not scene.get("website") or not scene.get("user_agent"):
                    return False
                if self.coordinator.service.resolve(scene["website"]).site_id != recovery.site_id:
                    return False
                signature = scene_key(scene)
                route = digest_json(
                    [
                        identity.revision,
                        egress.generation,
                        egress.observed_ip,
                        binding,
                        recovery.original_instance_id,
                        recovery.original_target_id,
                    ]
                )
                if active and active["status"] == "PENDING":
                    if active["scene"] != signature or active["binding"] != route:
                        self.fail(active, recovery, "SCENE_OR_EGRESS_CHANGED")
                        return False
                    current_egress = self.coordinator.service.repository.get_egress(
                        identity.egress_id
                    )
                    if (current_egress.generation, current_egress.observed_ip) != (
                        egress.generation,
                        egress.observed_ip,
                    ):
                        self.fail(active, recovery, "EGRESS_CHANGED")
                        return False
                    latest_scene = await browser.inspect()
                    if scene_key(latest_scene) != signature or not await self.egress_matches(
                        binding, current_egress
                    ):
                        self.fail(active, recovery, "SCENE_OR_EGRESS_CHANGED")
                        return False
                    active.update(status="APPLYING", verification_reason="application_in_progress")
                    self.save(active)  # Crash here is uncertain, never an invitation to set again.
                    await browser.apply(data.get("solution", {}).get("cookie"), scene["website"])
                    active.update(
                        status="APPLIED",
                        applied_at=utc_now().isoformat(),
                        verification_state="PENDING",
                    )
                    active["cookie_scope_verified"] = True
                    self.save(active)
                    self.coordinator.event(
                        recovery,
                        "DATADOME_SOLVER_APPLIED",
                        task_id=active["task_id"],
                        cost=active["cost"],
                    )
                    return (
                        True  # Next tick must prove the real listing/body, never mark healthy here.
                    )
                if scene["state"] in {"RETRY", "IP_BANNED", "UNAVAILABLE", "INTERSTITIAL"}:
                    if active and active["status"] == "WAITING_SLIDER":
                        reason = "DATADOME_" + scene["state"]
                        if active.get("reason") != reason:
                            active["reason"] = reason
                            self.save(active)
                            self.coordinator.event(
                                recovery, "DATADOME_SOLVER_WAITING", reason=reason
                            )
                        return False
                    if active and active["status"] == "REGENERATED":
                        if utc_now() - datetime.fromisoformat(active["created_at"]) < timedelta(
                            seconds=60
                        ):
                            return True
                        active.update(status="WAITING_SLIDER", reason="DATADOME_" + scene["state"])
                        self.save(active)
                        self.coordinator.event(
                            recovery, "DATADOME_SOLVER_WAITING", reason=active["reason"]
                        )
                        return False
                    active = self.new_job(recovery, scene, route)
                    active["status"] = "REGENERATED"
                    self.save(
                        active
                    )  # Persist before mutating; restart cannot create a reload loop.
                    await browser.reload()
                    self.coordinator.event(
                        recovery, "DATADOME_RETRY_REGENERATED", previous_state=scene["state"]
                    )
                    return True
                if scene["state"] != "SOLVABLE":
                    return False
                if not await self.egress_matches(binding, egress):
                    self.coordinator.event(
                        recovery, "DATADOME_SOLVER_UNAVAILABLE", reason="PROXY_EGRESS_NOT_VERIFIED"
                    )
                    return False
                if active is None:
                    active = self.new_job(recovery, scene, route)
                with self.coordinator.service.repository.transaction() as db:
                    count = db.execute(
                        "select count(*) from datadome_solver_jobs where "
                        "julianday(coalesce(json_extract(data_json,'$.submitted_at'),created_at))>=julianday(?) "
                        "and (json_extract(data_json,'$.submitted_at') is not null or "
                        "json_extract(data_json,'$.task_id') is not null or "
                        "json_extract(data_json,'$.status')='SUBMITTING')",
                        ((utc_now() - timedelta(days=1)).isoformat(),),
                    ).fetchone()[0]
                    if count >= min(int(config.get("daily_tasks", 20)), 100):
                        return False
                    local_count = db.execute(
                        "select count(*) from datadome_solver_jobs where identity_id=? "
                        "and julianday(json_extract(data_json,'$.submitted_at'))>=julianday(?)",
                        (identity.identity_id, (utc_now() - timedelta(minutes=10)).isoformat()),
                    ).fetchone()[0]
                    if local_count >= 2:
                        self.coordinator.event(
                            recovery, "DATADOME_SOLVER_BUDGET", reason="IDENTITY_10M_LIMIT"
                        )
                        return False
                    active.update(
                        status="SUBMITTING",
                        scene=signature,
                        binding=route,
                        submitted_at=utc_now().isoformat(),
                    )
                    active["updated_at"] = utc_now().isoformat()
                    db.execute(
                        "update datadome_solver_jobs set updated_at=?,data_json=? where job_id=?",
                        (active["updated_at"], json.dumps(active), active["job_id"]),
                    )
                task = {
                    k: binding[k]
                    for k in ["proxyAddress", "proxyPort", "proxyLogin", "proxyPassword"]
                }
                self.provider_ticks.add(recovery.recovery_id)
                data = await self.provider.request(
                    config["api_key"],
                    "createTask",
                    task={
                        **task,
                        "type": "DataDomeSliderTask",
                        "proxyType": "http",
                        "websiteURL": scene["website"],
                        "captchaUrl": scene["url"],
                        "userAgent": scene["user_agent"],
                    },
                )
                task_id = data.get("taskId")
                if isinstance(task_id, str) and task_id.isascii() and task_id.isdigit():
                    task_id = int(task_id)
                if not isinstance(task_id, int) or isinstance(task_id, bool) or task_id <= 0:
                    raise SolverError("INVALID_TASK_ID")
                active.update(status="PENDING", task_id=task_id)
                self.save(active)
                self.coordinator.event(
                    recovery, "DATADOME_SOLVER_SUBMITTED", task_id=active["task_id"]
                )
                return True
        except SolverError as exc:
            if (
                active
                and active["status"] == "PENDING"
                and str(exc) == "PROVIDER_TRANSPORT_UNCERTAIN"
            ):
                return True  # Poll the durable task again; never create a replacement purchase.
            if active:
                self.fail(active, recovery, str(exc))
            return False
        except Exception as exc:
            # Browser/provider messages can contain full challenge URLs, credentials or cookies.
            reason = (
                str(exc)
                if isinstance(exc, RuntimeError) and str(exc).startswith("solver_")
                else "BROWSER_OR_PROXY_UNAVAILABLE"
            )
            self.coordinator.event(
                recovery,
                "DATADOME_SOLVER_UNAVAILABLE",
                reason=reason,
                error_type=type(exc).__name__,
            )
            if active and active["status"] == "APPLYING":
                active.update(
                    status="APPLIED",
                    verification_state="INDETERMINATE",
                    applied_at=utc_now().isoformat(),
                    verification_reason="application_outcome_unknown",
                )
                self.save(active)
                return False
            return bool(
                active
                and (
                    active["status"] == "PENDING"
                    or active["status"] == "REGENERATED"
                    and utc_now() - datetime.fromisoformat(active["created_at"])
                    < timedelta(seconds=60)
                )
            )

    def confirmation_delay(self, recovery, report=None):
        """At most one original observation per tick; never sleep/refresh/rebuy."""
        jobs = self.jobs(recovery.recovery_id)
        job = jobs[-1] if jobs else None
        if not job or job.get("status") != "APPLIED" or not job.get("applied_at"):
            return None
        if job.get("verification_state") not in {"PENDING", "ORIGINAL_CLEARED"}:
            return None
        age = (utc_now() - datetime.fromisoformat(job["applied_at"])).total_seconds()
        if report and report.challenge == "CLEARED":
            job.setdefault("original_cleared_at", utc_now().isoformat())
            job["verification_state"] = "ORIGINAL_CLEARED"
            self.save(job)
            return None
        if age >= 120:
            job.update(
                verification_state="STILL_BLOCKED"
                if report and report.challenge == "BLOCKED"
                else "INDETERMINATE",
                verification_reason="confirmation_window_expired",
            )
            self.save(job)
            return None
        # An uncertain/cleared DOM may get ONE independent business request.
        if report and report.challenge != "BLOCKED" and not job.get("confirmation_probe_reserved"):
            job["confirmation_probe_reserved"] = True
            self.save(job)
            return 0
        next_point = next((point for point in (5, 15, 30, 60, 120) if point > age), 120)
        return max(1, next_point - age)

    def bind_confirmation_probe(self, recovery, probe_id):
        jobs = self.jobs(recovery.recovery_id)
        job = jobs[-1] if jobs else None
        if job and job.get("confirmation_probe_reserved") and not job.get("business_probe_id"):
            job["business_probe_id"] = probe_id
            self.save(job)

    def fail(self, job, recovery, reason):
        job.update(
            status="FAILED",
            reason=reason,
            verification_state="STILL_BLOCKED"
            if reason == "ERROR_CAPTCHA_UNSOLVABLE"
            else "SUPERSEDED"
            if reason in {"SCENE_OR_EGRESS_CHANGED", "EGRESS_CHANGED"}
            else "INDETERMINATE",
            verification_reason=reason,
        )
        self.save(job)
        self.coordinator.event(
            recovery, "DATADOME_SOLVER_FAILED", reason=reason, task_id=job.get("task_id")
        )
        from .schema import SitePurpose

        scope = self.coordinator.store.maintenance_scope(recovery.scope_id)
        if recovery.affected_contexts:
            self.coordinator.service.failover.schedule(
                scope, SitePurpose(recovery.affected_contexts[0]["purpose"])
            )
