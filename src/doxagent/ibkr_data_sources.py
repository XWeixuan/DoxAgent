"""Sticky read-only IB data source selection, shared by capability in each process."""

from __future__ import annotations

import json
import threading
import time
from contextvars import ContextVar
from functools import wraps

ACTIVE_DATA_SOURCE = ContextVar("ibkr_data_source", default=None)


class DataSourceRouter:
    def __init__(self, sources, *, clock=time.monotonic, cooldown=60):
        if not sources or len({s["name"] for s in sources}) != len(sources):
            raise ValueError("invalid IB data sources")
        for source in sources:
            if not 1 <= int(source["port"]) <= 65535 or not source["host"]:
                raise ValueError("invalid IB data endpoint")
        self.sources, self.clock, self.cooldown = sources, clock, cooldown
        self.selected, self.failed_until = {}, {}
        self.lock = threading.Lock()

    def run(self, capability, operation, *, succeeded=lambda value: True):
        now = self.clock()
        with self.lock:
            eligible = [
                s
                for s in self.sources
                if capability in s.get("capabilities", [])
                and self.failed_until.get((capability, s["name"]), 0) <= now
            ]
            selected = self.selected.get(capability)
            eligible.sort(key=lambda s: s["name"] != selected)
        last, failure = None, None
        for source in eligible:
            try:
                last = operation(source)
                if succeeded(last):
                    with self.lock:
                        self.selected[capability] = source["name"]
                    return last
            except Exception as exc:
                failure = exc
            with self.lock:
                self.failed_until[capability, source["name"]] = self.clock() + self.cooldown
        if last is not None:
            return last
        raise RuntimeError("IBKR_DATA_SOURCE_UNAVAILABLE") from failure


_ROUTERS = {}
_ROUTER_LOCK = threading.Lock()


def router(settings):
    raw = getattr(settings, "ibkr_data_sources_json", "[]")
    sources = json.loads(raw)
    if not sources:
        sources = [
            dict(
                name="configured",
                host=settings.ibkr_tws_host,
                port=settings.ibkr_tws_port,
                capabilities=["reference", "realtime", "history", "news"],
            )
        ]
    key = json.dumps(sources, sort_keys=True)
    with _ROUTER_LOCK:
        if key not in _ROUTERS:
            _ROUTERS[key] = DataSourceRouter(sources)
        return _ROUTERS[key]


def routed_tool(capability):
    """Retry the bounded read call only for transport/permission failures."""

    def decorate(call):
        @wraps(call)
        def wrapped(self, request):
            def invoke(source):
                token = ACTIVE_DATA_SOURCE.set(source)
                try:
                    return call(self, request)
                finally:
                    ACTIVE_DATA_SOURCE.reset(token)

            return router(self.settings).run(
                capability,
                invoke,
                succeeded=lambda result: (
                    result.succeeded
                    or not result.error
                    or result.error.code
                    not in {"ibkr_gateway_unavailable", "market_data_unavailable"}
                ),
            )

        return wrapped

    return decorate
