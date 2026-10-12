"""Fault only this diagnostic process's Node driver; never kill production Chrome."""

import asyncio
import json
import time

import httpx

from doxagent.settings import DoxAgentSettings
from doxagent.site_strategy.external_runtime import ExternalChromeRuntime
from doxagent.site_strategy.schema import BrowserIdentitySpec, ProxyEgress
from doxagent.site_strategy.supervisor_client import SupervisorInstance
from doxagent.site_strategy.tokens import read_token


async def main():
    settings = DoxAgentSettings()
    token = read_token(settings.site_access_admin_token, settings.site_access_admin_token_file)
    with httpx.Client(
        base_url="http://127.0.0.1:8011", timeout=15, headers={"Authorization": "Bearer " + token}
    ) as api:
        original = api.get("/v1/identities/public-markets-nl").json()
        identity = BrowserIdentitySpec.model_validate(original["spec"])
        live = original["live"]
        egress = ProxyEgress.model_validate(
            next(e for e in api.get("/v1/egresses").json() if e["egress_id"] == identity.egress_id)
        )

        class ReadOnlySupervisor:
            async def ensure(self, *_args):
                # Reuse the confirmed instance; do not acquire another writer,
                # start Chrome, or alter production controller ownership.
                return SupervisorInstance(
                    **{k: live[k] for k in SupervisorInstance.__dataclass_fields__}
                )

            async def release_controller(self, *_args):
                pass

        runtime = ExternalChromeRuntime(ReadOnlySupervisor())
        try:
            await runtime.prewarm(identity, egress)
            before = runtime.diagnostics()
            process = runtime._driver_process()
            assert process is not None
            process.kill()
            await process.wait()
            await runtime.start()
            await runtime.prewarm(identity, egress)
            await runtime.maintain_driver()
            recovered = runtime.diagnostics()
            assert recovered["ready"] and recovered["epoch"] == 2
            assert recovered["pid"] != before["pid"]
            runtime._driver_started = time.monotonic() - 4 * 3600 - 1
            await runtime.maintain_driver()
            await runtime.prewarm(identity, egress)
            rotated = runtime.diagnostics()
            assert rotated["epoch"] == 3 and rotated["pid"] != recovered["pid"]
            after = api.get("/v1/identities/public-markets-nl").json()["live"]
            assert after["instance_id"] == live["instance_id"] and after["pid"] == live["pid"]
            print(
                json.dumps(
                    {
                        "before": before,
                        "recovered": recovered,
                        "rotated": rotated,
                        "chrome_instance_unchanged": True,
                    }
                ),
                flush=True,
            )
        finally:
            await runtime.close()


if __name__ == "__main__":
    asyncio.run(main())
