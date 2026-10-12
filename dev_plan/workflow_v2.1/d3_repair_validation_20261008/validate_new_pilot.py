import json,sqlite3,hashlib
from pathlib import Path
from collections import Counter
from doxagent.workflows.codex_document3.state_v21 import canonical
root=Path("D:/DoxAgentPilot/cases/document3/mu-d3-v21-repair-20261008-01")
run_id="d3pilot-mu-20261008-repair-01"
db=sqlite3.connect("file:"+str(root/"runtime.sqlite")+"?mode=ro",uri=True)
run=json.loads(db.execute("select payload from codex_document3_v21_runs where run_id=?",(run_id,)).fetchone()[0])
tasks={key:json.loads(text) for key,text in db.execute("select task_key,payload from codex_document3_v21_tasks where run_id=?",(run_id,))}
assert run.get("commit"), "Pilot must finish before final validation"
full_reads=0; checks=[]; models=[]; node_counts=Counter(); issue_rows=[]; timings=[]
for key,task in tasks.items():
    owner=task["owner_slot"]; phase=task["phase"]
    assert owner!="GLOBAL" or phase in {"planning","integration"}, key
    owner_root=root/"workspaces"/task["workspace_run_id"]
    request=task["request"]
    assert request["model"]=="gpt-6-sol" and request["effort"]=="high"
    node_counts[request["node"]]+=1
    context=json.loads((owner_root/task["task_path"]).read_text(encoding="utf8"))
    if context.get("policy_catalog"):
        mapping={m["ref"]:m["local_path"] for m in context["read_mapping"]}
        pool=json.loads((owner_root/mapping[context["policy_catalog"]]).read_text(encoding="utf8"))
        for item in pool:
            assert mapping[item["ref"]]==item["local_path"], (key,item["ref"])
            body=(owner_root/item["local_path"]).read_bytes()
            assert hashlib.sha256(body).hexdigest()==item["sha256"], (key,item["ref"])
            full_reads+=1
        checks.append({"task":key,"pool_count":len(pool),"focus_directories":context.get("focus_directories",[])})
    audit=owner_root/"attempts"/request["attempt_id"]/"audit"
    receipt=json.loads((audit/"pilot_sdk_receipt.json").read_text(encoding="utf8"))
    assert not receipt.get("review_error"), (key,receipt.get("review_error"))
    selected=audit/"model_selection.json"
    assert selected.exists(), key
    model=json.loads(selected.read_text(encoding="utf8")); models.append(model)
    assert model["execution_model"]=="gpt-6-sol" and model["effort"]=="high"
    report=json.loads((audit/"pilot_review.json").read_text(encoding="utf8"))
    issue_rows.extend({"task":key,**issue} for issue in report["issues"])
    summaries={phase_name:json.loads((audit/(phase_name+"_summary.json")).read_text(encoding="utf8")) for phase_name in ["research","review"]}
    timings.append({"task":key,"owner":owner,"node":request["node"],
        "sdk_ms":{phase_name:summary["wall_time_ms"] for phase_name,summary in summaries.items()},
        "sdk_boundaries":{phase_name:{k:summary.get(k) for k in ["started_at","finished_at"]} for phase_name,summary in summaries.items()},
        "host_spans":task.get("host_spans",[]),"host_metrics":task.get("host_metrics",{}),
        "pilot_host_spans":receipt.get("host_spans",[]),"pilot_host_metrics":receipt.get("host_metrics",{}),
        "research_telemetry":summaries["research"].get("telemetry",{})})
assert run.get("affected_paths",[])==run.get("supplement_paths",[])
for batch in run.get("final_schedule",[]):assert len(batch["focus_directories"])<=5
con=tasks["integration:consolidation"]
owner_root=root/"workspaces"/con["workspace_run_id"]
context=json.loads((owner_root/con["task_path"]).read_text(encoding="utf8"))
mapping={m["ref"]:m["local_path"] for m in context["read_mapping"]}
effective=run.get("post_supplement_review") or run.get("review")
assert json.loads((owner_root/mapping["context/document3/v21/review_frozen.json"]).read_text(encoding="utf8"))==effective
assert not (root/"workspaces"/(run_id+"-global")/"context/document3/v21/assets/discovery.md").exists()
assert not (root/"workspaces"/(run_id+"-global")/"context/document3/v21/assets/build.md").exists()
summary={"run_id":run_id,"status":run["status"],"phase":run["phase"],"orchestration_revision":run["orchestration_revision"],
    "node_calls":dict(node_counts),"main_topics":len(run["agenda"]["topics"]),"main_policies":len(run["basis"]),
    "supplement_topics":len(run["supplement_agenda"]["topics"]),"supplement_policies":len(run["supplement_paths"]),
    "final_candidates":len(run["final_candidates"]),"published_policies":len(run["final"]),
    "main_review_batches":len(run["integration_schedule"]),"post_batches":len(run.get("post_supplement_schedule",[])),
    "final_batches":len(run.get("final_schedule",[])),"full_pool_read_checks":full_reads,"catalogs":checks,
    "models_checked":len(models),"missing":run["missing"],"remaining_gaps":run["coverage"]["remaining_gaps"],
    "issues":issue_rows,"timings":timings}
(root/"repair_validation.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf8")
print(json.dumps({k:v for k,v in summary.items() if k not in ["issues","timings","catalogs"]},ensure_ascii=False))
