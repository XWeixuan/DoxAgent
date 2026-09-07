"""Paper-only local TWS staged-order/cancel acceptance; never transmits to the broker.

The artificial $1 diagnostic price is not a strategy entry or fresh-quote acceptance.
"""

import argparse
import json
import time
from decimal import Decimal
from pathlib import Path

from ibapi.order import Order  # type: ignore[import-untyped]

from doxagent.persistent_runtime_v2.journal import RuntimeJournal
from doxagent.trade_execution.ibkr_session import IbkrSession
from doxagent.trade_execution.repository import ExecutionRepository
from doxagent.trade_execution.worker import WriterLock


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    repo = ExecutionRepository(RuntimeJournal(args.db))
    profile = repo.profile(args.profile)
    if profile.environment != "PAPER":
        raise ValueError("PAPER_ONLY_ACCEPTANCE")
    evidence = {"test": "local_staged_order_cancel", "transmit": False, "broker_orders": 0}
    with WriterLock(Path(args.db)) as owner:
        broker = IbkrSession(profile, write_guard=owner.assert_owned)
        order_id = None
        try:
            broker.connect()
            contract = broker.contract("MU")
            order_id = repo.allocate_order_id(
                profile.expected_account_id, profile.client_id, broker.next_id
            )
            order = Order()
            order.account = profile.expected_account_id
            order.orderRef = f"DA-LOCAL-TEST-{order_id}"
            evidence["order_id"] = order_id
            evidence["order_ref"] = order.orderRef
            order.action, order.orderType, order.tif = "BUY", "LMT", "DAY"
            order.totalQuantity, order.lmtPrice, order.transmit = Decimal(1), 1.0, False
            broker._fuse()
            broker.app.placeOrder(order_id, broker._contract_object(contract), order)
            deadline = time.monotonic() + 5
            while order_id not in broker.orders and time.monotonic() < deadline:
                time.sleep(0.1)
            evidence["staged_status"] = broker.orders.get(order_id, {}).get("status")
        finally:
            if order_id is not None:
                broker.cancel(
                    {
                        "account": profile.expected_account_id,
                        "client_id": profile.client_id,
                        "order_id": order_id,
                    }
                )
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline:
                    if broker.orders.get(order_id, {}).get("status") in {
                        "Cancelled",
                        "ApiCancelled",
                    }:
                        break
                    time.sleep(0.1)
                evidence["cancel_status"] = broker.orders.get(order_id, {}).get("status")
                evidence["result"] = (
                    "PASS"
                    if evidence["cancel_status"] in {"Cancelled", "ApiCancelled"}
                    else "UNCONFIRMED"
                )
            broker.close()
            destination = Path(args.output)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(json.dumps(evidence))
    if evidence.get("result") != "PASS":
        raise SystemExit("Local staged-order cleanup unconfirmed; inspect Paper TWS test order")


if __name__ == "__main__":
    main()
