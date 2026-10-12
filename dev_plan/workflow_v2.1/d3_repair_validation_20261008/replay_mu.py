"""Read-only old MU case replay into a separate acceptance workspace; no SDK calls."""
import asyncio
import hashlib
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

from doxagent.codex_worker.local_client import LocalWorkspaceClient
from doxagent.codex_worker.workspace_store import LocalWorkspaceStore
from doxagent.workflows.codex_document3.orchestrator_v21 import (
    Document3OrchestratorV21, policy_batches,
)
from doxagent.workflows.codex_document3.materials_v21 import reference_path
from doxagent.workflows.codex_document3.state_v21 import StateV21, canonical, digest
from doxagent.workflows.codex_document3.validation_v21 import accept_policy

BASE = Path(__file__).resolve().parent
OLD = Path("D:/DoxAgentPilot/cases/document3/mu-d3-v21-sdkpilot-20261007-02")
RUN = "replay-mu-repair-01"


async def main():
    with sqlite3.connect((OLD / "runtime.sqlite").resolve().as_uri() + "?mode=ro", uri=True) as db:
        old_run_id = "d3pilot-mu-20261007-single-shell-02"
        old = json.loads(db.execute("SELECT payload FROM codex_document3_v21_runs WHERE run_id=?", (old_run_id,)).fetchone()[0])
        tasks = {k: json.loads(v) for k, v in db.execute("SELECT task_key,payload FROM codex_document3_v21_tasks WHERE run_id=?", (old_run_id,))}
        drafts = {k: json.loads(v) for k, v in db.execute("SELECT path,payload FROM codex_document3_v21_draft_ids WHERE run_id=?", (old_run_id,))}
    state = StateV21(BASE / "replay.sqlite")
    state.start(RUN, {"ticker": "MU"}, {**old, "identity": {"ticker": "MU"}})
    for path, draft in drafts.items():
        state.save_draft(RUN, path, draft)
    workspace = LocalWorkspaceClient(LocalWorkspaceStore(BASE / "replay_workspaces"))
    orchestrator = Document3OrchestratorV21(input_preparer=SimpleNamespace(),
        agent_runner=SimpleNamespace(workspace=workspace), state=state, node_assets={})
    inheritance = orchestrator._inheritance(state.run(RUN), RUN)
    counts = {}
    identities = []
    for phase in ("research", "supplement"):
        paths, topics = [], set()
        for key, record in tasks.items():
            if not key.startswith(phase + ":"):
                continue
            record = {**record, "workspace_run_id": "replay-owner"}
            record.pop("acceptance", None)
            _, owner, ordinal = key.split(":")
            prefix = f"output/work/v21/{phase}/{owner}/{int(ordinal):04d}/"
            agenda = old["agenda"] if phase == "research" else old["supplement_agenda"]
            names = agenda["waves"][int(ordinal)]
            accepted = await orchestrator._accept_build_wave(RUN, key, record, prefix, names, inheritance)
            paths.extend(accepted["paths"])
            topics.update(accepted["results"])
            for path in accepted["paths"]:
                identities.append(state.get_draft(RUN, path)["policy"] == drafts[path]["policy"])
        counts[phase] = {"policies": len(set(paths)), "results": len(topics),
                        "directory_batches": len(policy_batches([{"path": p} for p in set(paths)]))}
    final_paths = []
    for key, record in tasks.items():
        if not key.startswith("integration:final:"):
            continue
        for path, text in record.get("files", {}).items():
            if path not in old["final_candidates"]:
                continue
            raw = json.loads(text)
            inherited = inheritance.get(raw.get("policy_id"))
            if not inherited:
                raw.pop("policy_id", None)
            policy, errors = accept_policy(raw, state=state, run_id=RUN, path=path, inherited=inherited)
            assert policy and not errors, (path, errors)
            identities.append(policy.model_dump(mode="json") == old["final_candidates"][path])
            final_paths.append(path)
    for key, record in tasks.items():
        if key.startswith("integration:final:"):
            state.save_task(RUN, key, {**record, "candidate_paths": [p for p in final_paths if p in record.get("files", {})]})
    files = orchestrator.policy_reference_context(RUN)
    catalog = json.loads(files["context/document3/v21/policy_catalog.json"])
    for entry in catalog:
        text = files[entry["ref"]]
        assert entry["local_path"] == reference_path(entry["ref"], text)
        await workspace.write_text("replay-global", entry["local_path"], text)
        assert (await workspace.read_text("replay-global", entry["local_path"])).sha256 == entry["sha256"]
    assert counts["research"] == {"policies": 23, "results": 16, "directory_batches": 3}
    assert counts["supplement"] == {"policies": 15, "results": 15, "directory_batches": 2}
    assert len(final_paths) == 18 and all(identities)
    baseline = json.loads((BASE / "old_pilot_sha256.json").read_text(encoding="utf-8"))
    assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest() == sha for p, sha in baseline.items())
    report = {**counts, "final_candidates": len(final_paths), "full_policy_references": len(catalog),
              "identity_checks": len(identities), "old_artifacts_unchanged": len(baseline),
              "new_model_calls": 0, "old_review_edit_requests_inferred": False}
    (BASE / "replay_result.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    asyncio.run(main())
