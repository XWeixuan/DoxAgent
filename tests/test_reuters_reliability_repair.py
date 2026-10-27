"""Reuters composite regressions: deterministic, without publisher/provider requests."""

import struct
import zlib
from contextlib import asynccontextmanager
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest

from doxagent.site_strategy.datadome_browser import NativeChallenge
from doxagent.site_strategy.datadome_solver import mark_solver_outcome
from doxagent.site_strategy.image_quality import png_quality
from doxagent.site_strategy.schema import utc_now
from tests import test_datadome_solver as paid
from tests import test_browser_recovery as base


@pytest.fixture
def setup(tmp_path):
    yield from base.setup.__wrapped__(tmp_path)


@pytest.fixture
def solver_setup(setup):
    yield from paid.solver_setup.__wrapped__(setup)


def png(rows):
    def chunk(kind, data):
        return (
            struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
        )

    header = struct.pack(">IIBBBBB", len(rows[0]) // 3, len(rows), 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(b"".join(b"\0" + r for r in rows)))
        + chunk(b"IEND", b"")
    )


def test_white_screenshot_is_not_readable_but_real_contrast_is():
    assert png_quality(png([bytes([255] * 300)] * 100)) == "BLANK_SUSPECTED"
    assert png_quality(png([bytes([0, 0, 0] * 50 + [255, 255, 255] * 50)] * 100)) == "READABLE"
    assert png_quality(b"invalid") == "FAILED"


@pytest.mark.parametrize(
    "manual,age,verified", [(False, 30, True), (False, 300, False), (True, 30, False)]
)
def test_manual_and_late_business_proof_never_claim_solver_confirmation(
    solver_setup, manual, age, verified
):
    solver, recovery, _, _, _, _ = solver_setup
    service = solver.coordinator.service
    now = utc_now()
    job = solver.save(
        {
            "job_id": "dd_attribution",
            "identity_id": recovery.identity_id,
            "recovery_id": recovery.recovery_id,
            "created_at": now.isoformat(),
            "status": "APPLIED",
            "applied_at": (now - timedelta(seconds=age)).isoformat(),
            "verification_state": "PENDING",
        }
    )
    mark_solver_outcome(
        service.repository,
        recovery.identity_id,
        "business_verified_at",
        recovery_id=recovery.recovery_id,
        purpose="CRAWLER",
        probe_id="proof",
        recovered_by="manual" if manual else "background_probe",
    )
    actual = solver.jobs(recovery.recovery_id)[-1]
    assert (actual["verification_state"] == "BUSINESS_VERIFIED") is verified
    assert actual["business_recovery"]["proof_id"] == "proof"
    assert ("late_recovery_at" in actual) is not verified
    assert job["job_id"] == actual["job_id"]


def test_capability_can_relapse_and_recover_while_same_original_is_retained(setup):
    repo, _, store, _ = setup
    recovery, target, _ = base.retained(setup, human=True)
    for epoch in (1, 2):
        with repo.transaction() as db:
            store._record_recovered_window(
                db, recovery, repo.get_identity_runtime(recovery.identity_id), purpose="CRAWLER"
            )
        assert (
            repo.get_identity_runtime(recovery.identity_id).capability_windows["CRAWLER"][
                "capability_epoch"
            ]
            == epoch
        )
        with repo.transaction() as db:
            store._record_capability_relapse(
                db, recovery.model_copy(update={"assessment": {"kind": "INTERACTIVE"}})
            )
        with repo.transaction() as db:
            store._record_capability_relapse(
                db, recovery.model_copy(update={"assessment": {"kind": "INTERACTIVE"}})
            )
        assert len(repo.get_identity_runtime(recovery.identity_id).recent_relapses) == epoch
    runtime = repo.get_identity_runtime(recovery.identity_id)
    assert runtime.paid_cooldown_until > utc_now()
    assert store.get_target(target.allocation_id).state == "LIVE"
    assert store.get_recovery(recovery.identity_id).challenge_task_id == recovery.challenge_task_id


def test_unsolvable_task_records_later_manual_recovery_without_changing_solver_failure(solver_setup):
    solver,recovery,_,_,_,_=solver_setup
    solver.save({'job_id':'dd_unsolvable','task_id':1,'identity_id':recovery.identity_id,
        'recovery_id':recovery.recovery_id,'created_at':utc_now().isoformat(),'status':'FAILED',
        'verification_state':'STILL_BLOCKED','solver_verification':'STILL_BLOCKED'})
    mark_solver_outcome(solver.coordinator.service.repository,recovery.identity_id,'business_verified_at',
        recovery_id=recovery.recovery_id,purpose='CRAWLER',recovered_by='manual')
    job=solver.jobs(recovery.recovery_id)[-1]
    assert job['business_recovery']['recovered_by']=='manual'
    assert job['solver_verification']==job['verification_state']=='STILL_BLOCKED'
    assert job['status']=='FAILED'


