"""A physical acquisition never waits for admission, BODY, distribution or Read."""

from __future__ import annotations

import asyncio
import logging
import time
import copy
from collections import defaultdict
from datetime import datetime, timedelta
from types import SimpleNamespace

from ..admission import AdmissionContext
from ..collection_override import CollectionStopped, REQUEST_GATE
from ..poll_trace import REQUESTS, DEMAND
from ..schema import (
    AcquisitionMode,
    PollContext,
    SharedPollContext,
    PollResult,
    PollState,
    canonical_json,
    sha256_text,
    new_id,
    utc_now,
)
from ..scheduler import GlobalPollScheduler
from ..search_plan import build_query_plan
from .config import Configuration, ConfigurationLoader, load
from .store import AcquisitionStore, path_for, required_unit

log = logging.getLogger(__name__)


def recovery_checkpoint(previous, proposed):
    """Old adapter bridge: carry recovery scalars, never uncommitted page cursors."""
    recovery={'failures','consecutive_failures','retry_at','retry_not_before','next_eligible',
        'error','last_error','last_error_code','last_error_message','recovery_token','status',
        'last_attempt_at','next_due_at','next_dispatch_at'}
    result=dict(previous)
    for key,value in proposed.items():
        if key in recovery:result[key]=value
        elif isinstance(value,dict):result[key]=recovery_checkpoint(previous.get(key,{}) if isinstance(previous.get(key),dict) else {},value)
    return result


def balanced_phases(desired):
    """Spread current physical HEAD work; transient tails never reshuffle it."""
    groups = defaultdict(list)
    for key, (source, targets, unit, options) in desired.items():
        if unit.startswith('TAIL:'):
            continue
        period = options['ir_spec'].target_interval_seconds if 'ir_spec' in options else min(b.polling.target_interval_seconds for b in targets)
        route = ('browser' if unit == 'SUPPLEMENT_HEAD' else 'rss') if source.source_id == 'yahoo_finance_news' else 'all'
        groups[(source.source_id, period, route)].append(key)
    phases = {}
    for group, members in groups.items():
        members.sort(key=lambda key: sha256_text(':'.join(key)))
        spacing = group[1] / len(members)
        offset = int(sha256_text(canonical_json(group))[:8], 16) / 2**32 * spacing
        signature = sha256_text(canonical_json([group, members]))
        for rank, key in enumerate(members):
            phases[key] = (offset + rank * spacing, signature)
    return phases


class SnapshotGate:
    def __init__(self, owner, source, bindings):
        self.owner, self.source, self.bindings = owner, source, bindings

    def check(self):
        config = self.owner.config
        source = config.sources.get(self.source.source_id)
        control=getattr(self.owner,'_control',None)
        if not source or not source.enabled or (control and not control['sources'].get(source.source_id,(0,False))[1]):
            raise CollectionStopped('COLLECTION_SOURCE_DISABLED')
        if source.version!=self.source.version or (control and control['sources'].get(source.source_id,(0,False))[0]!=source.version):
            raise CollectionStopped('COLLECTION_SOURCE_CONFIGURATION_CHANGED')
        now=utc_now()
        for binding in self.bindings:
            if config.allowed(binding,now,control=control):continue
            current=config.bindings.get(binding.binding_id)
            if not current or not current.enabled or (control and not control['bindings'].get(binding.binding_id,(0,False))[1]):
                reason='COLLECTION_BINDING_DISABLED'
            elif current.version!=binding.version or (control and control['bindings'].get(binding.binding_id,(0,False))[0]!=binding.version):
                reason='COLLECTION_BINDING_CONFIGURATION_CHANGED'
            elif (control['pauses'] if control else config.pauses).get(binding.ticker)=='all':
                reason='COLLECTION_TICKER_PAUSED'
            elif control and binding.ticker not in control['running']:
                reason='COLLECTION_TICKER_STOPPED'
            else:
                override=control['override'] if control else config.override
                reason='COLLECTION_OVERRIDE_EXPIRED' if override.get('enabled') and override.get('expires_at') and now>=datetime.fromisoformat(override['expires_at']) else 'COLLECTION_CALENDAR_OR_SCHEDULE_CLOSED'
            raise CollectionStopped(reason)


