"""Authorized one-time recovery of the incident's unresponsive Yahoo instance."""

import asyncio
import json

import httpx

from doxagent.settings import DoxAgentSettings
from doxagent.site_strategy.tokens import read_token


async def main():
    settings = DoxAgentSettings()
    headers = {
        "Authorization": "Bearer "
        + read_token(settings.site_access_admin_token, settings.site_access_admin_token_file)
    }
    async with httpx.AsyncClient(
        trust_env=False, base_url="http://127.0.0.1:8011", headers=headers, timeout=40
    ) as client:
        profiles = (await client.get("/v1/profiles")).json()
        profile = next(p for p in profiles if p["profile_id"] == "yahoo-main")
        assert not profile.get("maintenance_session_id"), "human maintenance active; do not restart"
        diag = (await client.get("/v1/diagnostics/access")).json()
        assert diag["identity_active"].get("yahoo-main", 0) == 0, "business request active"
        before = (await client.get("/v1/identities/yahoo-main")).json()
        print(
            json.dumps(
                {"identity": "yahoo-main", "before": before["live"], "human_maintenance": False}
            ),
            flush=True,
        )
        response = await client.post("/v1/identities/yahoo-main:drain")
        response.raise_for_status()
        print(json.dumps({"drain": response.json(), "profile_data_deleted": False}), flush=True)
        assert response.json()["stopped"]


if __name__ == "__main__":
    asyncio.run(main())
