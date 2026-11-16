"""Prepare optional historical context files; never used by the replay executor."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from doxagent.persistent_runtime_v2.schema import normalize_w2_result

from .dataset import load_cases, read_json, write_json


def context_from_case(original, *, events=None, state=None, recent=None, research_cutoffs=None):
    result = {}
    if original.get("w1_final"):
        result["w1_assessment"] = original["w1_final"]
    if original.get("w2_final"):
        w2 = original["w2_final"]
        result["w2_assessment"] = normalize_w2_result(w2) if "policy_ids" in w2 else w2
    w2 = result.get("w2_assessment", {})
    wanted = set(w2.get("policy_ids", [])) | set(w2.get("related_policy_ids", []))
    wanted.update(g["policy_id"] for g in w2.get("matched_condition_ids", []))
    projection = original.get("frozen_inputs", {}).get("projection", {})
    naming = original.get("frozen_inputs", {}).get(
        "policy_title_rows", projection.get("policies", [])
    )
    result["policy_title_rows"] = [
        {"policy_id": p["policy_id"], "title": p["title"]} for p in naming if "title" in p
    ]
    if "policies" in projection:
        result["policies"] = [p for p in projection["policies"] if p["policy_id"] in wanted]
    refs = result.get("w1_assessment", {}).get("reference_ids", [])
    if events is not None:
        selected = [e for e in events if e.get("event_id", e.get("provisional_event_id")) in refs]
        if len(selected) == len(set(refs)):
            result["w1_events"] = selected
    elif "w1_assessment" in result and not refs:
        result["w1_events"] = []
    if original.get("w2_alignment") is not None:
        from doxagent.persistent_runtime_v2.router import alignment_flags
        from doxagent.persistent_runtime_v2.schema import RuntimeCase

        # Older archives need their original contract-aware adapter; do not guess booleans.
        try:
            case = RuntimeCase.model_validate(original)
            hit, related = alignment_flags(case.w2_final, case.w2_alignment)
            result["alignment"] = dict(
                status=case.w2_alignment_status, error=case.w2_alignment_error
            )
            if hit is not None:
                result["alignment"]["hit_has_new_fact"] = hit
            if related is not None:
                result["alignment"]["related_has_new_fact"] = related
        except (ValueError, TypeError, KeyError):
            pass
    if state is not None and "pins" in state:
        result["pins"] = state["pins"]
    if recent is not None:
        result["recent_trade"] = recent
    if research_cutoffs is not None:
        result["research_cutoffs"] = research_cutoffs
    return result


def acquire_contexts(ids, host):
    script = """import json, sqlite3
from pathlib import Path
from doxagent.v2_read.native_content import NativeContent
from doxagent.persistent_runtime_v2.w1_r2_context import frozen_event_details

p = Path("/data/runtime/runtime.sqlite3")
db = sqlite3.connect(p.as_uri() + "?mode=ro", uri=True)
codec = NativeContent(p)


def value(ns, key):
    row = db.execute(
        "select payload from runtime_values where namespace=? and key=?", (ns, key)
    ).fetchone()
    return codec.decode(json.loads(row[0])) if row else None


for cid in IDS:
    row = db.execute("select payload_json from runtime_v2_cases where case_id=?", (cid,)).fetchone()
    if not row:
        continue
    c = codec.decode(json.loads(row[0]))
    f = c.get("frozen_inputs", {})
    w = c.get("w2_final") or {}
    wanted = set(w.get("policy_ids", [])) | set(w.get("related_policy_ids", []))
    wanted.update(g["policy_id"] for g in w.get("matched_condition_ids", []))
    proj = f.get("projection", {})
    if wanted and any(
        "title" not in p or "activation_conditions" not in p for p in proj.get("policies", [])
    ):
        try:
            research = sqlite3.connect("file:/data/research/research.sqlite3?mode=ro", uri=True)
            row = research.execute(
                "select policy_set_json from codex_document3_policy_sets "
                "where ticker=? and policy_set_version=?",
                (c["source"]["snapshot"]["ticker"], c["version_pin"]["policy_set_version"]),
            ).fetchone()
            if row:
                full = NativeContent(Path("/data/research/research.sqlite3")).decode(
                    json.loads(row[0])
                )
                proj = {"policies": full.get("policies", [])}
            research.close()
        except sqlite3.OperationalError:
            pass
    small = {
        k: c[k]
        for k in (
            "case_id",
            "source",
            "version_pin",
            "w1_final",
            "w2_final",
            "w2_alignment",
            "w2_alignment_status",
            "w2_alignment_error",
            "w2_contract_version",
            "orchestration_revision",
            "w3_contract_version",
            "created_at",
            "trading_date",
        )
        if k in c
    }
    small["frozen_inputs"] = (
        {
            "projection": {
                "policies": [p for p in proj.get("policies", []) if p["policy_id"] in wanted]
            }
        }
        if "policies" in proj
        else {}
    )
    small["frozen_inputs"]["policy_title_rows"] = [
        {"policy_id": p["policy_id"], "title": p["title"]}
        for p in proj.get("policies", [])
        if "title" in p
    ]
    round = value("round_inputs", cid + ":W1:R2")
    events = None
    if round is not None:
        details = frozen_event_details(round)
        events = details.get("canonical_events", []) + details.get("provisional_events", [])
    state = value("w3_global_input", cid)
    recent = value("w3_recent_filled_snapshot", cid)
    print(
        json.dumps(
            dict(case_id=cid, case=small, events=events, state=state, recent=recent),
            ensure_ascii=False,
        ),
        flush=True,
    )
