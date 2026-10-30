"""Bounded, read-only Gateway login/API probe; never registers an order writer."""

import asyncio
import os
import threading


def make_app():
    from ibapi.client import EClient
    from ibapi.wrapper import EWrapper

    class App(EWrapper, EClient):
        def __init__(self):
            EClient.__init__(self, self)
            self.ready = threading.Event()
            self.accounts = threading.Event()
            self.clock = threading.Event()
            self.broken = False
            self.account_ids = []

        def nextValidId(self, orderId):
            self.ready.set()

        def managedAccounts(self, accountsList):
            self.account_ids = [a.strip() for a in accountsList.split(",") if a.strip()]
            if self.account_ids:
                self.accounts.set()

        def currentTime(self, value):
            if value > 0:
                self.clock.set()

        def connectionClosed(self):
            self.broken = True

        def error(self, reqId, *args):
            # Official API versions with and without errorTime.
            code = args[0] if len(args) < 3 or isinstance(args[1], str) else args[1]
            if code in {502, 504, 1100, 1300, 326}:
                self.broken = True

    return App()


def probe(factory=make_app, *, profile=None, client_id=None):
    app, thread = None, None
    try:
        app = factory()
        app.connect(
            (profile or {}).get("host", os.getenv("IBKR_TWS_HOST", "127.0.0.1")),
            int((profile or {}).get("port", os.getenv("IBKR_TWS_PORT", "7496"))),
            clientId=client_id or int(os.getenv("DOXAGENT_IB_GATEWAY_HEALTH_CLIENT_ID", "197401")),
        )
        thread = threading.Thread(target=app.run, daemon=True)
        thread.start()
        if not app.ready.wait(2) or not app.accounts.wait(2):
            return False
        if profile and profile["expected_account_id"] not in app.account_ids:
            return False
        app.reqCurrentTime()
        return bool(app.clock.wait(2) and app.isConnected() and not app.broken)
    except Exception:
        return False
    finally:
        if app:
            app.disconnect()
        if thread:
            thread.join(timeout=1)


class GatewayMonitor:
    def __init__(self, journal=None):
        self.journal = journal
        self.task = None
        self.connected = False
        self.statuses = dict(
            ib_gateway_live_status="NOT_CONFIGURED", ib_gateway_paper_status="NOT_CONFIGURED"
        )

    async def status(self):
        if self.journal is not None:
            if self.task is None:
                self.task = asyncio.create_task(self._dual_status())
            task = self.task
            try:
                return await asyncio.wait_for(asyncio.shield(task), 8)
            except Exception:
                return dict(self.statuses)
            finally:
                if task.done() and self.task is task:
                    self.task = None
        if self.task is None:
            self.task = asyncio.create_task(asyncio.to_thread(probe))
        task = self.task
        try:
            # Keep one in-flight probe even if a network connect stalls.
            self.connected = await asyncio.wait_for(asyncio.shield(task), 8)
        except Exception:
            self.connected = False
        if task.done() and self.task is task:
            self.task = None
        return "CONNECTED" if self.connected else "DISCONNECTED"

    async def _dual_status(self):
        from doxagent.trade_execution.configuration import account_pin

        with self.journal.transaction(write=False) as db:
            pins = {environment: account_pin(db, environment) for environment in ("LIVE", "PAPER")}

        self.statuses = {
            "ib_gateway_" + environment.lower() + "_status": "DISCONNECTED"
            if pin
            else "NOT_CONFIGURED"
            for environment, pin in pins.items()
        }

        async def one(environment, serial):
            pin = pins[environment]
            if pin is None:
                return "NOT_CONFIGURED"
            connected = await asyncio.to_thread(
                probe, profile=pin["profile"], client_id=197401 + serial
            )
            status = "CONNECTED" if connected else "DISCONNECTED"
            self.statuses["ib_gateway_" + environment.lower() + "_status"] = status
            return status

        values = await asyncio.gather(one("LIVE", 0), one("PAPER", 1))
        return dict(ib_gateway_live_status=values[0], ib_gateway_paper_status=values[1])
