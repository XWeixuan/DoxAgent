"""Publish current access credentials without cloning the refresh-token owner."""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path


def access_credentials(source: Path) -> dict[str, str]:
    data = json.loads(source.read_text(encoding="utf-8"))
    tokens = data.get("tokens", data)
    access = tokens.get("access_token")
    account = tokens.get("account_id")
    if not access or not account:
        raise RuntimeError("REPAIR_AUTH_UNAVAILABLE: access token/account id missing")
    try:
        claims = json.loads(base64.urlsafe_b64decode(access.split(".")[1] + "==="))
    except (ValueError, IndexError):
        raise RuntimeError("REPAIR_AUTH_UNAVAILABLE: invalid access token") from None
    if claims.get("exp", 0) <= time.time():
        raise RuntimeError("REPAIR_AUTH_UNAVAILABLE: login owner must refresh expired credentials")
    return {"access_token": access, "account_id": account}


def publish_access_credentials(source: Path, target: Path) -> None:
    # The login owner alone rotates refresh tokens. Repair containers never
    # receive either its refresh token or its CODEX_HOME/configuration.
    credentials = access_credentials(source)
    if target.is_file():
        prior = json.loads(target.read_text(encoding="utf-8"))
        if prior.get("account_id") != credentials["account_id"]:
            raise RuntimeError("REPAIR_AUTH_ACCOUNT_CHANGED")
        if prior == credentials:
            return
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(credentials), encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(target)


async def install_external_auth(client, snapshot: Path) -> None:
    """Use the pinned SDK's typed external-token login and refresh callback."""
    from openai_codex.generated.v2_all import (
        ChatgptAuthTokensLoginAccountParams,
        LoginAccountParams,
    )

    credentials = access_credentials(snapshot)
    account_id = credentials["account_id"]
    # SDK 0.159.3 exposes typed external login on its underlying RPC client.
    rpc = client._client
    original_handler = rpc._sync._approval_handler

    def handle(method, params):
        if method == "account/chatgptAuthTokens/refresh":
            current = access_credentials(snapshot)
            if current["account_id"] != account_id:
                raise RuntimeError("REPAIR_AUTH_ACCOUNT_CHANGED")
            return {"accessToken": current["access_token"], "chatgptAccountId": account_id}
        return original_handler(method, params)

    rpc._sync._approval_handler = handle
    await rpc.account_login_start(
        LoginAccountParams(
            root=ChatgptAuthTokensLoginAccountParams(
                type="chatgptAuthTokens",
                access_token=credentials["access_token"],
                chatgpt_account_id=account_id,
            )
        )
    )
