"""Read-only, ticker-bounded evidence extraction; executed over SSH stdin."""
import base64
import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

result = {"captured_at": datetime.now(timezone.utc).isoformat(), "files": [], "tickers": {}, "safe_env": {}}

def add(name, body, origin, **metadata):
    if isinstance(body, str):
        body = body.encode("utf-8")
    result["files"].append({"name": name, "origin": origin, "sha256": hashlib.sha256(body).hexdigest(),
                            "size_bytes": len(body), "base64": base64.b64encode(body).decode(), **metadata})

def read_db(path):
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    c.execute("PRAGMA query_only=ON")
    c.execute("BEGIN")
    return c

research = read_db("/data/research/research.sqlite3")
control = read_db("/data/initialization/control.sqlite3")
for ticker in ("INTC", "BE"):
    candidates = []
    for key, raw in research.execute("SELECT record_key,payload_json FROM codex_runtime_records WHERE record_type='bundles' AND workflow_version='codex_global_research_v1'"):
        obj = json.loads(raw)
        if obj.get("ticker") == ticker and obj.get("status") == "published":
            candidates.append((obj.get("published_at", ""), key, raw, obj))
    candidates.sort(reverse=True)
    if not candidates:
        raise RuntimeError(f"No published bundle for {ticker}")
    _, run_id, raw, bundle = candidates[0]
    root = Path("/data/workspaces") / run_id
    info = {"run_id": run_id, "selection": "latest published Global Research in remote SQLite", "published_candidates": [{"run_id": v[1], "published_at": v[0]} for v in candidates], "published_at": bundle["published_at"], "relation_count": len(bundle["entity_relations"]), "future_node_count": len(bundle["future_nodes"])}
    result["tickers"][ticker] = info
    add(f"{ticker}/bundle.raw.json", raw, f"research.sqlite3:codex_runtime_records/bundles/{run_id}")
    add(f"{ticker}/entity_map.json", json.dumps(bundle["entity_relations"], ensure_ascii=False, indent=2)+"\n", f"derived losslessly from {ticker}/bundle.raw.json.entity_relations")
    artifacts = [json.loads(r[0]) for r in research.execute("SELECT payload_json FROM codex_runtime_records WHERE record_type='artifacts' AND run_id=?", (run_id,))]
    add(f"{ticker}/artifact_refs.json", json.dumps(artifacts, ensure_ascii=False, indent=2), f"research.sqlite3:codex_runtime_records/artifacts/{run_id}")
    for node in ("c4_pre_scan", "c4_enrichment"):
        ref = bundle["reports"].get(node)
        if ref is None:
            continue
        attempt = ref["attempt_id"]
        selected = [a for a in artifacts if a.get("node") == node and a.get("attempt_id") == attempt and a.get("kind") == "structured_completion"]
        path = root / (selected[0]["relative_path"] if selected else str(Path(ref["relative_path"]).with_name("completion.json")))
        body = path.read_bytes()
        expected = selected[0].get("sha256") if selected else None
        if expected and hashlib.sha256(body).hexdigest() != expected:
            raise RuntimeError(f"Artifact hash mismatch: {path}")
        add(f"{ticker}/{node}.completion.raw.json", body, str(path), expected_artifact_sha256=expected)
        add(f"{ticker}/{node}.report.raw.md", (root / ref["relative_path"]).read_bytes(), str(root / ref["relative_path"]), expected_artifact_sha256=ref["sha256"])
        manifest_row = research.execute("SELECT manifest_json FROM codex_local_citation_manifests WHERE run_id=? AND artifact_id=?", (run_id, ref["artifact_id"])).fetchone()
        if manifest_row:
            add(f"{ticker}/{node}.citation_manifest.raw.json", manifest_row[0], f"research.sqlite3:codex_local_citation_manifests/{run_id}/{ref['artifact_id']}")
        for relative in ("task.md", "task.json", "context.json", "bundle_manifest.json", "AGENTS.md", "schemas/node_output.schema.json", "skills/entity-map-and-future-nodes.md"):
            p = root / "attempts" / attempt / "input" / relative
            if p.exists():
                add(f"{ticker}/frozen_inputs/{node}/{relative}", p.read_bytes(), str(p))
        for relative in ("ingestion.json", "bundle.json", "validation.json"):
            p = root / "attempts" / attempt / "audit" / relative
            if p.exists():
                add(f"{ticker}/audit/{node}/{relative}", p.read_bytes(), str(p))
        attempts = [json.loads(r[0]) for r in research.execute("SELECT payload_json FROM codex_runtime_records WHERE record_type='attempts' AND run_id=?", (run_id,))]
        info[node] = {"attempt_id": attempt, "report_artifact_id": ref["artifact_id"], "attempts": [a for a in attempts if a.get("node") == node]}
    init_id = run_id.removesuffix("-d1")
    init = control.execute("SELECT payload FROM initialization_runs WHERE id=?", (init_id,)).fetchone()
    if init:
        init_obj = json.loads(init[0])
        info["initialization"] = {k: init_obj.get(k) for k in ("initialization_id", "ticker", "status", "research_cutoff_at", "created_at", "updated_at")}
    active = control.execute("SELECT revision_id FROM ticker_active_revision WHERE ticker=?", (ticker,)).fetchone()
    info["active_revision_id"] = active[0] if active else None
    if active:
        raw_activation = control.execute("SELECT payload FROM activation_revisions WHERE revision_id=?", (active[0],)).fetchone()
        if raw_activation:
            add(f"{ticker}/activation_revision.raw.json", raw_activation[0], f"control.sqlite3:activation_revisions/{active[0]}")
    node_row = control.execute("SELECT payload FROM initialization_nodes WHERE run_id=? AND node_key='d1'", (init_id,)).fetchone()
    if node_row:
        add(f"{ticker}/initialization_d1_node.raw.json", node_row[0], f"control.sqlite3:initialization_nodes/{init_id}/d1")

for relative in (
    "src/doxagent/workflows/codex_global_research/orchestrator.py",
    "src/doxagent/workflows/codex_document1/node_runner.py",
    "src/doxagent/workflows/codex_document1/orchestrator.py",
    "src/doxagent/workflows/codex_document1/schema.py",
    "src/doxagent/workflows/codex_document1/attempt_bundle.py",
    "src/doxagent/workflows/codex_document1/recovery.py",
    "src/doxagent/codex_runtime/schema.py",
    "src/doxagent/ticker_initialization/research_adapter.py",
    "prompts/codex_v2/document1/agents/c4.md",
    "prompts/codex_v2/document1/skills/entity-map-and-future-nodes.md",
    "prompts/codex_v2/document1/global_research/bundle_manifest.json",
    "prompts/codex_v2/document1/schemas/node_output.schema.json",
):
    p = Path("/app") / relative
    if p.exists():
        add("evidence/runtime_source/"+relative, p.read_bytes(), str(p))
for key in ("DOXAGENT_CODEX_MODEL", "DOXAGENT_CODEX_REASONING_EFFORT", "DOXAGENT_CODEX_NODE_TIMEOUT_SECONDS", "DOXAGENT_CODEX_RESEARCH_LANES_ENABLED"):
    result["safe_env"][key] = os.environ.get(key)
research.close()
control.close()
print(json.dumps(result, ensure_ascii=True))
