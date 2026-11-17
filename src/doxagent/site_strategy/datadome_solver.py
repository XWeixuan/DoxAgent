"""Opt-in paid DataDome recovery. Durable task IDs, short ticks, no success without business proof."""

import json
import re
import sqlite3
from contextlib import nullcontext
from datetime import datetime, timedelta
from urllib.parse import quote, urlsplit, parse_qs

import httpx

from .datadome_browser import NativeChallenge
from .schema import digest_json, new_id, utc_now


def record_solver_diagnostic(repository, identity_id, stage, summary, *, recovery_id=None, capture=None):
    try:
        return _record_solver_diagnostic(repository, identity_id, stage, summary,
                                         recovery_id=recovery_id, capture=capture)
    except (sqlite3.Error, ValueError):
        return None  # Optional evidence cannot fail an otherwise valid business operation.


def _record_solver_diagnostic(repository, identity_id, stage, summary, *, recovery_id=None, capture=None):
    """Bounded, passive evidence from existing observations and business requests.

    Only state transitions write. Evidence contains query-free paths and selected
    safe headers, never raw documents/credentials; it never changes admission.
    """
    if stage not in {"DOCUMENT", "CRAWLER", "BODY"}:
        raise ValueError("unsupported diagnostic stage")
    from .governance_repository import GovernanceRepository

    current = GovernanceRepository(repository).get_recovery(identity_id)
    if not current or (recovery_id and current.recovery_id != recovery_id):
        return
    allowed = {"challenge", "authentication", "outcome", "reason", "http_status",
               "query_key", "proof_id", "body_quality", "page_kind", "evidence"}
    clean = {k: v for k, v in summary.items() if k in allowed and (
        v is None or isinstance(v, (int, bool))
        or isinstance(v, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}", v)
    )}
    # A new article/query is not itself a state change. Keep the evidence which
    # established the transition, rather than rewriting on every healthy poll.
    def state(value):
        return {k: v for k, v in value.items() if k not in {"query_key", "proof_id", "at", "http_evidence"}}

    def already_recorded(previous):
        return previous is not None and state(previous) == state(clean) and (
            capture is None or "http_evidence" in previous)

    with repository._lock:
        row = repository._connection.execute(
            "select data_json from datadome_solver_jobs where recovery_id=? "
            "order by created_at desc limit 1", (current.recovery_id,),
        ).fetchone()
    if not row:
        return
    job = json.loads(row[0])
    if job.get("status") != "APPLIED":
        return
    previous = job.get("diagnostics", {}).get(stage)
    if already_recorded(previous):
        return
    observation = {**clean, "at": utc_now().isoformat()}
    if capture is not None:
        from .solver_forensics import response_summary
        observation["http_evidence"] = response_summary(capture.final_url, capture.status_code,
            capture.headers, capture.body, wire_body=False)
        provenance = getattr(capture, "provenance", None)
        observation["http_evidence"]["provenance"] = {key: getattr(provenance, key, None)
            for key in ("identity_id", "profile_id", "instance_id")}
    with repository.transaction() as db:
        # Re-read under the owner transaction: never overwrite concurrent proof,
        # provider cost, or terminal fields with an older copy of the job.
        row = db.execute("select data_json from datadome_solver_jobs where job_id=?",
                         (job["job_id"],)).fetchone()
        if not row:
            return
        job = json.loads(row[0])
        if job.get("status") != "APPLIED":
            return
        previous = job.get("diagnostics", {}).get(stage)
        if already_recorded(previous):
            return
        job["diagnostics"] = {**job.get("diagnostics", {}), stage: observation}
        layer = observation.get('http_evidence', {}).get('layer')
        if layer in {'DATADOME', 'PERIMETERX'} and (summary.get('challenge') == 'BLOCKED' or
                (summary.get('http_status') or 0) >= 400):
            job['failure_stage'] = 'SITE_ACCEPTANCE'
        elif layer == 'AUTHENTICATION' or summary.get('authentication') in {'REAUTH_REQUIRED','ENTITLEMENT_MISSING'}:
            job['failure_stage'] = 'SESSION_AUTHENTICATION'
        elif layer == 'PROXY_NETWORK':
            job['failure_stage'] = 'PROXY_NETWORK'
        job["diagnostic_timeline"] = [*job.get("diagnostic_timeline", []),
                                      {"stage": stage, **observation}][-8:]
        db.execute("update datadome_solver_jobs set data_json=?,updated_at=? where job_id=?",
                   (json.dumps(job), observation["at"], job["job_id"]))


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
    def __init__(self, code, *, failure_stage=None, cost=None, network_reason=None):
        super().__init__(code)
        self.failure_stage = failure_stage
        self.cost = cost
        self.network_reason = network_reason


