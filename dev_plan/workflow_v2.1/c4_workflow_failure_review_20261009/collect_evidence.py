"""Read existing C4 receipts and public tool events; never run a research model."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[2]
EVAL = REPO / "eval/c4_live_mu_20261009"
SESSIONS = Path("C:/Users/WEIXUANXIE/.codex/sessions/2026/10/09")
THREADS = {
    "02": "01a120df-434e-7841-8912-80dd270841f3",
    "03": "01a120f3-e144-7e93-b37f-a4bf1e385a0c",
    "04": "01a1210f-f512-71d2-861b-5905165488ad",
}


def main() -> None:
    records = []
    runs = []
    for number in ("01", "02", "03", "04"):
        run_id = f"c4-mu-local-20261009-{number}"
        workspace = EVAL / "workspaces" / run_id
        turn_nodes = {}
        stage_metrics = []
        for audit in sorted(workspace.glob("attempts/*/audit/turn_summary.json")):
            data = json.loads(audit.read_text(encoding="utf-8"))
            if data.get("turn_id"):
                turn_nodes[data["turn_id"]] = data.get("node", audit.parents[1].name)
            stage_metrics.append({
                "attempt": audit.parents[1].name,
                "node": data.get("node"),
                "job_status": data.get("job_status"),
                "failures": data.get("failures", []),
                "validation": data.get("validation"),
            })
        for receipt in sorted(workspace.glob("audit/jobs/*.json")):
            data = json.loads(receipt.read_text(encoding="utf-8"))
            if data.get("status") != "succeeded":
                records.append({
                    "run": number, "kind": "worker_failure", "attempt": data["attempt_id"],
                    "status": data["status"], "error_code": data.get("error_code"),
                    "error": data.get("error_message"), "receipt": str(receipt.relative_to(REPO)),
                })
        counts = Counter()
        seen = set()
        session_path = next(SESSIONS.glob(f"*{THREADS.get(number, 'no-session')}.jsonl"), None)
        if session_path:
            for line_number, line in enumerate(session_path.open(encoding="utf-8"), 1):
                value = json.loads(line)
                event = value.get("payload", {})
                if value.get("type") != "event_msg" or event.get("type") != "item_completed":
                    continue
                item = event.get("item", {})
                identity = (event.get("turn_id"), item.get("id"))
                if identity in seen:
                    continue
                seen.add(identity)
                result = item.get("result", {}) or {}
                structured = result.get("structuredContent", {}) or {}
                warning = structured.get("warning") if isinstance(structured, dict) else None
                failed = item.get("status") == "failed" or result.get("isError") is True
                if item.get("type") == "WebSearch":
                    counts["web_search"] += 1
                if item.get("server") == "source_capture":
                    counts["capture_calls"] += 1
                    if warning:
                        counts["capture_soft_warning"] += 1
                    elif structured.get("alias"):
                        counts["capture_alias_returned"] += 1
                if not (failed or warning):
                    continue
                counts["failed_events" if failed else "soft_warning_events"] += 1
                error = item.get("error") or structured.get("error")
                snippet = item.get("aggregated_output") or item.get("stdout")
                records.append({
                    "run": number, "node": turn_nodes.get(event.get("turn_id")),
                    "time": value.get("timestamp"), "kind": item.get("type"),
                    "name": f"{item.get('server', '')}.{item.get('tool', '')}",
                    "status": item.get("status"), "arguments": item.get("arguments"),
                    "command": item.get("command"), "error": error, "warning": warning,
                    "output_excerpt": snippet[:5000] if snippet else None,
                    "source_session": str(session_path), "source_line": line_number,
                })
        runs.append({"run": number, "tool_counts": dict(counts), "attempts": stage_metrics})
    target = ROOT / "evidence_inventory.json"
    target.write_text(json.dumps({"runs": runs, "events": records}, ensure_ascii=False, indent=2),
                      encoding="utf-8")
    print(json.dumps({"events": len(records), "counts": [
        {"run": run["run"], **run["tool_counts"]} for run in runs
    ]}, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
