"""Reuse the canonical narrow overlay release, without rebuilding unchanged UI."""

import dynamic_ownership_release as release

release.ROLES = ["v2-api", "v2-projector", "v2-executor"]
release.FILES = [
    "src/doxagent/trade_execution/capital.py",
    "src/doxagent/trade_execution/manual_finance.py",
    "src/doxagent/trade_execution/repository.py",
    "src/doxagent/revenue_audit/ledger.py",
    "src/doxagent/revenue_audit/projection.py",
    "src/doxagent/v2_read/pnl.py",
]

if __name__ == "__main__":
    release.main()
