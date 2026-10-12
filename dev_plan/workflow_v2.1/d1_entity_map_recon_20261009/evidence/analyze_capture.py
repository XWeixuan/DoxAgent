"""Verify capture, derive readable projections and record structural observations."""
import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path

from doxagent.codex_runtime.schema import EntityRelation, CodexD1Node
from doxagent.workflows.codex_document1.schema import NodeOutput, NODE_OUTPUT_SCHEMA
from doxagent.workflows.codex_document1.node_runner import validate_node_output
from doxagent.workflows.codex_document1.recovery import recover_output

root = Path(__file__).resolve().parents[1]
repo = root.parents[2]
manifest = json.loads((root / "evidence/remote_capture_manifest.json").read_text(encoding="utf-8"))
stats = {"captured_at": manifest["captured_at"], "verified_files": 0, "runtime_source_comparison": [], "tickers": {}}
for f in manifest["files"]:
    data = (root / f["name"]).read_bytes()
    assert len(data) == f["size_bytes"]
    assert hashlib.sha256(data).hexdigest() == f["sha256"]
    if f.get("expected_artifact_sha256"):
        assert f["expected_artifact_sha256"] == f["sha256"]
    stats["verified_files"] += 1
    prefix = "evidence/runtime_source/"
    if f["name"].startswith(prefix):
        rel = f["name"][len(prefix):]
        normalized = lambda b: b.decode("utf-8").replace("\r\n", "\n")
        local = (repo / rel).read_bytes()
        stats["runtime_source_comparison"].append({"path": rel, "normalized_equal": normalized(local) == normalized(data), "remote_sha256": f["sha256"], "local_sha256": hashlib.sha256(local).hexdigest()})

fields = ["关系主体", "关系对象", "关系类型", "关系说明", "关联业务或产品"]
def read(p):
    return json.loads((root / p).read_text(encoding="utf-8"))
def cell(s):
    return s.replace("|", "\\|").replace("\r", "").replace("\n", "<br>")
def pair(row):
    return row["关系主体"], row["关系对象"]
def cn_date(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone(timedelta(hours=8))).isoformat()

