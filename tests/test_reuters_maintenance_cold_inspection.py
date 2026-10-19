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
