"""Offline fault injection; no browser, network, or production data access."""

import argparse
import asyncio
import hashlib
import importlib.util
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

from doxagent.site_strategy.external_runtime import ExternalChromeRuntime

parser = argparse.ArgumentParser()
parser.add_argument("--source", type=Path)
options = parser.parse_args()
if options.source:
    module_name = "doxagent.site_strategy._offline_external_probe"
    spec = importlib.util.spec_from_file_location(module_name, options.source)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    ExternalChromeRuntime = module.ExternalChromeRuntime


async def main():
    targets = set()

    async def incomplete_page():
        # Chrome may create the target before the controller receives its Page.
        targets.add("allocated-before-timeout")
        raise TimeoutError("injected response loss after allocation")

    marker = None

    async def raw_send(method, params=None):
        nonlocal marker
        if method == "Target.createTarget":
            marker = params["url"]
            return await incomplete_page()
        if method == "Target.getTargets":
            return {"targetInfos": [{"targetId": t, "url": marker} for t in targets]}
        if method == "Target.closeTarget":
            targets.discard(params["targetId"])
            return {"success": True}
        raise AssertionError(method)

    entry = SimpleNamespace(
        context=SimpleNamespace(new_page=incomplete_page, pages=[]),
        active_pages=0,
        cdp=SimpleNamespace(send=raw_send, _owned_targets=set()),
    )
    adapter = ExternalChromeRuntime.__new__(ExternalChromeRuntime)
    adapter._page_slots = asyncio.Semaphore(1)
    adapter._acquiring = 0

    async def get_entry(*_):
        return entry

    adapter._entry = get_entry
    try:
        await adapter.page(None, None, deadline=time.monotonic() + 0.05)
    except TimeoutError:
        pass
    allocation = {
        "unclosed_targets": sorted(targets),
        "active_pages": entry.active_pages,
        "acquiring": adapter._acquiring,
        "permit_released": not adapter._page_slots.locked(),
    }

    closed = {"raw_cdp": False, "driver": False}

    async def fail_target_close(*_, **__):
        raise RuntimeError("injected CDP cleanup failure")

    async def close_cdp():
        closed["raw_cdp"] = True

    async def stop_driver():
        closed["driver"] = True

    entry = SimpleNamespace(
        identity=SimpleNamespace(identity_id="offline-probe"),
        cdp=SimpleNamespace(
            _policies={"owned-target": object()},
            send=fail_target_close,
            close=close_cdp,
        ),
    )
    adapter = ExternalChromeRuntime.__new__(ExternalChromeRuntime)
    adapter._entries = {"offline-probe": entry}
    adapter._playwright = SimpleNamespace(stop=stop_driver)
    adapter._driver_process = lambda: None
    adapter.driver_state = "FAILED"
    adapter.driver_epoch = 1
    adapter.rotation_paused = False
    await adapter._detach_driver()
    detach = {**closed, "attachment_registry_empty": not adapter._entries}
    print(
        json.dumps(
            {
                "source_sha256": hashlib.sha256(options.source.read_bytes()).hexdigest()
                if options.source
                else None,
                "allocation_failure": allocation,
                "detach_failure": detach,
            }
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
