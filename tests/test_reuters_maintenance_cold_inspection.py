"""Regression: cold inspection must not repeatedly kill its own controller."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from doxagent.site_strategy.schema import BrowserRuntimeKind
from doxagent.site_strategy.worker_runtime import WorkerSiteAccessRuntime


@pytest.mark.asyncio
async def test_cold_challenge_inspection_has_attach_and_classifier_budget():
    runtime = object.__new__(WorkerSiteAccessRuntime)
    runtime._binding = lambda token: (
        "reuters",
        SimpleNamespace(runtime_kind=BrowserRuntimeKind.MANAGED_PLAYWRIGHT),
        "egress",
    )
    runtime._maintenance_refs = lambda token: {"token": token, "target_id": "original"}
    runtime.workers = SimpleNamespace(
        command=AsyncMock(
            return_value={
                "status_code": 0,
                "final_url": "https://www.reuters.com/world/story/",
                "headers": {},
                "body": "<article>normal original</article>",
                "challenge_cleared": True,
            }
        )
    )
    result = await runtime.inspect_challenge("private")
    assert result.challenge_cleared
    args, kwargs = runtime.workers.command.call_args
    assert args[2] == "verify" and args[3]["inspect_only"]
    assert args[3]["target_id"] == "original"
    # Measured cold classifier 4.6s + CDP/page recovery exceeded the old 5s.
    assert kwargs["timeout"] == 15


@pytest.mark.asyncio
async def test_login_recovery_inspection_is_also_cold_start_bounded():
    runtime = object.__new__(WorkerSiteAccessRuntime)
    runtime._binding = lambda token: ("reuters", "identity", "egress")
    runtime._maintenance_refs = lambda token: {"token": token, "target_id": "original"}
    runtime.workers = SimpleNamespace(command=AsyncMock(return_value={"navigation_state": "READY"}))
    assert (await runtime.inspect_login("private"))["navigation_state"] == "READY"
    assert runtime.workers.command.call_args.kwargs["timeout"] == 15


def test_original_classification_does_not_load_generic_extractor(monkeypatch):
    import doxagent.content_enrichment.quality as quality

    def forbidden():
        raise AssertionError("Challenge inspection must not cold-load body extraction")

    monkeypatch.setattr(quality, "_default_extractor", forbidden)
    for html, expected in [
        (
            "<title>Normal</title><article><h1>A real story</h1><p>"
            + "News text. " * 90
            + "</p></article>",
            "article",
        ),
        ("<title>Verify you are human</title><main>Blocked</main>", "challenge"),
        ("<main>Search results for Micron: 0 results</main>", "listing"),
        ("<main></main>", "unknown"),
    ]:
        info = quality.inspect_html(
            html, "https://www.reuters.com/site-search/", "", generic_extraction=False
        )
        # Site search is only recognized with a rendered result heading.
        assert info.page_kind == expected


@pytest.mark.asyncio
async def test_original_observation_requires_exact_live_human_ownership():
    from doxagent.site_strategy.maintenance_document import read_original_document

    client = SimpleNamespace(status=AsyncMock())
    identity = SimpleNamespace(identity_id="reuters-main")
    session = SimpleNamespace(target_id="human-page", browser_instance_id="native-instance")
    for target in (
        None,
        SimpleNamespace(owner_kind="BUSINESS"),
        SimpleNamespace(owner_kind="CHALLENGE", state="LOST"),
    ):
        with pytest.raises(RuntimeError, match="exact_maintenance_target_ownership_required"):
            await read_original_document(client, identity, session, target)
    client.status.assert_not_awaited()


@pytest.mark.asyncio
async def test_original_observation_never_replaces_native_instance():
    from doxagent.site_strategy.maintenance_document import read_original_document

    client = SimpleNamespace(
        status=AsyncMock(return_value={"running": True, "instance_id": "replacement"})
    )
    identity = SimpleNamespace(identity_id="reuters-main")
    session = SimpleNamespace(target_id="human-page", browser_instance_id="original")
    target = SimpleNamespace(
        owner_kind="CHALLENGE",
        state="LIVE",
        identity_id="reuters-main",
        target_id="human-page",
        browser_instance_id="original",
    )
    with pytest.raises(RuntimeError, match="original_challenge_target_lost"):
        await read_original_document(client, identity, session, target)


@pytest.mark.asyncio
async def test_original_observation_only_reads_exact_page(monkeypatch):
    import json

    import httpx
    import websockets.asyncio.client

    from doxagent.site_strategy import maintenance_document as document

    identity = SimpleNamespace(
        identity_id="reuters-main", revision=3, runtime_kind=BrowserRuntimeKind.EXTERNAL_CHROME
    )
    session = SimpleNamespace(target_id="human-page", browser_instance_id="original")
    target = SimpleNamespace(
        owner_kind="CHALLENGE",
        state="LIVE",
        identity_id="reuters-main",
        target_id="human-page",
        browser_instance_id="original",
    )
    client = SimpleNamespace(
        status=AsyncMock(
            return_value={
                "running": True,
                "instance_id": "original",
                "generation": 1,
                "cdp_http_url": "http://127.0.0.1:9225",
            }
        )
    )
    requests, commands = [], []

    def reply(request):
        requests.append(str(request.url))
        return httpx.Response(
            200,
            json=[
                {"id": "unowned-page", "type": "page"},
                {
                    "id": "human-page",
                    "type": "page",
                    "webSocketDebuggerUrl": "ws://127.0.0.1:9225/devtools/page/human-page",
                },
            ],
        )

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        document.httpx,
        "AsyncClient",
        lambda **kw: real_client(**kw, transport=httpx.MockTransport(reply)),
    )

    class Socket:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def send(self, command):
            commands.append(json.loads(command))

        async def recv(self):
            return json.dumps(
                {
                    "id": 1,
                    "result": {
                        "result": {
                            "value": json.dumps(
                                {
                                    "html": "<main>Search results for Micron: 0 results</main>",
                                    "url": "https://www.reuters.com/site-search/?query=Micron",
                                }
                            )
                        }
                    },
                }
            )

    def connect(endpoint, **kwargs):
        assert endpoint.endswith("/human-page")
        return Socket()

    monkeypatch.setattr(websockets.asyncio.client, "connect", connect)
    capture = await document.read_original_document(client, identity, session, target)
    assert capture.provenance.instance_id == "original"
    assert capture.challenge_target_id == "human-page"
    assert requests == ["http://127.0.0.1:9225/json/list"]
    assert [c["method"] for c in commands] == ["Runtime.evaluate"]
    assert "location.href" in commands[0]["params"]["expression"]


@pytest.mark.asyncio
async def test_explicit_cold_close_never_starts_or_closes_replacement():
    from doxagent.site_strategy.maintenance_document import close_original_document

    identity = SimpleNamespace(identity_id="reuters-main")
    session = SimpleNamespace(target_id="human-page", browser_instance_id="original")
    target = SimpleNamespace(
        owner_kind="CHALLENGE",
        identity_id="reuters-main",
        target_id="human-page",
        browser_instance_id="original",
    )
    # Only status is exposed. No ensure/claim method exists for this operation.
    client = SimpleNamespace(
        status=AsyncMock(
            return_value={
                "running": True,
                "instance_id": "replacement",
            }
        )
    )
    await close_original_document(client, identity, session, target)
    client.status.assert_awaited_once_with("reuters-main")


@pytest.mark.asyncio
async def test_cold_close_uses_exact_target_not_worker(monkeypatch, tmp_path):
    from doxagent.site_strategy import maintenance_document
    from doxagent.site_strategy.governance_models import MaintenanceSession, OwnedBrowserTarget
    from doxagent.site_strategy.governance_repository import GovernanceRepository
    from doxagent.site_strategy.repository import SiteStrategyRepository
    from doxagent.site_strategy.schema import BrowserIdentitySpec

    repository = SiteStrategyRepository(tmp_path / "registry.sqlite3")
    try:
        store = GovernanceRepository(repository)
        target = store.save_target(
            OwnedBrowserTarget(
                identity_id="reuters-main",
                browser_instance_id="original",
                worker_epoch=1,
                target_id="human-page",
                owner_kind="CHALLENGE",
                state="LIVE",
            )
        )
        session = store.save_session(
            MaintenanceSession(
                kind="CHALLENGE",
                scope_id="source:reuters_site_search",
                identity_id="reuters-main",
                site_id="reuters",
                allocation_id=target.allocation_id,
                target_id="human-page",
                browser_instance_id="original",
                control_token="private",
            )
        )
        runtime = object.__new__(WorkerSiteAccessRuntime)
        identity = BrowserIdentitySpec(
            identity_id="reuters-main",
            profile_id="reuters-profile",
            egress_id="de",
            runtime_kind="external_chrome",
        )
        runtime._binding = lambda token: ("reuters", identity, "egress")
        runtime.repository = repository
        runtime.supervisor_socket = "/socket"
        runtime.controller_id = "owner"
        runtime._tokens = {"private": ("reuters", "reuters-main")}
        runtime.workers = SimpleNamespace(workers={}, command=AsyncMock())
        close = AsyncMock()
        monkeypatch.setattr(maintenance_document, "close_original_document", close)
        assert await runtime.close_login("private") == "reuters-profile"
        runtime.workers.command.assert_not_awaited()
        assert close.call_args.args[2].session_id == session.session_id
        assert store.get_target(target.allocation_id).state == "CLOSED"
        assert "private" not in runtime._tokens
    finally:
        repository.close()
