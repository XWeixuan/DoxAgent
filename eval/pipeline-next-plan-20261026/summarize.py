"""Build small per-unit and probe summaries without crediting cached responses."""
import csv
import json
from collections import Counter
from pathlib import Path

from doxagent.message_bus_v2.cadence import distribution

root = Path(__file__).parent
reconciled = json.loads((root / "coverage-reconciliation.json").read_text())
categories = sorted(reconciled["total"])
with (root / "coverage-units.csv").open("w", encoding="utf-8-sig", newline="") as handle:
    writer = csv.DictWriter(handle, fieldnames=["binding_id", "unit", "expected", *categories])
    writer.writeheader()
    for unit in sorted(reconciled["units"], key=lambda r: (r["binding_id"], r["unit"])):
        writer.writerow({"binding_id": unit["binding_id"], "unit": unit["unit"],
                         "expected": unit["expected"], **{c: unit["categories"].get(c, 0) for c in categories}})
assert sum(u["expected"] for u in reconciled["units"]) == 2658
assert sum(reconciled["total"].values()) == 2658
assert sum(reconciled["total"][k] for k in categories if k != "fresh_by_old_definition") == 782
rows = [json.loads(line) for line in (root / "probe30-results.jsonl").read_text(encoding="utf-8-sig").splitlines()]
summary = {}
for source in sorted({r["source_id"] for r in rows}):
    values = [r for r in rows if r["source_id"] == source]
    physical = [r for r in values if not r.get("served_from_shared_capture")]
    summary[source] = {
        "calls": len(values), "shared_cache_calls": len(values)-len(physical),
        "physical_attempt_calls": len(physical),
        "statuses": dict(Counter(r.get("disposition", r.get("error_type")) for r in values)),
        "parse_errors": sum("error_type" in r for r in values),
        "physical_network_ms": distribution([r["network_ms"] for r in physical if "network_ms" in r]),
        "queue_ms": distribution([r["queue_wait_ms"] for r in physical if "queue_wait_ms" in r]),
        "message_counts": [r.get("message_count") for r in values],
        "identities": dict(Counter(r.get("identity_id") for r in physical)),
    }
(root / "probe30-summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
assert len(rows) == 16
assert sum(s["shared_cache_calls"] for s in summary.values()) == 1
outbox = json.loads((root / "outbox-amplification.json").read_text(encoding="utf-8-sig"))
assert sum(r["n"] for r in outbox["tables"]) == 17243
print(json.dumps({"logical_units": len(reconciled["units"]), "sources": len(reconciled["sources"]),
                  "cycles": 2658, "gap": 782, "probe_calls": 16, "cached_probe_calls": 1}))