for ticker in ("INTC", "BE"):
    info = manifest["tickers"][ticker]
    rows = read(f"{ticker}/entity_map.json")
    bundle = read(f"{ticker}/bundle.raw.json")
    pre = read(f"{ticker}/c4_pre_scan.completion.raw.json")
    final = read(f"{ticker}/c4_enrichment.completion.raw.json")
    active = read(f"{ticker}/activation_revision.raw.json")
    assert active["artifacts"]["document1"]["run_id"] == info["run_id"]
    assert rows == bundle["entity_relations"] == final["entity_relations"]
    for row in rows:
        assert set(row) == set(fields)
        EntityRelation.model_validate(row)
    s = {"run_id": info["run_id"], "pre_scan_relations": len(pre["entity_relations"]), "published_relations": len(rows), "pre_scan_futures": len(pre["future_nodes"]), "published_futures": len(final["future_nodes"]), "active_d1_matches": True, "unique_relation_types": dict(Counter(r["关系类型"] for r in rows)), "empty_fields": sum(not value.strip() for row in rows for value in row.values()), "exact_duplicate_rows": len(rows)-len({json.dumps(r, sort_keys=True, ensure_ascii=False) for r in rows}), "duplicate_subject_object_pairs": [{"pair": list(p), "count": n} for p,n in Counter(pair(r) for r in rows).items() if n>1], "pre_pairs_absent_in_final": [list(pair(r)) for r in pre["entity_relations"] if pair(r) not in {pair(x) for x in rows}], "new_pairs": [list(pair(r)) for r in rows if pair(r) not in {pair(x) for x in pre["entity_relations"]}], "citation_styles": {}, "nodes": {}}
    for label, pattern in (("formal_alias", r"【cite:O[1-9]\d*】"), ("direct_url", r"https?://"), ("upstream_unavailable", r"\[upstream evidence unavailable\]")):
        s["citation_styles"][label+"_rows"] = sum(bool(re.search(pattern, " ".join(r.values()))) for r in rows)
    blocks = [f"# {ticker} Entity Map — 远端已发布快照", "", f"- Run ID：`{info['run_id']}`", f"- 研究截止：`{cn_date(info['initialization']['research_cutoff_at'])}`", f"- 发布：`{cn_date(info['published_at'])}`", f"- 关系数：{len(rows)}；当前激活 revision 的 D1 指针已核对匹配。", "- 以下是原始五字段的可读投影，保留原文和引用；未重新研究或更新关系事实。", "", "| " + " | ".join(fields) + " |", "| " + " | ".join(["---"]*5) + " |"]
    blocks += ["| " + " | ".join(cell(r[f]) for f in fields) + " |" for r in rows]
    for node in ("c4_pre_scan", "c4_enrichment"):
        m = read(f"{ticker}/{node}.citation_manifest.raw.json")
        ingestion = read(f"{ticker}/audit/{node}/ingestion.json")
        nrows = pre["entity_relations"] if node == "c4_pre_scan" else rows
        aliases = set(re.findall(r"【cite:(O[1-9]\d*)】", json.dumps(nrows, ensure_ascii=False)))
        entries = {e["alias"]:e for e in m["entries"]}
        attempts = info[node]["attempts"]
        seconds = (datetime.fromisoformat(attempts[-1]["completed_at"].replace("Z", "+00:00"))-datetime.fromisoformat(attempts[-1]["started_at"].replace("Z", "+00:00"))).total_seconds()
        s["nodes"][node] = {"attempt_id":info[node]["attempt_id"], "duration_seconds": seconds, "manifest_entries_total_output": len(m["entries"]), "unresolved_total_output": sum(not e["resolved"] for e in m["entries"]), "relation_aliases": sorted(aliases), "relation_unresolved_aliases": sorted(a for a in aliases if a not in entries or not entries[a]["resolved"]), "quarantined_count": len(ingestion.get("quarantined",[]))}
    c4_entries = {e["alias"]:e for e in read(f"{ticker}/c4_enrichment.citation_manifest.raw.json")["entries"]}
    aggregate_entries = {e["alias"]:e for e in bundle["citation_manifest"]["entries"]}
    s["c4_vs_aggregate_alias_url_conflicts"] = [{"alias":a, "c4_url":c4_entries[a].get("url"), "aggregate_url":aggregate_entries[a].get("url"), "c4_attempt_id":c4_entries[a].get("attempt_id"), "aggregate_attempt_id":aggregate_entries[a].get("attempt_id")} for a in s["nodes"]["c4_enrichment"]["relation_aliases"] if a in aggregate_entries and c4_entries[a].get("url") != aggregate_entries[a].get("url")]
    s["c4_aliases_absent_in_aggregate"] = [a for a in s["nodes"]["c4_enrichment"]["relation_aliases"] if a not in aggregate_entries]
    s["frozen_prompt_comparison"] = {}
    for node in ("c4_pre_scan", "c4_enrichment"):
        for frozen, current in (("task.md", "agents/c4.md"), ("skills/entity-map-and-future-nodes.md", "skills/entity-map-and-future-nodes.md"), ("schemas/node_output.schema.json", "schemas/node_output.schema.json")):
            left = (root / ticker / "frozen_inputs" / node / frozen).read_text(encoding="utf-8")
            right = (repo / "prompts/codex_v2/document1" / current).read_text(encoding="utf-8")
            s["frozen_prompt_comparison"][node+"/"+frozen] = left == right
    blocks += ["", "## C4 enrichment 的正式引用索引", "", "别名作用域为本次 enrichment attempt；不能用 Global Research 汇总文档的 citation manifest 解读这些 O 编号。`resolved=true` 表示平台找到了对应 Observation，不代表本次已独立复核事实。", ""]
    if not s["nodes"]["c4_enrichment"]["relation_aliases"]:
        blocks += ["本样本关系行没有正式 O 别名，原文主要以直接网页链接附在关系说明中；节点 manifest 中其他引用用于 warnings / Future Nodes 等输出。"]
    else:
        m = read(f"{ticker}/c4_enrichment.citation_manifest.raw.json")
        aliases = set(s["nodes"]["c4_enrichment"]["relation_aliases"])
        for e in m["entries"]:
            if e["alias"] in aliases:
                location = f"[{e.get('title') or e['alias']}]({e['url']})" if e.get("url") else f"`{e.get('source_locator')}`"
                blocks.append(f"- `{e['alias']}` → {location}；resolved=`{str(e['resolved']).lower()}`。")
    (root / ticker / "entity_map.md").write_text("\n".join(blocks)+"\n", encoding="utf-8")
    stats["tickers"][ticker] = s

# Small schema/validator probes document current acceptance behavior; no model calls.
empty_row = {f:"" for f in fields}
EntityRelation.model_validate(empty_row)
output = NodeOutput(status="arbitrary", entity_relations=[empty_row, empty_row], report_markdown="C4 unexpected report accepted")
validate_node_output(CodexD1Node.C4_ENRICHMENT, output)
future_only = NodeOutput(status="completed", future_nodes=[{"时间":"待定", "未来事项":"probe", "与目标公司的关系":"probe", "来源":"probe", "来源发布日期":"probe"}])
validate_node_output(CodexD1Node.C4_ENRICHMENT, future_only, require_complete_c4_enrichment=True)
recovered, quarantined = recover_output(json.dumps({"status":"completed", "entity_relations":[{**empty_row,"confidence":1}]}))
stats["acceptance_probes"] = {"empty_strings_accepted":True, "duplicate_rows_accepted":True, "arbitrary_status_accepted":True, "nonempty_c4_report_accepted":True, "future_only_satisfies_complete_flag":True, "extra_relation_field_stripped": "confidence" not in recovered.entity_relations[0].model_dump(), "extra_relation_field_quarantine_count":len(quarantined), "wire_schema_required":NODE_OUTPUT_SCHEMA["required"]}
(root / "evidence/analysis.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
print(json.dumps(stats, ensure_ascii=False, indent=2))