class Collector:
    def __init__(self, settings, adapters, *, store=None, config_loader=load):
        self.settings, self.adapters = settings, adapters
        self.store = store or AcquisitionStore(path_for(settings.message_bus_v2_sqlite_path))
        self.config_loader = ConfigurationLoader() if config_loader is load else config_loader
        self.config = Configuration()
        self.inflight = {}
        self.states = {}
        self.desired = {}
        self._stop = False
        self._control = None
        self._plan_dirty = False
        # Reuse the tested unit definitions and actual-request limiter without
        # constructing its business Repository, intake worker or orchestration.
        self.planner = object.__new__(GlobalPollScheduler)
        p = self.planner
        p._clock, p._sleep = time.monotonic, asyncio.sleep
        p._limiters, p._query_plans, p._group_demand, p._source_demand = {}, {}, {}, {}
        p._group_state_cache, p._request_starts = {}, {}
        p._wake = asyncio.Event()
        p._intake_backlogged = False
        p.repository = SimpleNamespace(list_sources=lambda: list(self.config.sources.values()))

    async def refresh(self, *, seeds=False):
        config = await asyncio.to_thread(self.config_loader, self.settings, seeds=seeds)
        scope=tuple(b.binding_id for b in config.bindings.values() if config.allowed(b,utc_now()))
        if (isinstance(self.config_loader,ConfigurationLoader) and config is self.config
                and not seeds and not self._plan_dirty and scope==getattr(self,"_active_config_scope",None)):
            return
        self._plan_dirty = False
        self._active_config_scope=scope
        self.config = config
        if seeds:
            self.states = await asyncio.to_thread(self.store.states)
        p = self.planner
        streaming = getattr(self.adapters, "set_streaming_bindings", None)
        if streaming is not None:
            streaming(
                {
                    b.binding_id
                    for b in config.bindings.values()
                    if b.source_id == "ibkr_news" and config.allowed(b, utc_now())
                }
            )
        p._configured_sources = [s for s in config.sources.values() if s.enabled]
        p._query_plans = {}
        for b in config.bindings.values():
            s = config.sources.get(b.source_id)
            terms = config.terms.get(b.ticker)
            if s and terms and s.acquisition_mode is AcquisitionMode.BY_SEARCH:
                try:
                    p._query_plans[(s.source_id, b.ticker)] = build_query_plan(
                        s, terms[1], terms[0]
                    ).model_dump(mode="json")
                except ValueError:
                    log.warning(
                        "collector incomplete query source=%s ticker=%s", s.source_id, b.ticker
                    )
        desired = {}
        now = utc_now()
        for b in config.bindings.values():
            s = config.sources.get(b.source_id)
            if not s or not b.enabled or not b.polling.enabled or not config.allowed(b, now):
                continue
            if not b.polling.active_at(now):
                continue
            if s.source_id == "corporate_ir_press_releases":
                from ..corporate_ir.schema import endpoints

                units = [
                    ("IR:" + spec.endpoint_id, {"ir_spec": spec})
                    for spec in endpoints(s, b)
                    if spec.enabled
                ]
                for spec in endpoints(s, b):
                    head = self.states.get((b.binding_id, "IR:" + spec.endpoint_id), {}).get(
                        "state", {}
                    )
                    if spec.enabled and head.get("checkpoint", {}).get("ir_state", {}).get(
                        "checkpoint", {}
                    ).get("recovery_url"):
                        units.append(
                            ("TAIL:IR:" + spec.endpoint_id, {"ir_spec": spec, "recovery": True, "coverage_required": True})
                        )
            else:
                state = self.binding_state(b)
                units = [
                    (o["acquisition_unit"], o)
                    for _, o in p.work_units(s, b, state, now, include_all=True)
                ]
            for unit, options in units:
                options={**options,'coverage_required':required_unit(s.source_id,unit)}
                shared = s.acquisition_mode is AcquisitionMode.BY_DISTRIBUTION
                key = (s.source_id if shared else b.binding_id, unit)
                if key not in desired:
                    desired[key] = [s, [], unit, options]
                desired[key][1].append(b)
        phases = balanced_phases(desired)
        # Demand is physical endpoints/queries/channels, never a fixed ticker count.
        rates = defaultdict(float)
        groups = defaultdict(float)
        limiter_rates=defaultdict(float)
        for key, (s, targets, unit, options) in desired.items():
            if s.acquisition_mode is AcquisitionMode.BY_DISTRIBUTION:
                options['roster']=sha256_text(canonical_json(sorted(b.binding_id for b in targets)))
                prior=self.states.get((targets[0].binding_id,unit),{}).get('state',{})
                options['force_refresh']=prior.get('association_roster')!=options['roster']
            period = (
                options["ir_spec"].target_interval_seconds
                if "ir_spec" in options
                else min(b.polling.target_interval_seconds for b in targets)
            )
            if options.get('coverage_required', not unit.startswith("TAIL:")):
                rates[s.source_id] += 1 / period
                groups[s.scheduler_group] += 1 / period
                limiter_key=s.source_id
                if s.source_id=="yahoo_finance_news":
                    limiter_key+=":browser" if unit=="SUPPLEMENT_HEAD" else ":rss"
                limiter_rates[limiter_key]+=1/period
            phase, phase_key = phases.get(key, ((int(sha256_text(':'.join(key))[:8], 16) % max(1, period * 1000)) / 1000, None))
            options['schedule_phase_key'] = phase_key
            generation = self.generation(s, targets, options)
            for b in targets:
                state = self.states.get((b.binding_id, unit))
                required=options.get('coverage_required',not unit.startswith('TAIL:'))
                if state and state.get('required',0)>=0 and bool(state['required'])!=required:
                    state['required']=required
                    await asyncio.to_thread(self.store.save_unit,s,b,unit,state['state'],state['generation'],required=required)
                    await asyncio.to_thread(self.store.schedule,s,b,unit,now,True,now)
                if (state and state["generation"] == generation and state.get("required", 0) >= 0
                    and state["state"].get("schedule_interval_seconds")==period
                    and state["state"].get("schedule_phase_version") == 3
                    and state["state"].get("schedule_phase_key") == phase_key):
                    continue
                seed = config.seeds.get(b.binding_id)
                old = (
                    dict(state["state"])
                    if state
                    else (seed.model_dump(mode="json") if seed else {})
                )
                if not state:
                    old.update((seed.units.get(unit, {}) if seed else {}))
                old.pop("units", None)
                old.pop("projection_counts", None)
                if "ir_spec" in options:
                    ir = config.ir_seeds.get((b.binding_id, options["ir_spec"].endpoint_id))
                    if ir and not old.get("checkpoint", {}).get("ir_state"):
                        old.update(
                            checkpoint={"ir_state": ir.model_dump(mode="json")},
                            bootstrap_complete=ir.bootstrap_complete,
                        )
                epoch = now + timedelta(
                    seconds=phase
                )
                old.update(
                    schedule_phase_version=3,
                    schedule_phase_key=phase_key,
                    target_due_at=epoch.isoformat(),
                    next_dispatch_at=epoch.isoformat(),
                    schedule_interval_seconds=period,
                )
                state = {
                    "state": old,
                    "generation": generation,
                    "input_key": None,
                    "required": options.get('coverage_required', not unit.startswith("TAIL:")),
                }
                self.states[(b.binding_id, unit)] = state
                clock_binding = b.model_copy(
                    update={
                        "polling": b.polling.model_copy(update={"target_interval_seconds": period})
                    }
                )
                await asyncio.to_thread(
                    self.store.save_unit, s, b, unit, old, generation, required=state["required"]
                )
                await asyncio.to_thread(
                    self.store.schedule, s, clock_binding, unit, epoch, True, now
                )
        # Reconcile persisted units too: after restart self.desired is empty,
        # while old query hashes can still be marked required in the owner DB.
        expected = {(b.binding_id, unit) for _, targets, unit, _ in desired.values() for b in targets}
        for (binding_id, unit), record in self.states.items():
            if record.get('required', 0) >= 0 and (binding_id, unit) not in expected:
                await asyncio.to_thread(self.store.disable_unit, binding_id, unit, at=now)
                record['required'] = -1
        self.desired = desired
        p._source_demand, p._group_demand = dict(rates), dict(groups)
        p._limiter_demand=dict(limiter_rates)
        await asyncio.to_thread(
            self.store.meta,
            "configuration",
            {
                "at": config.at,
                "override_revision": (self._control['override'] if self._control else config.override).get("revision", 0),
                "units": len(desired),
                "physical_demand": dict(rates),
            },
        )

    @staticmethod
    def generation(source, targets, options):
        return sha256_text(
            canonical_json(
                [
                    source.adapter_ref,
                    source.version,
                    sorted((b.binding_id,b.version) for b in targets),
                    options.get('schedule_phase_key'),
                    source.default_parameters,
                    None if source.acquisition_mode is AcquisitionMode.BY_DISTRIBUTION
                    else targets[0].source_parameters,
                    options.get("query_plan_override"),
                    options["ir_spec"].fingerprint if "ir_spec" in options else None,
                ]
            )
        )

    def binding_state(self, binding):
        seed = self.config.seeds.get(binding.binding_id)
        state = seed or PollState(
            binding_id=binding.binding_id, source_id=binding.source_id, ticker=binding.ticker
        )
        units = {
            u: {k: v for k, v in r["state"].items() if k in PollState.model_fields}
            for (bid, u), r in self.states.items()
            if bid == binding.binding_id and r.get("required", 0) >= 0
        }
        return state.model_copy(update={"units": units})

    async def _refresh_loop(self):
        while not self._stop:
            try:
                await self.refresh()
            except Exception:
                log.exception(
                    "collector configuration refresh failed; retaining last verified snapshot"
                )
            await asyncio.sleep(1)

    async def _maintenance_loop(self):
        while not self._stop:
            await asyncio.sleep(30)
            try:
                await asyncio.to_thread(self.store.maintain)
            except Exception:
                log.exception("acquisition retirement deferred")

    async def _control_loop(self):
        published_revision = None
        summary_at = 0
        while not self._stop:
            try:
                control=await asyncio.to_thread(ConfigurationLoader.control,self.settings)
                self._control=control
                revision=control['override'].get('revision',0)
                if published_revision!=revision or time.monotonic()-summary_at>=30:
                    applied=await asyncio.to_thread(self.store.get_meta,'configuration',{})
                    await asyncio.to_thread(self.store.meta,'configuration',{**applied,
                        'at':utc_now().isoformat(),'override_revision':revision})
                    published_revision=revision
                    summary_at=time.monotonic()
            except Exception:
                log.exception('collector control read failed; retaining explicit authorization')
            await asyncio.sleep(1)

    async def run(self, stop):
        await asyncio.to_thread(self.store.recover_previous_owner)
        await self.refresh(seeds=True)
        refresh = asyncio.create_task(self._refresh_loop())
        maintenance = asyncio.create_task(self._maintenance_loop())
        control = asyncio.create_task(self._control_loop()) if isinstance(self.config_loader,ConfigurationLoader) else None
        try:
            while not stop.is_set():
                now = utc_now()
                for key, task in list(self.inflight.items()):
                    if task.done():
                        if not task.cancelled() and task.exception():
                            log.error(
                                "collector unit failed unit=%s error=%s",
                                key,
                                str(task.exception()),
                            )
                        del self.inflight[key]
                for key, (s, targets, unit, options) in list(self.desired.items()):
                    if key in self.inflight:
                        continue
                    state = self.states[(targets[0].binding_id, unit)]["state"]
                    due = datetime.fromisoformat(state["next_dispatch_at"])
                    if due <= now:
                        if unit.startswith("TAIL:"):
                            from ..pressure import background_allowed

                            if not background_allowed():
                                continue
                        self.inflight[key] = asyncio.create_task(
                            self.acquire(s, targets, unit, options)
                        )
                try:
                    await asyncio.wait_for(stop.wait(), 0.1)
                except TimeoutError:
                    pass
        finally:
            self._stop = True
            refresh.cancel()
            maintenance.cancel()
            if control:control.cancel()
            for t in self.inflight.values():
                t.cancel()
            await asyncio.gather(refresh, maintenance, *([control] if control else []), *self.inflight.values(), return_exceptions=True)

    async def acquire(self, source, targets, unit, options):
        from doxagent.site_strategy.scope import AccessRoutingContext, access_routing
        from doxagent.site_strategy.client import ACCESS_RESULTS

        events = []
        trace = REQUESTS.set(events)
        access = ACCESS_RESULTS.set([])
        gate = REQUEST_GATE.set(SnapshotGate(self, source, targets))
        demand = DEMAND.set(
            {
                "rate": self.planner._limiter_demand.get(source.source_id+(
                    ':browser' if unit=='SUPPLEMENT_HEAD' else ':rss') if source.source_id=='yahoo_finance_news'
                    else source.source_id,0),
                "work_class": "backfill" if unit.startswith("TAIL:") else "foreground",
            }
        )
        now = utc_now()
        started = time.monotonic()
        b = targets[0]
        row = self.states[(b.binding_id, unit)]
        old = dict(row["state"])
        generation = row["generation"]
        period = old["schedule_interval_seconds"]
        epoch = datetime.fromisoformat(old["target_due_at"])
        due = epoch + timedelta(
            seconds=max(0, int((now - epoch).total_seconds() // period)) * period
        )
        DEMAND.get()["due_at"] = due.isoformat()
        DEMAND.get()["interval_seconds"] = period
        run_id = new_id("collect")
        pages = []
        page_committed = False
        page_event_offset=0
        committed_checkpoint=options.get('checkpoint',old.get('checkpoint',{}))
        seen = set()
        reason = None
        valid = False
        registration = asyncio.create_task(
            asyncio.to_thread(self.store.start, source, targets, unit, run_id, due, now, period)
        )
        state = dict(old)
        checkpoint = copy.deepcopy(options.get("checkpoint", old.get("checkpoint", {})))

        async def on_page(messages, *, checkpoint=None, observed_at=None, **evidence):
            nonlocal page_committed,page_event_offset,committed_checkpoint
            from ..poll_trace import validate_listing
            listings = validate_listing(events,evidence.get('validated_request_ids'),since=page_event_offset)
            page_event_offset=len(events)
            observed = (
                max(e["response_received_at"] for e in listings)
                if listings
                else observed_at.isoformat()
                if observed_at
                else utc_now().isoformat()
            )
            messages = await asyncio.to_thread(
                self.store.normalize_unknown, source.source_id, messages, observed
            )
            await registration
            self.add_payload(
                pages,
                source,
                targets,
                unit,
                messages,
                observed,
                run_id,
                bool(not old.get("bootstrap_complete")),
            )
            for payload, input_key, captured_at in pages:
                payload["generation"] = generation
                await asyncio.to_thread(
                    self.store.capture_page,
                    source,
                    targets,
                    unit,
                    payload,
                    input_key,
                    captured_at,
                    run_id,
                    events,
                )
                page_committed = True
            if checkpoint is not None:
                committed_checkpoint=copy.deepcopy(checkpoint)
                state['checkpoint']=committed_checkpoint
            pages.clear()
            seen.update((m.external_id, m.url) for m in messages)

        try:
            with access_routing(AccessRoutingContext("source:" + source.source_id)):
                async with asyncio.timeout(45 if not unit.startswith("TAIL:") else 30):
                    SnapshotGate(self, source, targets).check()
                    adapter = self.adapters.resolve(
                        source.adapter_ref, source_version=source.version
                    )
                    if "ir_spec" in options:
                        if options.get("recovery"):
                            checkpoint = self.states[
                                (b.binding_id, "IR:" + options["ir_spec"].endpoint_id)
                            ]["state"].get("checkpoint", {})
                        result = await self.ir(
                            adapter,
                            options["ir_spec"],
                            b,
                            checkpoint,
                            recovery=options.get("recovery", False),
                        )
                    elif source.acquisition_mode is AcquisitionMode.BY_DISTRIBUTION:
                        result = await adapter.poll_shared(
                            SharedPollContext(
                                run_id=run_id,
                                source=source,
                                acquisition_unit=unit,
                                checkpoint=checkpoint,
                                force_refresh=bool(options.get('force_refresh')),
                                requested_at=now,
                                request_permit=self.planner._limiter(source, unit).permit,
                            )
                        )
                    else:
                        plan = options.get("query_plan_override") or self.planner._query_plans.get(
                            (source.source_id, b.ticker)
                        )
                        result = await adapter.poll(
                            PollContext(
                                poll_run_id=run_id,
                                ticker=b.ticker,
                                source=source,
                                binding=b,
                                acquisition_unit=unit,
                                checkpoint=checkpoint,
                                query_plan=plan,
                                is_bootstrap=not old.get("bootstrap_complete", False),
                                requested_at=now,
                                request_permit=self.planner._limiter(source, unit).permit,
                                on_page=on_page,
                            )
                        )
                    valid = (
                        not result.failures
                        and not result.site_access_deferred
                        and result.acquisition_metadata.get("query_attempt_count") != 0
                    )
                    reason = result.site_access_deferred_reason or (
                        result.failures[0].error_code if result.failures else None
                    )
                    # A failed response still carries recovery progress. Adapters
                    # advance a page cursor only after their page callback commits.
                    # Never discard failure counts/Retry-After for an empty list.
                    # Successful cursors belong to persisted pages. Failure-only
                    # recovery fields may advance without a successful cursor.
                    if not valid:
                        state['checkpoint']=recovery_checkpoint(committed_checkpoint,
                            result.recovery_delta or result.next_checkpoint)
                    if valid or page_committed:
                        state["bootstrap_complete"] = True
                    remaining = [m for m in result.messages if (m.external_id, m.url) not in seen]
                    if remaining:
                        await on_page(remaining, validated_request_ids=result.acquisition_metadata.get('validated_request_ids'))
                    if valid:
                        state['checkpoint']=result.next_checkpoint or committed_checkpoint
                        if options.get('roster'):
                            state['association_roster']=options['roster']
                        from ..poll_trace import validate_listing
                        validate_listing(events,result.acquisition_metadata.get('validated_request_ids'),since=page_event_offset)
                    if options.get("recovery") and valid:
                        head = self.states.get(
                            (b.binding_id, "IR:" + options["ir_spec"].endpoint_id)
                        )
                        if head and head["state"].get("checkpoint", {}) == checkpoint:
                            head["state"]["checkpoint"] = result.next_checkpoint
                            await asyncio.to_thread(
                                self.store.save_unit,
                                source,
                                b,
                                "IR:" + options["ir_spec"].endpoint_id,
                                head["state"],
                                head["generation"],
                            )
                    state["retry_not_before"] = (
                        result.site_access_retry_not_before.isoformat()
                        if result.site_access_retry_not_before
                        else None
                    )
        except CollectionStopped as exc:
            reason = str(exc) or "COLLECTION_CONFIGURATION_CHANGED"
        except asyncio.CancelledError:
            reason = "CANCELLED"
            raise
        except Exception as exc:
            reason = type(exc).__name__
            log.warning(
                "collector request failed source=%s unit=%s reason=%s detail=%s",
                source.source_id,
                unit,
                reason,
                str(exc),
            )
        finally:
            finished = utc_now()
            valid = valid and any(
                e.get("role", "listing") == "listing"
                and e.get('list_validated') and e.get('proof_basis')=='TRANSPORT_SEND'
                and e.get("response_received_at")
                and not e.get("error_type")
                and (e.get("status_code") is None or 200 <= e["status_code"] < 400)
                for e in events
            )
            if not valid and not reason:
                reason = "UNPROVEN_LISTING"
            next_due = epoch + timedelta(
                seconds=(max(0, int((finished - epoch).total_seconds() // period)) + 1) * period
            )
            dispatch = next_due
            if state.get("retry_not_before"):
                dispatch = max(dispatch, datetime.fromisoformat(state["retry_not_before"]))
            state.update(
                target_due_at=epoch.isoformat(),
                next_dispatch_at=dispatch.isoformat(),
                last_attempt_at=now.isoformat(),
                last_latency_ms=round((time.monotonic() - started) * 1000),
                last_error_code=reason,
                status="succeeded" if valid else "partial" if page_committed else "failed",
            )
            # A failed persistence attempt must not leave the network clock due
            # and refetch the same unit every 100ms. Only scheduling moves here;
            # checkpoints/freshness advance after the durable commit below.
            for target in targets:
                record = self.states.get((target.binding_id, unit))
                if record and record["generation"] == generation:
                    record["state"].update(
                        next_dispatch_at=dispatch.isoformat(), last_attempt_at=now.isoformat()
                    )
            try:
                await registration
                await asyncio.to_thread(
                    self.store.finish,
                    source,
                    targets,
                    unit,
                    state,
                    generation,
                    run_id=run_id,
                    due=due,
                    dispatched=now,
                    requests=events,
                    valid=valid,
                    reason=reason,
                    capture_payloads=pages,
                )
                for target in targets:
                    record = self.states.get((target.binding_id, unit))
                    if record and record["generation"] == generation:
                        if self._continuations(record['state'].get('checkpoint',{})) != self._continuations(state.get('checkpoint',{})):
                            self._plan_dirty = True
                        record["state"].update(state)
                        if valid:
                            listings = [
                                e
                                for e in events
                                if e.get("role", "listing") == "listing"
                                and e.get("response_received_at")
                                and (e.get("status_code") is None or 200 <= e["status_code"] < 400)
                            ]
                            if listings:
                                record["state"].update(
                                    last_capture_at=listings[-1]["response_received_at"],
                                    last_success_at=finished.isoformat(),
                                )
            finally:
                REQUESTS.reset(trace)
                ACCESS_RESULTS.reset(access)
                REQUEST_GATE.reset(gate)
                DEMAND.reset(demand)

    @staticmethod
    def _continuations(value):
        if not isinstance(value,dict):return ()
        keys={'tail_pending','done','head_at','recovery_url','pending_timestamp_rows'}
        return tuple((k,canonical_json(v) if k in keys else Collector._continuations(v))
                     for k,v in sorted(value.items()) if k in keys or isinstance(v,dict))

    def add_payload(self, pages, source, targets, unit, messages, observed, run_id, bootstrap):
        if not messages:
            return
        context = AdmissionContext(observed_at=datetime.fromisoformat(observed))
        from ..body_results import article_key, update_hint
        target_refs = [{"binding_id":b.binding_id,"ticker":b.ticker,
            "terms_revision":(self.config.terms.get(b.ticker) or (0,None))[0]} for b in targets]
        payload = {
            "generation": self.states[(targets[0].binding_id, unit)]["generation"],
            "source_id": source.source_id,
            "targets":target_refs,
            "result":{"messages":[m.model_dump(mode="json") for m in messages]},
            "bootstrap":bootstrap,"admission":context.model_dump(mode="json")}
        target_scope=sorted({b.ticker for b in targets})
        keys=[sha256_text(canonical_json([source.source_id,article_key(m,source.source_id),
              update_hint(m),target_scope])) for m in messages]
        payload["input_keys"] = keys
        fingerprint = sha256_text(canonical_json(sorted(keys))) if keys else ""
        for offset in range(0,len(messages),32):
            part={**payload,"result":{"messages":payload["result"]["messages"][offset:offset+32]},
                  "input_keys":keys[offset:offset+32]}
            part_key=sha256_text(canonical_json(sorted(keys[offset:offset+32])))
            pages.append((part,part_key,observed))

    async def ir(self, adapter, spec, binding, checkpoint, *, recovery=False):
        from ..corporate_ir.schema import IrEndpointState
        from ..corporate_ir.parsers import parse_listing

        state = IrEndpointState.model_validate(
            checkpoint.get("ir_state")
            or {"binding_id": binding.binding_id, "endpoint_id": spec.endpoint_id}
        )
        if state.config_revision and state.config_revision != spec.fingerprint and not recovery:
            state = state.model_copy(
                update={"etag": None, "last_modified": None, "representation_hash": None}
            )
        if recovery:
            cp = dict(state.checkpoint)
            url = cp.get("recovery_url")
            if not url:
                return PollResult(acquisition_metadata={"query_attempt_count": 0})
            async with adapter.endpoint_lock(binding.binding_id, spec.endpoint_id + ":archive"):
                capture = await adapter.http.fetch(spec, url=url)
            if capture.status != 200:
                raise ValueError("IR_HISTORY_HTTP_" + str(capture.status))
            parser_spec = (
                spec.model_copy(update={"parser_ref": "coherent"})
                if spec.endpoint_id == "rklb.ir" and url != spec.entry_url
                else spec
            )
            messages, following = await asyncio.to_thread(
                parse_listing, parser_spec, capture.body, capture.url
            )
            found = set(cp.get("recovery_anchors", [])).intersection(m.url for m in messages)
            cp["recovery_url"] = None if found else following
            if found or not following:
                cp.pop("recovery_anchors", None)
                cp["coverage"] = "COMPLETE" if found else "PARTIAL_HISTORY_EXHAUSTED"
            state = state.model_copy(update={"checkpoint": cp})
            return PollResult(
                messages=messages, next_checkpoint={"ir_state": state.model_dump(mode="json")}
            )
        headers = {}
        if state.representation_hash:
            if state.etag:
                headers["If-None-Match"] = state.etag
            if state.last_modified:
                headers["If-Modified-Since"] = state.last_modified
        async with adapter.endpoint_lock(binding.binding_id, spec.endpoint_id):
            capture = await adapter.http.fetch(spec, headers=headers)
        if capture.status not in {200, 304}:
            raise ValueError("IR_HTTP_" + str(capture.status))
        if capture.status == 304 and not state.representation_hash:
            raise ValueError("IR_304_WITHOUT_BASELINE")
        messages = []
        cp = dict(state.checkpoint)
        if capture.status == 200 and capture.digest != state.representation_hash:
            rows, following = await asyncio.to_thread(
                parse_listing, spec, capture.body, capture.url
            )
            previous = cp.get("items", {})
            recent = {}
            for m in rows:
                digest = sha256_text(
                    canonical_json(
                        {"title": m.title, "published": m.published_at, "raw": m.raw_payload}
                    )
                )
                recent[m.url] = digest
                if previous.get(m.url) != digest:
                    messages.append(m)
            cp["items"] = recent
            if previous and not set(previous).intersection(recent):
                cp.update(
                    coverage="PARTIAL_ANCHOR_LOST",
                    recovery_anchors=list(previous),
                    recovery_url=following or next(iter(spec.fallback_urls), None),
                )
        state = state.model_copy(
            update={
                "config_revision": spec.fingerprint,
                "checkpoint": cp,
                "representation_hash": capture.digest
                if capture.status == 200
                else state.representation_hash,
                "etag": capture.headers.get("etag", state.etag),
                "last_modified": capture.headers.get("last-modified", state.last_modified),
                "bootstrap_complete": True,
                "last_success_at": utc_now(),
                "status": "succeeded",
            }
        )
        return PollResult(
            messages=messages,
            next_checkpoint={"ir_state": state.model_dump(mode="json")},
            window_coverage="PARTIAL" if cp.get("recovery_url") else "COMPLETE",
        )
