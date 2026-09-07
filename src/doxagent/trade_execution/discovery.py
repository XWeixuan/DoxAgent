"""Read-only account discovery, before an exact account fuse can be configured."""

import threading
from typing import Any


def discover_accounts(host: str, port: int, client_id: int) -> list[str]:
    from ibapi.client import EClient  # type: ignore[import-untyped]
    from ibapi.wrapper import EWrapper  # type: ignore[import-untyped]

    ready = threading.Event()
    accounts: list[str] = []

    class App(EWrapper, EClient):  # type: ignore[misc]
        def __init__(self) -> None:
            EClient.__init__(self, self)

        def managedAccounts(self, accountsList: Any) -> Any:
            accounts.extend(a for a in accountsList.split(",") if a)
            ready.set()

        def error(
            self,
            reqId: Any,
            errorTime: Any,
            errorCode: Any,
            errorString: Any,
            advancedOrderRejectJson: Any = "",
        ) -> Any:
            pass  # No account/order/credential details printed by discovery.

    app = App()
    thread = None
    try:
        app.connect(host, port, client_id)
        thread = threading.Thread(target=app.run, daemon=True)
        thread.start()
        if not ready.wait(8):
            raise TimeoutError("ACCOUNT_DISCOVERY_TIMEOUT")
        return sorted(set(accounts))
    finally:
        app.disconnect()
        if thread:
            thread.join(timeout=2)
