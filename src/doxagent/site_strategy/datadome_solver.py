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
    recovered_by=None,
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
            if job.get("status") != "APPLIED" and not (
                field in {'business_verified_at','natural_success_at','route_activated_at'}
                and (job.get('task_id') or job.get('submitted_at'))
            ):
                continue
            if field == "natural_success_at" and purpose not in job.get("verified_purposes", []):
                continue
            if (
                field == "route_activated_at"
                and job.get("verification_state") != "BUSINESS_VERIFIED"
                and not job.get("business_recovery")
            ):
                continue
            now = utc_now().isoformat()
            job.setdefault(field, now)
            if field == "business_verified_at":
                in_window = (job.get('status')=='APPLIED' and recovered_by != 'manual' and job.get('applied_at')
                             and (utc_now()-datetime.fromisoformat(job['applied_at'])).total_seconds() <= 120
                             and job.get('verification_state') in {'PENDING','ORIGINAL_CLEARED','BUSINESS_VERIFIED'})
                job['business_recovery'] = {'at':now,'purpose':purpose,'proof_id':probe_id,
                    'recovered_by': 'solver_confirmation' if in_window else recovered_by or 'background_probe'}
                job['business_recoveries']=[*job.get('business_recoveries',[]),job['business_recovery']][-8:]
                job['verified_purposes'] = list(dict.fromkeys([*job.get('verified_purposes', []), purpose]))
                if in_window:
                    job.update(verification_state='BUSINESS_VERIFIED', solver_verification='VERIFIED_IN_WINDOW',
                               business_probe_id=probe_id, verification_reason='confirmation_window_business_proof')
                else:
                    job.setdefault('late_recovery_at',now)
                    job.setdefault('solver_verification', 'INDETERMINATE')
                    # Do not erase a failed confirmation window or claim causality for manual recovery.
                    if job.get('verification_state') in {'PENDING','ORIGINAL_CLEARED'}:
                        job['verification_state']='INDETERMINATE'
                    job.setdefault('verification_reason','late_recovery')
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
        self._egress_checks = {}
        self._proof_refresh_at = {}
        self.egress_reason = 'NOT_CHECKED'

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
        # Unpaid preparation is not a live provider task after its incident ends.
        with repo.transaction() as db:
            db.execute("update datadome_solver_jobs set data_json=json_set(data_json,'$.status','SUPERSEDED',"
                "'$.verification_state','SUPERSEDED','$.reason','INCIDENT_ENDED_WITHOUT_PURCHASE') where "
                "json_extract(data_json,'$.status') in ('PREPARING','REGENERATED','WAITING_SLIDER') "
                "and json_extract(data_json,'$.task_id') is null and not exists (select 1 from browser_recoveries r "
                "where r.phase<>'RESOLVED' and json_extract(r.data_json,'$.recovery_id')=datadome_solver_jobs.recovery_id)")
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
            self.egress_reason = 'BINDING_MISMATCH'
            return False
        if utc_now() - egress.observed_at > timedelta(hours=1):
            self.egress_reason = 'OBSERVATION_STALE'
            return False
        key = digest_json([egress.egress_id,egress.generation,egress.observed_ip,egress.observed_at.isoformat(),
                           binding.get('localAddress'),binding.get('proxyPort')])
        cached=self._egress_checks.get(key)
        if cached and cached[0] > utc_now():
            self.egress_reason=cached[2]
            return cached[1]
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
                matches=response.text.strip() == egress.observed_ip
                self.egress_reason='MATCH' if matches else 'IP_MISMATCH'
        except httpx.HTTPError:
            matches=False
            self.egress_reason='LOCAL_PROXY_UNREACHABLE'
        self._egress_checks={key:(utc_now()+timedelta(seconds=60 if matches else 15),matches,self.egress_reason)}
        return matches

    async def prepare_scene(self, recovery, browser, scene):
        """Free, durable preparation independent of whether the incident already bought a task."""
        if scene.get('state') == 'IP_BANNED' or scene.get('scene_state') == 'DENIED':
            return False
        current=self.coordinator.store.get_recovery(recovery.identity_id)
        if not current or (current.recovery_id,current.generation) != (recovery.recovery_id,recovery.generation):
            return False
        recovery=current
        previous = recovery.scene_preparation
        signature = scene_key(scene)
        now=utc_now()
        if scene.get('state') == 'RETRY':
            attempts=int(previous.get('attempt_no',0))
            if attempts and previous.get('started_at'):
                age=(now-datetime.fromisoformat(previous['started_at'])).total_seconds()
                if age < 60:
                    return True
                if attempts >= 2 or previous.get('status') == 'UNCERTAIN':
                    return False
            action={ 'retry_action_id':new_id('retry'), 'scene_signature':signature,
                'instance_id':recovery.original_instance_id,'target_id':recovery.original_target_id,
                'attempt_no':attempts+1,'started_at':now.isoformat(),'status':'UNCERTAIN'}
            value=recovery.model_copy(update={'scene_preparation':action})
            self.coordinator.store.save_recovery(value,expected_generation=recovery.generation)
            # Persist before interaction. An interrupted action is observed, never blindly replayed.
            await browser.prepare_retry(scene)
            action={**action,'status':'CLICKED'}
            self.coordinator.store.save_recovery(value.model_copy(update={'scene_preparation':action}),
                                                  expected_generation=recovery.generation)
            self.coordinator.event(recovery,'DATADOME_RETRY_CLICKED',attempt_no=attempts+1,
                                   retry_action_id=action['retry_action_id'])
            return True
        if scene.get('state') in {'INTERSTITIAL','UNAVAILABLE'} and not previous:
            # Only a genuinely stalled bootstrap gets one normal refresh, not an IP denial or a loading widget.
            if scene.get('scene_state') == 'SLIDER_LOADING':
                return False
            action={'status':'BOOTSTRAP_REFRESHED','started_at':now.isoformat(),'attempt_no':0,
                    'scene_signature':signature}
            self.coordinator.store.save_recovery(recovery.model_copy(update={'scene_preparation':action}),
                                                 expected_generation=recovery.generation)
            await browser.reload()
            self.coordinator.event(recovery,'DATADOME_RETRY_REGENERATED',previous_state=scene['state'])
            return True
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
        paid_terminal = bool(active and active['status'] in {'FAILED','APPLIED','SUBMITTING','APPLYING','SUPERSEDED'})
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
            if active['status'] in {'SUBMITTING','APPLYING'} or active.get('verification_reason') in {
                'application_interrupted','application_outcome_unknown'}:
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
            # Refresh stale main-route telemetry once, outside the Profile writer.
            # The normal maintenance loop owns the half-hour cadence; never probe
            # both routes again on every five-second solver tick.
            if (not paid_terminal or data) and (not egress.observed_at or utc_now()-egress.observed_at>timedelta(hours=1)):
                last=self._proof_refresh_at.get(egress.egress_id)
                if not last or utc_now()-last>timedelta(minutes=1):
                    self._proof_refresh_at[egress.egress_id]=utc_now()
                    try:
                        await self.coordinator.service.probe_egress(egress.egress_id)
                    except (httpx.HTTPError, RuntimeError, TimeoutError):
                        self.egress_reason='OBSERVATION_STALE'
                egress=self.coordinator.service.repository.get_egress(identity.egress_id)
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
                        self.coordinator.event(recovery,'DATADOME_SOLVER_UNAVAILABLE',egress_reason=self.egress_reason)
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
                if scene['state'] != 'SOLVABLE':
                    preparing=await self.prepare_scene(recovery,browser,scene)
                    if active and active.get('status') == 'WAITING_SLIDER':
                        reason='DATADOME_'+scene['state']
                        if active.get('reason') != reason:
                            active['reason']=reason
                            self.save(active)
                    return preparing
                if paid_terminal:
                    return False
                runtime=self.coordinator.service.repository.get_identity_runtime(identity.identity_id)
                if runtime.paid_cooldown_until and runtime.paid_cooldown_until > utc_now():
                    return False
                if not await self.egress_matches(binding, egress):
                    self.coordinator.event(
                        recovery, "DATADOME_SOLVER_UNAVAILABLE", reason="PROXY_EGRESS_NOT_VERIFIED",
                        egress_reason=self.egress_reason,
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
            solver_verification='STILL_BLOCKED' if reason == 'ERROR_CAPTCHA_UNSOLVABLE' else 'INDETERMINATE',
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