"""
    proc = subprocess.run(
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=12",
            host,
            "sudo -n docker exec -i doxagent-v2-codex-worker-1 /app/.venv/bin/python -",
        ],
        input="IDS=" + repr(ids) + "\n" + script,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
    )
    if proc.returncode:
        raise RuntimeError(proc.stderr[-1500:])
    return {
        v["case_id"]: v
        for v in (json.loads(line) for line in proc.stdout.splitlines() if line.strip())
    }


def enrich_contexts(dataset, *, host="doxagent-sg", case_keys=None, remote=True):
    root = Path(dataset)
    cases = [c for c in load_cases(root) if not case_keys or c["case_key"] in case_keys]
    fetched, remote_error = {}, None
    if remote:
        try:
            fetched = acquire_contexts(
                [c["original_case_id"] for c in cases if c.get("original_case_id")], host
            )
        except (RuntimeError, subprocess.TimeoutExpired) as exc:
            remote_error = str(exc)
    report = []
    for case in cases:
        key = case["case_key"]
        archive = fetched.get(case.get("original_case_id"))
        source = "remote_frozen_case"
        if archive is None:
            paths = [
                root / "preparation/additions_20261114" / (key + ".json"),
                root.parent / "w3_history_20261014/inputs" / key / "case.json",
            ]
            for path in paths:
                if path.exists():
                    old = read_json(path)
                    original = old.get("original_case", old.get("case"))
                    if original:
                        archive = dict(case=original)
                        source = str(path)
                        break
        research = root / "research" / case.get("research_key", "absent")
        cutoffs = {}
        if (research / "document1.md").exists():
            cutoffs["D1"] = None
        if (research / "document2.json").exists():
            cutoffs["D2"] = read_json(research / "document2.json").get("as_of")
        context = dict(research_cutoffs=cutoffs)
        if archive:
            recent = archive.get("recent")
            # v6 membership is only reusable when the scenario cutoff is identical.
            if recent and recent.get("cutoff_at") != case["as_of"]:
                recent = None
            state = archive.get("state")
            bus_time = archive["case"].get("source", {}).get("message_bus_event_time")
            if bus_time != case["as_of"]:
                state = None  # Do not silently move a later PIN snapshot into an earlier scenario.
            context = context_from_case(
                archive["case"],
                events=archive.get("events"),
                state=state,
                recent=recent.get("items") if recent else None,
                research_cutoffs=cutoffs,
            )
        target = root / "inputs" / key / "context.json"
        if target.exists():
            write_json(
                root / "preparation/context_alignment_20261116/before" / (key + ".json"),
                read_json(target),
            )
            context = {**read_json(target), **context}
        write_json(target, context)
        report.append(
            dict(
                case_key=key,
                source=source if archive else "existing_partial_input",
                provided=sorted(context),
                missing=[
                    k
                    for k in (
                        "w1_assessment",
                        "w1_events",
                        "w2_assessment",
                        "policies",
                        "pins",
                        "recent_trade",
                    )
                    if k not in context
                ],
            )
        )
    report_path = root / "preparation/context_alignment_20261116/report.json"
    previous = read_json(report_path).get("cases", []) if report_path.exists() else []
    merged = {row["case_key"]: row for row in previous}
    merged.update({row["case_key"]: row for row in report})
    result = dict(cases=list(merged.values()), remote_error=remote_error)
    write_json(root / "preparation/context_alignment_20261116/report.json", result)
    return result
