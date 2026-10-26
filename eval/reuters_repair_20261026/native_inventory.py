"""Inside Site Access: local CDP target inventory only. Never navigate or read Cookie/HTML."""

import asyncio
import json
import httpx
from doxagent.settings import DoxAgentSettings
from doxagent.site_strategy.supervisor_client import ChromeSupervisorClient


async def main():
    settings = DoxAgentSettings()
    client = ChromeSupervisorClient(
        settings.site_access_chrome_supervisor_socket, owner_id="reuters-readonly-audit"
    )
    out = []
    async with httpx.AsyncClient(trust_env=False, timeout=5) as http:
        for identity in ("digitimes-de-1", "reuters-backup"):
            status = await client.status(identity)
            item = {k: status.get(k) for k in ("identity_id", "instance_id", "running", "pid")}
            if status.get("running"):
                result = await http.get(status["cdp_http_url"].rstrip("/") + "/json/list")
                item["targets"] = [{"id": t["id"], "type": t["type"]} for t in result.json()]
            out.append(item)
    print(json.dumps(out))


asyncio.run(main())