def provider_failure(code, description='', *, cost=None):
    # Classify explicit signals only; ERROR_TASK_FAILED alone proves neither a
    # proxy failure nor a bad answer. Never persist the provider's raw message.
    text = (str(code) + ' ' + str(description))[:2048].lower()
    hints = [('PROXY_AUTH_REJECTED', r'proxy.{0,25}(?:auth|407)'),
             ('PROXY_CONNECT_FAILED', r'proxy.{0,40}(?:connection|connect|timeout|unreachable|refused)|err_tunnel_connection_failed|econnrefused')]
    network = next((name for name, pattern in hints if re.search(pattern, text)), None)
    stage = ('PROXY_NETWORK' if network else 'PROVIDER_DECODE' if code in
             {'ERROR_CAPTCHA_UNSOLVABLE', 'ERROR_UNSOLVABLE'} else 'PROVIDER_UNDETERMINED')
    return SolverError(code, failure_stage=stage, cost=cost, network_reason=network)


def scene_key(scene):
    # Presentation state may change while the same paid challenge is processing.
    value = {key: scene.get(key) for key in ("url", "website", "user_agent")}
    if scene.get('family') == 'PERIMETERX':
        value['website_key'] = scene.get('website_key')
    return digest_json(value)


def retry_provider_bootstrap(recovery, scene):
    """One actual t=fe frame after acknowledged RETRY; provider loads its own widget.

    Local canvas rendering is not a 2Captcha API prerequisite. Keep exact owned
    scene, fresh action and existing paid/egress limits, never fabricate a URL.
    """
    action=recovery.scene_preparation
    if (recovery.site_id!='reuters' or action.get('status')!='CLICKED'
            or action.get('preparation_version',1)<2
            or action.get('instance_id')!=recovery.original_instance_id
            or action.get('target_id')!=recovery.original_target_id
            or scene.get('visible') is not True or not scene.get('loaded_frame_id')
            or scene.get('scene_state') not in {'RETRY_READY','SLIDER_LOADING'}):
        return False
    try:
        age=(utc_now()-datetime.fromisoformat(action['started_at'])).total_seconds()
        p=urlsplit(scene.get('url') or '')
        query=parse_qs(p.query)
        return (5<=age<=120 and p.scheme=='https' and p.hostname
            and (p.hostname=='captcha-delivery.com' or p.hostname.endswith('.captcha-delivery.com'))
            and p.port in {None,443} and not p.username and p.path=='/captcha/'
            and query.get('t')==['fe'] and all(query.get(k) for k in ('cid','initialCid','hash')))
    except (KeyError,ValueError,TypeError):
        return False


class TwoCaptcha:
    base_url = "https://api.2captcha.com/"

    async def request(self, key, method, **payload):
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=12) as client:
                response = await client.post(
                    self.base_url + method, json={"clientKey": key, **payload}
                )
                response.raise_for_status()
                data = response.json()
        except (httpx.HTTPError, ValueError):
            raise SolverError("PROVIDER_TRANSPORT_UNCERTAIN", failure_stage='PROVIDER_API') from None
        if data.get("errorId"):
            code = str(data.get("errorCode", "PROVIDER_ERROR"))
            code = code if code.isascii() and code.replace("_", "").isalnum() else "PROVIDER_ERROR"
            raise provider_failure(code, data.get('errorDescription'), cost=data.get('cost'))
        return data