@pytest.mark.asyncio
async def test_interrupted_free_retry_is_not_replayed_or_rebought(solver_setup):
    solver, recovery, _, _, _, browser = solver_setup
    store = solver.coordinator.store
    async def interrupted(scene, *, on_dispatch):
        await on_dispatch()
        raise RuntimeError("transport_interrupted")
    browser.prepare_retry.side_effect = interrupted
    scene = {"state": "RETRY", "scene_state": "RETRY_READY", "url": paid.URL}
    with pytest.raises(RuntimeError):
        await solver.prepare_scene(recovery, browser, scene)
    current = store.get_recovery(recovery.identity_id)
    assert current.scene_preparation["status"] == "UNCERTAIN"
    current.scene_preparation["started_at"] = (utc_now() - timedelta(seconds=61)).isoformat()
    store.save_recovery(current, expected_generation=current.generation)
    assert not await solver.prepare_scene(current, browser, scene)
    assert browser.prepare_retry.await_count == 1
    assert not solver.jobs(recovery.recovery_id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scene",
    [
        {"state": "IP_BANNED"},
        {"state": "UNAVAILABLE", "scene_state": "DENIED"},
        {"state": "UNAVAILABLE", "scene_state": "SLIDER_LOADING"},
    ],
)
async def test_ban_denial_or_loading_never_refreshes_or_clicks(solver_setup, scene):
    solver, recovery, _, _, _, browser = solver_setup
    assert not await solver.prepare_scene(recovery, browser, scene)
    browser.reload.assert_not_awaited()
    browser.prepare_retry.assert_not_awaited()


@pytest.mark.asyncio
async def test_native_retry_uses_exact_current_owner_and_native_input(monkeypatch):
    import doxagent.site_strategy.verification_frames as frames

    scene = {
        "scene_state": "RETRY_READY",
        "retry_control": {"x": 10, "y": 20},
        "url": paid.URL,
        "loaded_frame_id": "owned",
    }
    doc = {"frames": [{"frame_id": "owned", "visible": True, "url": paid.URL, "owner_node_id": 42}]}
    monkeypatch.setattr(frames, "observe_verification_document", AsyncMock(return_value=doc))
    monkeypatch.setattr(
        frames, "observe_widget", AsyncMock(return_value={"retry_control": {"x": 10, "y": 20}})
    )
    calls = []

    async def call(method, params=None):
        calls.append((method, params))
        return {"model": {"content": [100, 200, 400, 200, 400, 500, 100, 500]}}

    @asynccontextmanager
    async def connection():
        yield call

    browser = NativeChallenge.__new__(NativeChallenge)
    browser.inspect = AsyncMock(return_value=scene)
    browser.connection = connection
    browser.check = lambda: None
    browser._evidence = AsyncMock()
    await browser.prepare_retry(scene)
    assert [p["type"] for m, p in calls if m == "Input.dispatchMouseEvent"] == [
        "mouseMoved",
        "mousePressed",
        "mouseReleased",
    ]
    assert all(p["x"] == 110 and p["y"] == 220 for m, p in calls if m == "Input.dispatchMouseEvent")
    assert not any(m in {"Page.reload", "Runtime.evaluate"} for m, p in calls)
    doc["frames"][0]["frame_id"] = "replacement"
    with pytest.raises(RuntimeError, match="scene_changed"):
        await browser.prepare_retry(scene)


def test_storage_busy_is_narrow_not_a_blanket_storage_exception():
    import sqlite3
    from doxagent.content_enrichment.service import _storage_busy
    from doxagent.message_bus_v2.storage_lane import WriteAdmissionBusy

    assert _storage_busy(sqlite3.OperationalError("database is locked"))
    assert _storage_busy(WriteAdmissionBusy())
    assert not _storage_busy(sqlite3.DatabaseError("database disk image is malformed"))
    assert not _storage_busy(RuntimeError("locked"))


