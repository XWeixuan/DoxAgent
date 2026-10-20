import asyncio
import logging
import time
from datetime import timedelta

from ..schema import PollResult, canonical_json, sha256_text, utc_now
from . import SOURCE_ID
from .parsers import parse_listing
from .repository import aggregate, get_state, save_state
from .schema import endpoints

logger = logging.getLogger(__name__)


class IrCoordinator:
    def __init__(self, scheduler):
        self.scheduler = scheduler
        self.bus = scheduler.service
        self.repo = scheduler.repository
        self.inflight = {}
        self.runner = None

    def start(self):
        if self.runner is None or self.runner.done():
            self.runner = asyncio.create_task(self._serve())

    async def _serve(self):
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("ir_coordinator_tick_failed")
            await asyncio.sleep(1)

    def _roster(self):
        source = self.repo.get_source(SOURCE_ID)
        if not source or not source.enabled:
            return []
        result = []
        for binding in self.repo.list_bindings(source_id=SOURCE_ID, active_only=True):
            if binding.source_id != SOURCE_ID or not binding.polling.enabled:
                continue
            ticker = self.repo.get_ticker_state(binding.ticker)
            orchestration = self.scheduler.runtime_orchestration
            if (
                not ticker
                or ticker.status.value != "running"
                or (orchestration and orchestration.journal.get("pause", binding.ticker) == "all")
            ):
                continue
            for spec in endpoints(source, binding):
                if spec.enabled:
                    result.append(
                        (
                            source,
                            binding,
                            spec,
                            get_state(self.repo, binding.binding_id, spec.endpoint_id),
                        )
                    )
        return result

    async def tick(self):
        now = utc_now()
        roster = await self.bus.storage.call(self._roster)
        desired = {
            (b.binding_id, s.endpoint_id): (source.version, b.version, s.fingerprint)
            for source, b, s, _ in roster
        }
        for key, (task, generation) in list(self.inflight.items()):
            if desired.get(key) != generation:
                task.cancel()
            if task.done():
                if not task.cancelled() and task.exception():
                    logger.error(
                        "ir_endpoint_job_failed endpoint=%s error=%s",
                        key,
                        type(task.exception()).__name__,
                    )
                del self.inflight[key]
        for source, binding, spec, state in roster:
            key = (binding.binding_id, spec.endpoint_id)
            if key in self.inflight:
                continue
            if state.config_revision != spec.fingerprint:
                state = type(state)(
                    binding_id=binding.binding_id,
                    endpoint_id=spec.endpoint_id,
                    config_revision=spec.fingerprint,
                    next_due_at=now
                    + timedelta(seconds=int(sha256_text(spec.endpoint_id)[:4], 16) % 4),
                )
                await self.bus.storage.call(save_state, self.repo, state)
            if state.next_dispatch_at and state.next_dispatch_at > now:
                continue
            if state.next_due_at and state.next_due_at > now:
                continue
            self.inflight[key] = (
                asyncio.create_task(self._run(source, binding, spec, state)),
                desired[key],
            )

    def _current(self, source, binding, spec):
        latest = self.repo.get_source(SOURCE_ID)
        current = self.repo.get_binding(binding.binding_id)
        ticker = self.repo.get_ticker_state(binding.ticker)
        orchestration = self.scheduler.runtime_orchestration
        return bool(
            latest
            and current
            and ticker
            and ticker.status.value == "running"
            and latest.enabled
            and current.enabled
            and current.polling.enabled
            and latest.version == source.version
            and current.version == binding.version
            and any(
                s.enabled and s.fingerprint == spec.fingerprint for s in endpoints(latest, current)
            )
            and not (orchestration and orchestration.journal.get("pause", binding.ticker) == "all")
        )

    def _commit(self, source, binding, spec, state, messages=None):
        with self.repo.atomic_intake():
            if not self._current(source, binding, spec):
                return
            execution = None
            if messages is not None:
                execution = self.bus._accept_poll_result(
                    source=source,
                    binding=binding,
                    result=PollResult(messages=messages),
                    attempted_at=state.last_attempt_at,
                    persist_poll_state=False,
                    bootstrap_override=not state.bootstrap_complete,
                    strict_intake=True,
                )
                state = state.model_copy(update={"bootstrap_complete": True})
            save_state(self.repo, state)
            aggregate(self.repo, source, binding, utc_now(), execution)

    async def _run(self, source, binding, spec, state):
        now = utc_now()
        started = time.monotonic()
        adapter = self.scheduler.adapters.resolve(source.adapter_ref, source_version=source.version)
        capture = None
        interval = spec.target_interval_seconds
        due = state.next_due_at or now
        while due <= now:
            due += timedelta(seconds=interval)
        try:
            headers = {}
            if state.representation_hash:
                if state.etag:
                    headers["If-None-Match"] = state.etag
                if state.last_modified:
                    headers["If-Modified-Since"] = state.last_modified
            async with adapter.endpoint_lock(binding.binding_id, spec.endpoint_id):
                capture = await adapter.http.fetch(spec, headers=headers)
            if capture.status not in {200, 304}:
                raise ValueError(f"HTTP_{capture.status}")
            if capture.status == 304 and not state.representation_hash:
                raise ValueError("IR_304_WITHOUT_CONSUMED_BASELINE")
            changed = capture.status == 200 and capture.digest != state.representation_hash
            messages = []
            checkpoint = dict(state.checkpoint)
            if changed:
                rows, following = await asyncio.to_thread(
                    parse_listing, spec, capture.body, capture.url
                )
                previous = checkpoint.get("items", {})
                recent = {}
                for message in rows:
                    digest = sha256_text(
                        canonical_json(
                            {
                                "title": message.title,
                                "published": message.published_at,
                                "raw": message.raw_payload,
                            }
                        )
                    )
                    recent[message.url] = digest
                    if previous.get(message.url) != digest:
                        messages.append(message)
                checkpoint = {**checkpoint, "items": recent}
                if previous and not set(previous).intersection(recent):
                    checkpoint["coverage"] = "PARTIAL_ANCHOR_LOST"
                    checkpoint["recovery_anchors"] = list(previous)
                    checkpoint["recovery_url"] = following or next(iter(spec.fallback_urls), None)
            updated = state.model_copy(
                update={
                    "checkpoint": checkpoint,
                    "representation_hash": capture.digest
                    if capture.status == 200
                    else state.representation_hash,
                    "etag": capture.headers.get("etag", state.etag),
                    "last_modified": capture.headers.get("last-modified", state.last_modified),
                    "last_attempt_at": now,
                    "last_success_at": utc_now(),
                    "last_change_at": now if changed else state.last_change_at,
                    "next_due_at": due,
                    "next_dispatch_at": due,
                    "status": "succeeded",
                    "consecutive_failures": 0,
                    "last_error_code": None,
                    "last_error_message": None,
                    "latency_ms": int((time.monotonic() - started) * 1000),
                    "http_status": capture.status,
                    "bytes_received": capture.bytes_received,
                    "method_used": spec.method,
                    "updated_at": utc_now(),
                }
            )
            await self.bus.storage.call(self._commit, source, binding, spec, updated, messages)
            # Head results are durable before a bounded archive page is attempted.
            if checkpoint.get("recovery_url"):
                try:
                    await self._recover_one(source, binding, spec, adapter)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning(
                        "ir_archive_recovery_pending endpoint=%s error=%s",
                        spec.endpoint_id,
                        type(exc).__name__,
                    )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            count = state.consecutive_failures + 1
            wait = min(300, interval * 2 ** min(count - 1, 4))
            if capture and capture.status == 429:
                from email.utils import parsedate_to_datetime

                retry = capture.headers.get("retry-after", "")
                try:
                    wait = max(wait, float(retry))
                except ValueError:
                    try:
                        wait = max(wait, (parsedate_to_datetime(retry) - now).total_seconds())
                    except (ValueError, TypeError):
                        pass
            failed = state.model_copy(
                update={
                    "last_attempt_at": now,
                    "next_due_at": due,
                    "next_dispatch_at": now + timedelta(seconds=wait),
                    "status": "failed",
                    "consecutive_failures": count,
                    "last_error_code": type(exc).__name__,
                    "last_error_message": str(exc)[:300],
                    "latency_ms": int((time.monotonic() - started) * 1000),
                    "http_status": capture.status if capture else None,
                    "updated_at": utc_now(),
                }
            )
            await self.bus.storage.call(self._commit, source, binding, spec, failed)

    async def _recover_one(self, source, binding, spec, adapter):
        state = await self.bus.storage.call(
            get_state, self.repo, binding.binding_id, spec.endpoint_id
        )
        checkpoint = dict(state.checkpoint)
        url = checkpoint.get("recovery_url")
        if not url:
            return
        async with adapter.endpoint_lock(binding.binding_id, spec.endpoint_id):
            capture = await adapter.http.fetch(spec, url=url)
        if capture.status != 200:
            return
        parser_spec = (
            spec.model_copy(update={"parser_ref": "coherent"})
            if (spec.endpoint_id == "rklb.ir" and url != spec.entry_url)
            else spec
        )
        messages, following = await asyncio.to_thread(
            parse_listing, parser_spec, capture.body, capture.url
        )
        found = set(checkpoint.get("recovery_anchors", [])).intersection(m.url for m in messages)
        checkpoint["recovery_url"] = None if found else following
        if found or not following:
            checkpoint.pop("recovery_anchors", None)
            checkpoint["coverage"] = "COMPLETE" if found else "PARTIAL_HISTORY_EXHAUSTED"
        state = state.model_copy(update={"checkpoint": checkpoint, "updated_at": utc_now()})
        await self.bus.storage.call(self._commit, source, binding, spec, state, messages)

    async def close(self):
        if self.runner is not None:
            self.runner.cancel()
            await asyncio.gather(self.runner, return_exceptions=True)
        for task, _ in self.inflight.values():
            task.cancel()
        await asyncio.gather(*(task for task, _ in self.inflight.values()), return_exceptions=True)
        self.inflight.clear()