class Solverify(TwoCaptcha):
    """Translate the vendor wire contract; keep recovery and browser ownership shared."""
    base_url = "https://solver.solverify.net/"

    @staticmethod
    def prepare_task(source):
        task = dict(source)
        family = task.pop('challengeFamily', 'DATADOME')
        if family == 'PERIMETERX':
            if not re.fullmatch(r'PX[a-zA-Z0-9]{4,64}', task.get('websiteKey', '')):
                raise SolverError('PERIMETERX_APP_ID_MISSING', failure_stage='INPUT_CONFIGURATION')
        else:
            try:
                p = urlsplit(task.get('captchaUrl', ''))
                q = parse_qs(p.query)
                valid = (p.scheme == 'https' and p.hostname and
                    (p.hostname == 'captcha-delivery.com' or p.hostname.endswith('.captcha-delivery.com'))
                    and p.port in {None, 443} and not p.username
                    and p.path.rstrip('/') in {'/captcha', '/interstitial'}
                    and all(q.get(k) for k in ('cid', 'initialCid', 'hash')))
            except ValueError:
                valid = False
            if not valid:
                raise SolverError('DATADOME_CHALLENGE_INPUT_INVALID', failure_stage='INPUT_CONFIGURATION')
        task.update(type="perimeterx" if family == 'PERIMETERX' else "datadome", useragent=task.pop("userAgent"),
                    proxyPort=str(task["proxyPort"]))
        return task

    async def request(self, key, method, **payload):
        if method == "createTask":
            payload = {**payload, "task": self.prepare_task(payload["task"])}
        data = await super().request(key, method, **payload)
        if method == "getTaskResult":
            status = data.get("status")
            if status == "failed":
                code = str(data.get("errorCode") or "SOLVERIFY_TASK_FAILED")
                code = code if re.fullmatch(r"[A-Z0-9_]{1,100}", code) else "SOLVERIFY_TASK_FAILED"
                raise provider_failure(code, data.get('errorDescription'), cost=data.get('cost'))
            data = {**data, "status": {"pending": "processing", "processing": "processing",
                                      "completed": "ready"}.get(status, status)}
        return data

    @staticmethod
    def cookie(solution, website, expected_user_agent):
        # Never rewrite native Chrome's identity to fit a provider's default Mac UA.
        if not expected_user_agent or solution.get("useragent") != expected_user_agent:
            raise SolverError("SOLVERIFY_USER_AGENT_MISMATCH")
        value = solution.get("cookies", {}).get("datadome")
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_~%+./=-]{1,16384}", value):
            raise SolverError("INVALID_PROVIDER_COOKIE")
        host = urlsplit(website).hostname
        if host not in {"www.reuters.com", "reuters.com"}:
            raise SolverError("SOLVERIFY_PUBLISHER_NOT_ENABLED")
        # Reuters uses a parent-domain Cookie. Do not introduce a host-only shadow.
        return f"datadome={value}; Domain=.reuters.com; Path=/; Secure; SameSite=Lax"


