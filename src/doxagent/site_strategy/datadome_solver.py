"""Opt-in paid DataDome recovery. Durable task IDs, short ticks, no success without business proof."""

import json
from datetime import timedelta
from urllib.parse import quote

import httpx

from .datadome_browser import NativeChallenge
from .schema import digest_json, new_id, utc_now


class SolverError(RuntimeError):
    pass


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

    def new_job(self, recovery, scene, binding):
        now = utc_now().isoformat()
        return self.save(
            {
                "job_id": new_id("dd"),
                "recovery_id": recovery.recovery_id,
                "identity_id": recovery.identity_id,
                "created_at": now,
                "status": "PREPARING",
                "scene": digest_json(scene),
                "binding": binding,
                "task_id": None,
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
        config = self.config()
        if not config.get("enabled") or recovery.site_id not in config.get("sites", ["reuters"]):
            return False
        if (
            recovery.assessment.get("provider") != "DATADOME"
            or identity.runtime_kind.value != "external_chrome"
        ):
            return False
        binding = config.get("bindings", {}).get(identity.identity_id)
        if not binding or recovery.page_status == "LOST":
            return False
        jobs = self.jobs(recovery.recovery_id)
        active = jobs[-1] if jobs else None
        if active and active["status"] in {"FAILED", "APPLIED", "SUBMITTING"}:
            # SUBMITTING after interruption is ambiguous. Never silently buy another task.
            if active["status"] == "SUBMITTING":
                self.fail(active, recovery, "SUBMISSION_OUTCOME_UNKNOWN")
            return False
        if active and active["status"] == "PENDING":
            from datetime import datetime

            if utc_now() - datetime.fromisoformat(
                active.get("submitted_at", active["created_at"])
            ) > timedelta(seconds=240):
                self.fail(active, recovery, "SOLVER_DEADLINE_EXCEEDED")
                return False
        try:
            async with self.coordinator.service.profile_use.recovery(
                identity, recovery.recovery_id, recovery.generation
            ):
                browser = self.browser_factory(self.coordinator, recovery, identity)
                scene = await browser.inspect()
                if not scene.get("website") or not scene.get("user_agent"):
                    return False
                if self.coordinator.service.resolve(scene["website"]).site_id != recovery.site_id:
                    return False
                signature = digest_json(scene)
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
                    data = await self.provider.request(
                        config["api_key"], "getTaskResult", taskId=active["task_id"]
                    )
                    if data.get("status") == "processing":
                        return True
                    if data.get("status") != "ready":
                        self.fail(active, recovery, "INVALID_PROVIDER_RESPONSE")
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
                    if digest_json(latest_scene) != signature or not await self.egress_matches(
                        binding, current_egress
                    ):
                        self.fail(active, recovery, "SCENE_OR_EGRESS_CHANGED")
                        return False
                    await browser.apply(data.get("solution", {}).get("cookie"), scene["website"])
                    active["status"], active["cost"] = "APPLIED", str(data.get("cost", "unknown"))
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
                        return False
                    if active and active["status"] == "REGENERATED":
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
                        "and (json_extract(data_json,'$.task_id') is not null or "
                        "json_extract(data_json,'$.status')='SUBMITTING')",
                        ((utc_now() - timedelta(days=1)).isoformat(),),
                    ).fetchone()[0]
                    if count >= min(int(config.get("daily_tasks", 20)), 100):
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
        except Exception:
            # Browser/provider messages can contain full challenge URLs, credentials or cookies.
            self.coordinator.event(
                recovery, "DATADOME_SOLVER_UNAVAILABLE", reason="BROWSER_OR_PROXY_UNAVAILABLE"
            )
            return bool(active and active["status"] == "PENDING")

    def fail(self, job, recovery, reason):
        job.update(status="FAILED", reason=reason)
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
