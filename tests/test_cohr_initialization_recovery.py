from __future__ import annotations

import asyncio
import base64
import json
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from doxagent.initialization_repair.auth import (
    access_credentials,
    install_external_auth,
    publish_access_credentials,
)
from doxagent.settings import DoxAgentSettings
from doxagent.tools.providers.base import BaseRealToolClient
from doxagent.workflows.codex_document3.orchestrator_v21 import settle_parallel


def auth_file(path, *, version=1, account="account", expires=None):
    claims = (
        base64.urlsafe_b64encode(
            json.dumps({"exp": expires or time.time() + 86400, "version": version}).encode()
        )
        .decode()
        .rstrip("=")
    )
    path.write_text(
        json.dumps(
            {
                "tokens": {
                    "access_token": f"header.{claims}.signature",
                    "refresh_token": "must-not-propagate",
                    "account_id": account,
                }
            }
        )
    )


def test_registry_clients_defer_tls_until_used_and_reuse_injected_client(monkeypatch):
    calls = []
    client = object()
    monkeypatch.setattr(
        "doxagent.tools.providers.base.httpx.Client", lambda **kw: calls.append(kw) or client
    )
    provider = BaseRealToolClient(DoxAgentSettings())
    assert calls == []
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert all(value is client for value in pool.map(lambda _: provider.client, range(20)))
    assert len(calls) == 1
    injected = BaseRealToolClient(DoxAgentSettings(), client=client)
    assert injected.client is client
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_failed_parallel_stage_settles_siblings_before_transport_owner_exits():
    started, release = asyncio.Event(), asyncio.Event()
    accepted = []
    failure = RuntimeError("original MCP infrastructure failure")

    async def failed():
        await started.wait()
        raise failure

    async def healthy():
        started.set()
        await release.wait()
        accepted.append("successful worker receipt")

    stage = asyncio.create_task(settle_parallel(failed(), healthy()))
    await started.wait()
    await asyncio.sleep(0)
    assert not stage.done()
    release.set()
    with pytest.raises(RuntimeError) as caught:
        await stage
    assert caught.value is failure
    assert accepted == ["successful worker receipt"]


def test_external_snapshot_has_no_refresh_token_and_follows_owner_rotation(tmp_path):
    source, snapshot = tmp_path / "owner.json", tmp_path / "external.json"
    auth_file(source)
    publish_access_credentials(source, snapshot)
    before = snapshot.read_text()
    assert "refresh_token" not in before
    assert "must-not-propagate" not in before
    auth_file(source, version=2)
    publish_access_credentials(source, snapshot)
    assert snapshot.read_text() != before
    auth_file(source, account="different")
    with pytest.raises(RuntimeError, match="ACCOUNT_CHANGED"):
        publish_access_credentials(source, snapshot)
    auth_file(source, expires=time.time() - 1)
    with pytest.raises(RuntimeError, match="expired"):
        access_credentials(source)


@pytest.mark.asyncio
async def test_sdk_external_login_refresh_reads_current_snapshot(tmp_path):
    snapshot = tmp_path / "auth.json"
    auth_file(snapshot)
    logins = []

    async def login(params):
        logins.append(params.model_dump(mode="json", by_alias=True))

    rpc = SimpleNamespace(
        _sync=SimpleNamespace(_approval_handler=lambda *_: {"decision": "decline"}),
        account_login_start=login,
    )
    await install_external_auth(SimpleNamespace(_client=rpc), snapshot)
    assert logins[0]["type"] == "chatgptAuthTokens"
    assert "refresh" not in json.dumps(logins)
    old = logins[0]["accessToken"]
    auth_file(snapshot, version=2)
    refreshed = rpc._sync._approval_handler("account/chatgptAuthTokens/refresh", {})
    assert refreshed["accessToken"] != old
    assert refreshed["chatgptAccountId"] == "account"
    assert rpc._sync._approval_handler("item/commandExecution/requestApproval", {}) == {
        "decision": "decline"
    }
