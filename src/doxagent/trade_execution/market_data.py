"""Data-only sockets may fail over; account order sockets never do."""

import json
import os
import threading

from doxagent.ibkr_data_sources import DataSourceRouter

from .ibkr_session import IbkrSession


class ReadOnlyQuotes:
    read_only = True

    def __init__(self, executor, profile):
        self.executor, self.profile = executor, profile
        self.sources = json.loads(os.environ["IBKR_DATA_SOURCES_JSON"])
        self.router = DataSourceRouter(self.sources)
        self.sessions, self.contracts = {}, {}
        self.current = None
        self.session_lock = threading.Lock()

    def _session(self, source):
        with self.session_lock:
            return self._session_locked(source)

    def _session_locked(self, source):
        key = source["name"]
        if key not in self.sessions:
            pins = self.executor.journal.values("execution_account_profiles")
            profiles = [self.executor.repository.profile(revision) for revision in pins]
            profile = next(
                (p for p in profiles if p.host == source["host"] and p.port == int(source["port"])),
                None,
            )
            if profile is None:
                raise ValueError("DATA_SOURCE_ACCOUNT_UNREGISTERED")
            # No write guard: IbkrSession refuses all place/cancel operations.
            self.sessions[key] = IbkrSession(
                profile.model_copy(update={"client_id": 190100 + self.sources.index(source)})
            )
        return self.sessions[key]

    def _contract(self, source, contract):
        broker = self._session(source)
        other = broker.contract(contract["symbol"], contract["exchange"])
        if other["con_id"] != contract["con_id"]:
            raise ValueError("QUOTE_CONTRACT_MISMATCH")
        return broker, other

    @property
    def connection_generation(self):
        return (
            0
            if self.current is None
            else self.current.connection_generation
            + (list(self.sessions.values()).index(self.current) + 1) * 100000
        )

    @property
    def healthy(self):
        return bool(self.current and self.current.healthy)

    def contract(self, ticker, exchange="SMART"):
        return self.router.run(
            "reference", lambda source: self._session(source).contract(ticker, exchange)
        )

    def quote(self, contract, side, *, strategy=None):
        def invoke(source):
            broker, actual = self._contract(source, contract)
            value = broker.quote(actual, side, strategy=strategy)
            self.current = broker
            return {**value, "data_source": source["name"]}

        return self.router.run("realtime", invoke)

    def subscribe_quote(self, contract, owner, refresh=False):
        def invoke(source):
            broker, actual = self._contract(source, contract)
            try:
                broker.subscribe_quote(actual, owner, refresh=refresh)
                broker.quote(actual, "BUY")
                broker.quote(actual, "SELL")
            except Exception:
                broker.release_quote(actual, owner)
                raise
            self.current = broker

        return self.router.run("realtime", invoke)

    def release_quote(self, contract, owner):
        for broker in self.sessions.values():
            broker.release_quote(contract, owner)

    def quote_snapshot(self, contract):
        return self.current.quote_snapshot(contract) if self.current else None

    def atr_seed(self, contract, *args):
        def invoke(source):
            broker, actual = self._contract(source, contract)
            return broker.atr_seed(actual, *args)

        return self.router.run("history", invoke)

    def planning_bars(self, contract):
        def invoke(source):
            broker, actual = self._contract(source, contract)
            return broker.planning_bars(actual)

        return self.router.run("history", invoke)

    def close(self):
        for broker in self.sessions.values():
            broker.close()