@pytest.mark.asyncio
async def test_reuters_head_is_one_navigation_tail_keeps_remaining_page(tmp_path):
    from tests.test_message_bus_v2_news_sources import _context
    from doxagent.message_bus_v2.news_adapters import ReutersSiteSearchAdapter

    ctx, repo = _context(tmp_path, "reuters_site_search", {"max_pages": 2})
    calls = []

    class Browser:
        async def reuters_search(self, query, offset):
            calls.append(offset)
            return [
                {
                    "title": f"An actual Micron article {offset + i}",
                    "date": "September 14, 2026",
                    "url": f"/technology/micron-{offset + i}-2026-09-14/",
                }
                for i in range(20)
            ]

    adapter = ReutersSiteSearchAdapter(Browser())
    ctx = ctx.model_copy(update={"acquisition_unit": "QUERY:q"})
    head = await adapter._poll_query(ctx, "Micron", "q", "monitoring_terms")
    assert calls == [0] and head.next_checkpoint["tail_pending"]
    tailctx = ctx.model_copy(
        update={
            "acquisition_unit": "TAIL:q",
            "checkpoint": {"queries": {"q": head.next_checkpoint}},
        }
    )
    tail = await adapter._poll_query(tailctx, "Micron", "q", "monitoring_terms")
    assert calls == [0, 20] and tail.next_checkpoint["page"] == 2
    assert len(head.messages) == len(tail.messages) == 20
    repo.close()


def test_first_complete_cycle_cannot_borrow_a_legacy_green_query(tmp_path):
    from tests.test_message_bus_v2_news_sources import _context
    from doxagent.message_bus_v2.schema import PollStatus
    ctx,repo=_context(tmp_path,'reuters_site_search',{})
    now=utc_now()
    base=repo.get_poll_state(ctx.binding)
    old=base.model_copy(update={'status':PollStatus.SUCCEEDED,'last_success_at':now-timedelta(hours=1),
        'last_attempt_at':now-timedelta(hours=1),'units':{}})
    repo.save_poll_unit_state(old,'QUERY:legacy')
    # Drop new-format migration metadata to represent an actual legacy projection.
    current=repo.get_poll_state(ctx.binding)
    repo.save_poll_state(current.model_copy(update={'checkpoint':{}}))
    fresh=base.model_copy(update={'status':PollStatus.SUCCEEDED,'last_success_at':now,
        'last_attempt_at':now-timedelta(seconds=2),'units':{}})
    repo.save_poll_unit_state(fresh,'QUERY:fresh')
    state=repo.get_poll_state(ctx.binding)
    assert state.status is PollStatus.PARTIAL
    assert not state.checkpoint['query_cycle']['complete']
    repo.save_poll_unit_state(fresh.model_copy(update={'last_success_at':now+timedelta(seconds=1)}),'QUERY:legacy')
    assert repo.get_poll_state(ctx.binding).checkpoint['query_cycle']['complete']
    repo.close()


@pytest.mark.asyncio
async def test_widget_uses_rendered_not_hidden_retry_and_loaded_puzzle():
    from pathlib import Path
    from playwright.async_api import async_playwright
    from doxagent.site_strategy.verification_frames import WIDGET_EXPRESSION

    executable = Path("C:/Program Files/Google/Chrome/Application/chrome.exe")
    if not executable.exists():
        pytest.skip("local Chrome fixture unavailable")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(executable_path=str(executable), headless=True)
        try:
            page = await browser.new_page()
            for markup, ready in [
                ("<button>RETRY</button>", True),
                ('<div style="opacity:0"><button>RETRY</button></div>', False),
                ('<button style="display:none">RETRY</button>', False),
                ("<button>RETRY</button><button>RETRY</button>", False),
            ]:
                await page.set_content(markup)
                assert bool((await page.evaluate(WIDGET_EXPRESSION))["retry_control"]) is ready
            await page.set_content(
                '<div role="slider"><canvas width="300" height="100"></canvas></div>'
            )
            assert (await page.evaluate(WIDGET_EXPRESSION))["widget_ready"]
            await page.set_content(
                '<div role="slider"><img width="300" height="100" src="data:image/png;base64,invalid"></div>'
            )
            assert not (await page.evaluate(WIDGET_EXPRESSION))["widget_ready"]
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_busy_heartbeat_cancels_only_its_job_and_sibling_completes(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from doxagent.content_enrichment.service import ContentEnrichmentHub

    hub = ContentEnrichmentHub.__new__(ContentEnrichmentHub)
    hub.repository = None
    finished = []

    async def renew(job):
        if job.job_id == "bad":
            await asyncio.sleep(0.01)
            raise RuntimeError("enrichment_lease_lost")
        await asyncio.sleep(100)

    async def process(job, now):
        await asyncio.sleep(0.05)
        finished.append(job.job_id)

    hub._renew = renew
    hub._process = process
    jobs = [
        SimpleNamespace(
            job_id=k,
            created_at=utc_now(),
            work_kind="foreground",
            bootstrap=False,
            body_result_key=None,
            binding=None,
        )
        for k in ("bad", "good")
    ]
    await asyncio.gather(*(hub._process_guarded(j, utc_now()) for j in jobs))
    assert finished == ["good"]