class DataDomeSolver:
    def __init__(self, coordinator):
        self.coordinator = coordinator
        self.provider = TwoCaptcha()
        self.solverify = Solverify()
        self.browser_factory = NativeChallenge
        from .perimeterx_browser import PerimeterXChallenge
        self.perimeterx_browser_factory = PerimeterXChallenge
        self.provider_ticks = set()
        self._egress_checks = {}
        self._proof_refresh_at = {}
        self.egress_reason = 'NOT_CHECKED'

    def provider_for(self, config, *, job=None, identity_id=None, site_id=None):
        # Durable tasks are always collected from their original provider, even
        # after an operator rolls back the per-identity selection. Legacy = 2Captcha.
        name = (job.get("provider", "2captcha") if job else
                config.get("provider_by_identity", {}).get(identity_id,
                    config.get('provider_by_site', {}).get(site_id, "2captcha")))
        if name not in {"2captcha", "solverify"}:
            raise SolverError("INVALID_PROVIDER_SELECTION")
        key = config.get("solverify_api_key" if name == "solverify" else "api_key")
        if not key:
            raise SolverError("PROVIDER_KEY_UNAVAILABLE")
        return (self.solverify if name == "solverify" else self.provider), key, name

    def event(self, recovery, event, **details):
        family = details.pop('solver_family', None) or (
            'PERIMETERX' if recovery.assessment.get('provider') in {'PERIMETERX', 'HUMAN_PERIMETERX'} else 'DATADOME')
        if family == 'PERIMETERX' and event.startswith('DATADOME_'):
            event = event.replace('DATADOME_', 'PERIMETERX_', 1)
        return self.coordinator.event(recovery, event, challenge_family=family, **details)

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
                    failure_stage='PROVIDER_UNDETERMINED',
                )
                self.save(job)
                continue
            job["last_provider_poll_at"] = now.isoformat()
            self.save(job)
            try:
                provider, key, _ = self.provider_for(config, job=job)
                data = await provider.request(
                    key, "getTaskResult", taskId=job["task_id"]
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
            except SolverError as exc:
                if str(exc) != 'PROVIDER_TRANSPORT_UNCERTAIN':
                    job.update(status='FAILED', reason=str(exc), failure_stage=exc.failure_stage,
                               provider_cost=exc.cost, cost=exc.cost, verification_state='SUPERSEDED')
                    self.save(job)

    def new_job(self, recovery, scene, binding):
        now = utc_now().isoformat()
        return self.save(
            {
                "job_id": new_id("dd"),
                "recovery_id": recovery.recovery_id,
                "identity_id": recovery.identity_id,
                'site_id': recovery.site_id,
                'challenge_family': scene.get('family', 'DATADOME'),
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
        # Release only an old, proven no-input preflight latch after this input
        # fix. Paid jobs and actual/uncertain click counts remain unchanged.
        legacy_preflight = (previous.get('status')=='NOT_DISPATCHED'
            and previous.get('preparation_version',1)<2 and not previous.get('attempt_no'))
        if legacy_preflight:
            previous={**previous,'preflight_failures':0}
        signature = scene_key(scene)
        now=utc_now()
        if scene.get('state') == 'RETRY':
            attempts=int(previous.get('attempt_no',0))
            if previous.get('started_at'):
                age=(now-datetime.fromisoformat(previous['started_at'])).total_seconds()
                if age < 60:
                    return True
                if attempts >= 2 or int(previous.get('preflight_failures', 0)) >= 3:
                    return False
                if previous.get('status') == 'UNCERTAIN':
                    # A free RETRY is not a paid purchase. Reconcile only from
                    # a new exact-target observation of a visible RETRY button;
                    # never blindly replay after a transport interruption.
                    if (scene.get('scene_state') != 'RETRY_READY' or not scene.get('retry_control')
                            or previous.get('instance_id') != recovery.original_instance_id
                            or previous.get('target_id') != recovery.original_target_id):
                        return False
            action={ 'retry_action_id':new_id('retry'), 'scene_signature':signature,
                'instance_id':recovery.original_instance_id,'target_id':recovery.original_target_id,
                'attempt_no':attempts+1,'started_at':now.isoformat(),'status':'PREPARING',
                'preflight_failures':int(previous.get('preflight_failures',0)),
                'preparation_version':2}
            value=recovery.model_copy(update={'scene_preparation':action})
            self.coordinator.store.save_recovery(value,expected_generation=recovery.generation)
            async def dispatched():
                action['status']='UNCERTAIN'
                self.coordinator.store.save_recovery(
                    value.model_copy(update={'scene_preparation':dict(action)}),
                    expected_generation=recovery.generation)
            try:
                await browser.prepare_retry(scene,on_dispatch=dispatched)
            except Exception as exc:
                action.update(failure_stage=getattr(browser,'retry_stage','UNKNOWN'),
                              error_type=type(exc).__name__)
                if action['status']=='PREPARING':
                    action.update(status='NOT_DISPATCHED',attempt_no=attempts,
                                  preflight_failures=action['preflight_failures']+1)
                    self.coordinator.store.save_recovery(
                        value.model_copy(update={'scene_preparation':dict(action)}),
                        expected_generation=recovery.generation)
                    self.event(recovery,'DATADOME_RETRY_NOT_DISPATCHED',
                        retry_action_id=action['retry_action_id'],stage=action['failure_stage'],
                        error_type=action['error_type'])
                raise
            action={**action,'status':'CLICKED'}
            self.coordinator.store.save_recovery(value.model_copy(update={'scene_preparation':action}),
                                                  expected_generation=recovery.generation)
            self.event(recovery,'DATADOME_RETRY_CLICKED',attempt_no=attempts+1,
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
            self.event(recovery,'DATADOME_RETRY_REGENERATED',previous_state=scene['state'])
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
        jobs = self.jobs(recovery.recovery_id)
        active = jobs[-1] if jobs else None
        family = ('PERIMETERX' if recovery.assessment.get('provider') in {'HUMAN_PERIMETERX', 'PERIMETERX'}
                  else recovery.assessment.get('provider'))
        if active and (active.get('task_id') or active.get('submitted_at')):
            family = active.get('challenge_family', 'DATADOME')
        if (
            family not in {'DATADOME', 'PERIMETERX'}
            or identity.runtime_kind.value != "external_chrome"
        ):
            return False
        binding = config.get("bindings", {}).get(identity.identity_id)
        if not active and utc_now() - recovery.opened_at > timedelta(days=7):
            return False  # Historic GC may have removed an already-paid tombstone.
        if active and active["status"] in {"FAILED", "APPLIED", "SUBMITTING", "APPLYING", "SUPERSEDED"}:
            # SUBMITTING after interruption is ambiguous. Never silently buy another task.
            if active["status"] == "SUBMITTING":
                self.fail(active, recovery, "SUBMISSION_OUTCOME_UNKNOWN")
            elif active["status"] == "APPLYING":
                active.update(
                    status="APPLIED",
                    verification_state="INDETERMINATE",
                    verification_reason="application_interrupted",
                    failure_stage='RESULT_APPLICATION',
                    application_state='PARTIAL_OR_UNKNOWN',
                )
                self.save(active)
            elif active["status"] == "APPLIED" and not active.get("verification_state"):
                active.update(
                    verification_state="INDETERMINATE",
                    verification_reason="legacy_application_without_proof",
                )
                self.save(active)
            # Terminal means no more browser preparation or provider submission.
            # Existing passive confirmation/manual recovery owns the next steps.
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
                provider, key, _ = self.provider_for(config, job=active)
                data = await provider.request(
                    key, "getTaskResult", taskId=active["task_id"]
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
                active["provider_receipt"] = {key: data[key] for key in ("createTime", "endTime", "solveCount")
                    if isinstance(data.get(key), int) and not isinstance(data[key], bool)}
                self.save(active)  # Persist the cost BEFORE any browser/ownership work.
            if not binding or recovery.page_status == "LOST":
                if data:
                    self.fail(active, recovery, "PAID_RESULT_ORIGINAL_UNAVAILABLE")
                return False
            # Refresh stale main-route telemetry once, outside the Profile writer.
            # The normal maintenance loop owns the half-hour cadence; never probe
            # both routes again on every five-second solver tick.
            if not egress.observed_at or utc_now()-egress.observed_at>timedelta(hours=1):
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
                factory = self.perimeterx_browser_factory if family == 'PERIMETERX' else self.browser_factory
                browser = factory(self.coordinator, recovery, identity)
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
                        self.event(recovery,'DATADOME_SOLVER_UNAVAILABLE',egress_reason=self.egress_reason)
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
                    solution = data.get("solution") or {}
                    if active.get('challenge_family') == 'PERIMETERX':
                        from .perimeterx_browser import cookie_parameters
                        try:
                            cookie = cookie_parameters(solution, scene['website'], active.get('requested_user_agent'))
                        except ValueError as exc:
                            raise SolverError(str(exc), failure_stage='RESULT_APPLICATION') from None
                    else:
                        cookie = (self.solverify.cookie(solution, scene["website"], active.get("requested_user_agent"))
                                  if active.get("provider") == "solverify" else solution.get("cookie"))
                    active.update(status="APPLYING", verification_reason="application_in_progress")
                    self.save(active)  # Crash here is uncertain, never an invitation to set again.
                    application = await browser.apply(cookie, scene["website"])
                    application = application if isinstance(application, dict) else {}
                    if application:
                        active["application_evidence"] = application
                    active.update(
                        status="APPLIED",
                        applied_at=(application or {}).get("cookie_written_at") or utc_now().isoformat(),
                        verification_state="PENDING",
                        application_state='COOKIE_WRITE_VERIFIED',
                        failure_stage=None,
                    )
                    active["cookie_scope_verified"] = True
                    self.save(active)
                    self.event(
                        recovery,
                        "DATADOME_SOLVER_APPLIED",
                        solver_family=family,
                        task_id=active["task_id"],
                        cost=active["cost"],
                    )
                    return (
                        True  # Next tick must prove the real listing/body, never mark healthy here.
                    )
                provider_bootstrap=family == 'DATADOME' and retry_provider_bootstrap(recovery,scene)
                if scene['state'] != 'SOLVABLE' and not provider_bootstrap:
                    choice = config.get('provider_by_identity', {}).get(identity.identity_id,
                        config.get('provider_by_site', {}).get(recovery.site_id, '2captcha'))
                    stalled_auto = (family == 'DATADOME' and choice == 'solverify'
                        and scene.get('state') == 'INTERSTITIAL' and scene.get('visible') is True
                        and scene.get('loaded_frame_id') and (utc_now()-recovery.opened_at).total_seconds() >= 60)
                    if not stalled_auto:
                        preparing=await self.prepare_scene(recovery,browser,scene) if family == 'DATADOME' else False
                        if active and active.get('status') == 'WAITING_SLIDER':
                            reason = 'DATADOME_' + scene['state']
                            if active.get('reason') != reason:
                                active['reason'] = reason
                                self.save(active)
                        if family == 'PERIMETERX' and scene.get('visible') and not scene.get('website_key'):
                            if active is None:
                                active = self.new_job(recovery, scene, route)
                            if active.get('reason') != 'PERIMETERX_APP_ID_MISSING':
                                active.update(reason='PERIMETERX_APP_ID_MISSING', failure_stage='INPUT_CONFIGURATION')
                                self.save(active)
                        return preparing
                runtime=self.coordinator.service.repository.get_identity_runtime(identity.identity_id)
                if runtime.paid_cooldown_until and runtime.paid_cooldown_until > utc_now():
                    return False
                if active is None:
                    active = self.new_job(recovery, scene, route)
                    active['admission']='ACKNOWLEDGED_RETRY_PROVIDER_BOOTSTRAP' if provider_bootstrap else 'LOCAL_SLIDER_READY'
                    active['challenge_family'] = family
                if not await self.egress_matches(binding, egress):
                    if active.get('network_reason') != self.egress_reason:
                        active.update(failure_stage='PROXY_NETWORK', network_reason=self.egress_reason)
                        self.save(active)
                        self.event(recovery, 'CHALLENGE_SOLVER_NETWORK_FAILED',
                            family=family, reason=self.egress_reason)
                    return False
                provider, key, provider_name = self.provider_for(config, identity_id=identity.identity_id, site_id=recovery.site_id)
                host = urlsplit(scene['website']).hostname
                if provider_name == "solverify" and host not in {'www.reuters.com', 'reuters.com', 'www.bloomberg.com', 'bloomberg.com'}:
                    raise SolverError("SOLVERIFY_PUBLISHER_NOT_ENABLED")
                if family == 'PERIMETERX' and provider_name != 'solverify':
                    raise SolverError('PERIMETERX_PROVIDER_NOT_ENABLED')
                task = {
                    k: binding[k]
                    for k in ["proxyAddress", "proxyPort", "proxyLogin", "proxyPassword"]
                }
                request_task = {
                    **task, 'type':'DataDomeSliderTask', 'proxyType':'http',
                    'websiteURL':scene['website'], 'userAgent':scene['user_agent']}
                if family == 'PERIMETERX':
                    request_task.update(challengeFamily=family, websiteKey=scene['website_key'])
                else:
                    request_task['captchaUrl'] = scene['url']
                if provider_name == 'solverify':
                    self.solverify.prepare_task(request_task)  # Local validation before paid reservation.
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
                        self.event(
                            recovery, "DATADOME_SOLVER_BUDGET", reason="IDENTITY_10M_LIMIT"
                        )
                        return False
                    active.update(
                        status="SUBMITTING",
                        scene=signature,
                        binding=route,
                        submitted_at=utc_now().isoformat(),
                        provider=provider_name,
                        requested_user_agent=scene["user_agent"],
                        challenge_family=family,
                        failure_stage=None,
                        network_reason=None,
                        reason=None,
                        network_preflight={'scope':'LOCAL_PROXY','result':'MATCH','observed_ip':egress.observed_ip},
                    )
                    active["updated_at"] = utc_now().isoformat()
                    db.execute(
                        "update datadome_solver_jobs set updated_at=?,data_json=? where job_id=?",
                        (active["updated_at"], json.dumps(active), active["job_id"]),
                    )
                self.provider_ticks.add(recovery.recovery_id)
                data = await provider.request(
                    key,
                    "createTask",
                    task=request_task,
                )
                task_id = data.get("taskId")
                if provider_name == "solverify":
                    from uuid import UUID
                    try:
                        if not isinstance(task_id, str) or str(UUID(task_id)) != task_id.lower():
                            raise ValueError()
                    except ValueError:
                        raise SolverError("INVALID_TASK_ID") from None
                else:
                    if isinstance(task_id, str) and task_id.isascii() and task_id.isdigit():
                        task_id = int(task_id)
                    if not isinstance(task_id, int) or isinstance(task_id, bool) or task_id <= 0:
                        raise SolverError("INVALID_TASK_ID")
                active.update(status="PENDING", task_id=task_id)
                self.save(active)
                self.event(
                    recovery, "DATADOME_SOLVER_SUBMITTED", task_id=active["task_id"], solver_family=family
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
                if exc.cost is not None:
                    active.update(cost=exc.cost, provider_cost=exc.cost)
                active.update(failure_stage=exc.failure_stage or
                    ('RESULT_APPLICATION' if str(exc) in {'SOLVERIFY_USER_AGENT_MISMATCH','INVALID_PROVIDER_COOKIE'}
                     else 'PROVIDER_UNDETERMINED'), network_reason=exc.network_reason)
                self.fail(active, recovery, str(exc))
            return False
        except Exception as exc:
            # Browser/provider messages can contain full challenge URLs, credentials or cookies.
            reason = (
                str(exc)
                if isinstance(exc, RuntimeError) and str(exc).startswith("solver_")
                else "BROWSER_OR_PROXY_UNAVAILABLE"
            )
            self.event(
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
                    failure_stage='RESULT_APPLICATION',
                    application_state='PARTIAL_OR_UNKNOWN',
                    cookie_scope_verified=False,
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
                failure_stage='SITE_ACCEPTANCE' if report and report.challenge == 'BLOCKED' else 'PROOF_UNAVAILABLE',
            )
            self.save(job)
            return None
        # An uncertain/cleared DOM may get ONE independent business request.
        if (not job.get("confirmation_probe_reserved") and
                ((report and report.challenge != "BLOCKED") or age >= 15)):
            job["confirmation_probe_reserved"] = True
            job["confirmation_probe_reason"] = "independent_after_apply"
            self.save(job)
            return 0
        next_point = next((point for point in (5, 15, 30, 60, 120) if point > age), 120)
        return max(1, next_point - age)

    def confirmation_probe_due(self, recovery):
        jobs = self.jobs(recovery.recovery_id)
        job = jobs[-1] if jobs else {}
        return bool(job.get("status") == "APPLIED"
            and job.get("verification_state") in {"PENDING", "ORIGINAL_CLEARED"}
            and job.get("confirmation_probe_reserved") and not job.get("business_probe_id")
            and job.get("applied_at")
            and (utc_now() - datetime.fromisoformat(job["applied_at"])).total_seconds() < 120)

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
        self.event(
            recovery, "DATADOME_SOLVER_FAILED" if job.get('challenge_family') != 'PERIMETERX' else 'PERIMETERX_SOLVER_FAILED',
            reason=reason, task_id=job.get("task_id"), failure_stage=job.get('failure_stage'),
            solver_family=job.get('challenge_family', 'DATADOME')
        )
        from .schema import SitePurpose

        scope = self.coordinator.store.maintenance_scope(recovery.scope_id)
        if recovery.affected_contexts:
            self.coordinator.service.failover.schedule(
                scope, SitePurpose(recovery.affected_contexts[0]["purpose"])
            )
