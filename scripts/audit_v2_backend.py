"""Offline route/OpenAPI inventory. A missing contract route is a failed release gate."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from doxagent.api_v2.app import create_app
from doxagent.api_v2.openapi import coverage
from doxagent.persistent_runtime_v2.journal import RuntimeJournal
from doxagent.v2_control.repository import ControlRepository
from doxagent.v2_read.repository import ReadStore


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with TemporaryDirectory(prefix="doxagent-v2-contract-") as directory:
        root = Path(directory)
        store = ReadStore(root / "read.db")
        store.migrate()
        control = ControlRepository(RuntimeJournal(root / "runtime.db"))
        control.migrate()
        # No HTTP request occurs. This object cannot authenticate a production request.
        app = create_app(store=store, control=control, auth=object())
        report = coverage(app)
        report["scope"] = "ROUTE_AND_OPENAPI_COVERAGE_ONLY"
        spec = app.openapi()
        report["status"] = "PASS" if not report["missing"] and not report["unexpected"] else "INCOMPLETE"
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output / "route-coverage.json").write_text(json.dumps(report, indent=2)+"\n", encoding="utf-8")
        (args.output / "openapi.json").write_text(json.dumps(spec, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
        print(json.dumps({k: report[k] for k in ("expected", "implemented", "status")}))
        return int(report["status"] != "PASS")


if __name__ == "__main__":
    raise SystemExit(main())
