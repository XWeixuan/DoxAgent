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

        def nextValidId(self, orderId):
            self.ready.set()

        def managedAccounts(self, accountsList):
            if any(a.strip() for a in accountsList.split(",")):
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


def probe(factory=make_app):
    app, thread = None, None
    try:
        app = factory()
        app.connect(os.getenv("IBKR_TWS_HOST", "127.0.0.1"),
                    int(os.getenv("IBKR_TWS_PORT", "7496")),
                    clientId=int(os.getenv("DOXAGENT_IB_GATEWAY_HEALTH_CLIENT_ID", "197401")))
        thread = threading.Thread(target=app.run, daemon=True)
        thread.start()
        if not app.ready.wait(2) or not app.accounts.wait(2):
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
    def __init__(self):
        self.task = None
        self.connected = False

    async def status(self):
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
