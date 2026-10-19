"""Explicit read-model membership repair; published research bodies stay immutable."""

import argparse
import hashlib
import json
from pathlib import Path

from .artifacts import PublishedArtifacts
from .policy_origins import memberships, published_origins
from .repository import ReadStore, encode


def record_value(store, kind, ticker, row):
    # Keep immutable definition handles: only membership metadata is changing.
    # Native file handles must be decoded before ingest can encode them again.
    data = store.content_codec.decode(json.loads(row["raw_payload"]), expand_definitions=False)
    return dict(kind=kind, ticker=ticker, id=row["id"], parent=row["parent"],
                sort=row["sort_key"], day=row["day"], source_id=row["source_id"],
                route=row["route"], search=row["search_text"], data=data)


def records(store, kind, ticker):
    with store.connect() as db:
        return [
            record_value(store, kind, ticker, r)
            for r in db.execute(
                "SELECT id,parent,sort_key,day,source_id,route,search_text,payload AS raw_payload "
                "FROM object_current WHERE kind=? AND ticker=?", (kind, ticker),
            )
        ]


def plan(store, source, ticker, repairs):
    output, revised = [], {}
    bases = {}
    for value in sorted(repairs.values(), key=lambda v: v["version"]):
        frozen = value.get("source_document2_ref")
        if frozen:
            bases[(frozen["run_id"], frozen["sha256"])] = value["policy_origins"]
    for run_id, value in repairs.items():
        prior = store.get("policy_origin_repair", ticker, run_id)
        if prior != value:
            output.append(dict(kind="policy_origin_repair", ticker=ticker, id=run_id, data=value))
    # The immutable D3 version selects its own source D2 and origins; never use
    # a policy's latest content or a current-D2 positional index for old versions.
    shells_by_run = {}
    for shell in records(store, "shell", ticker):
        shells_by_run.setdefault(shell["parent"], []).append(shell["data"])
    for record in sorted(records(store, "policy_set", ticker),
                         key=lambda r: r["data"]["reference"]["policy_set_version"]):
        data = record["data"]
        if data["reference"]["schema_version"] != "document3.v3":
            continue
        run_id = data["reference"]["run_id"]
        if run_id in repairs and repairs[run_id].get("source_document2_ref"):
            frozen = repairs[run_id]["source_document2_ref"]
            actual = data["source_document2"]
            if any(frozen[k] != actual[v] for k, v in (
                ("run_id", "run_id"), ("artifact_id", "artifact_id"),
                ("sha256", "content_sha256"),
            )):
                raise ValueError("policy origin repair source D2 mismatch")
        bundle = source.record("bundles", run_id, run_id)
        origins = published_origins(source, bundle)
        bound = data.get("source_document2") or {}
        inherited = bases.get((bound.get("run_id"), bound.get("content_sha256")), {})
        origins = {**inherited, **{k: v for k, v in origins.items() if v}}
        if run_id in repairs:
            origins = {**origins, **repairs[run_id]["policy_origins"]}
        if data.get("policy_origins") != origins:
            output.append({**record, "data": {**data, "policy_origins": origins}})
        source_run = (data.get("source_document2") or {}).get("run_id")
        if run_id in repairs:
            basis = dict(source_document2=data["source_document2"],
                         policy_origins=repairs[run_id]["policy_origins"])
            revised_basis = dict(
                kind="policy_origin_basis", ticker=ticker, id=source_run, data=basis
            )
            # Later accepted versions extend known origins for the same frozen D2.
            output = [r for r in output if not (
                r["kind"] == "policy_origin_basis" and r["id"] == source_run
            )]
            if store.get("policy_origin_basis", ticker, source_run) != basis:
                output.append(revised_basis)
        views = shells_by_run.get(source_run, [])
        with store.connect() as db:
            details = list(db.execute(
                "SELECT id,parent,sort_key,day,source_id,route,search_text,payload AS raw_payload,"
                "json_extract(payload,'$.summary') AS summary FROM object_current "
                "INDEXED BY current_parent WHERE kind='policy_detail' AND ticker=? AND parent=?",
                (ticker, record["id"]),
            ))
        for row in details:
            identity, summary = row["id"], json.loads(row["summary"])
            matched, unresolved = memberships(origins.get(summary["policy_id"], []), views)
            revised[identity] = (matched, unresolved)
            if (summary["shell_ids"], summary["unresolved_shell_ids"]) != (matched, unresolved):
                detail = record_value(store, "policy_detail", ticker, row)
                output.append({**detail, "data": {**detail["data"], "summary": {
                                       **summary, "shell_ids": matched,
                                       "unresolved_shell_ids": unresolved,
                                   }}})
    catalog = records(store, "policy_catalog", ticker)
    by_key = {}
    for record in catalog:
        summary = record["data"]
        selected = revised.get(summary["policy_revision_id"])
        if selected is not None:
            matched, unresolved = selected
            if (summary["shell_ids"], summary["unresolved_shell_ids"]) != selected:
                record = {**record, "data": {**summary, "shell_ids": matched,
                                             "unresolved_shell_ids": unresolved}}
                output.append(record)
        by_key[record["id"]] = record["data"]
    changes = records(store, "policy_change", ticker)
    existing = {r["id"]: r for r in changes}
    desired = {}
    for record in changes:
        # One canonical ALL row, plus one membership row for each source Shell.
        targets = {"ALL", *by_key.get(record["route"], {}).get("shell_ids", [])}
        for shell in targets:
            identity = record["data"]["change_id"] + ":" + shell
            candidate = {**record, "id": identity, "parent": shell}
            if existing.get(identity) != candidate:
                desired[identity] = candidate
    output.extend(desired.values())
    return output


def apply(store, planned):
    for offset in range(0, len(planned), 100):
        batch = planned[offset:offset+100]
        digest = hashlib.sha256(encode(batch).encode()).hexdigest()
        store.ingest("policy-origin-repair", digest, batch)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--read-db", type=Path, required=True)
    parser.add_argument("--research-db", type=Path, required=True)
    parser.add_argument("--repairs", type=Path, required=True)
    parser.add_argument("--ticker", action="append", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    store, source = ReadStore(args.read_db), PublishedArtifacts(args.research_db)
    repairs = json.loads(args.repairs.read_text(encoding="utf-8"))
    from doxagent.trade_execution.worker import WriterLock

    with WriterLock(Path(str(store.path)+".projector")):
        for ticker in args.ticker:
            with store.projection_session():
                planned = plan(store, source, ticker, repairs.get(ticker, {}))
            counts = {kind: sum(r["kind"] == kind for r in planned)
                      for kind in sorted({r["kind"] for r in planned})}
            if args.apply:
                with store.projection_session():
                    apply(store, planned)
            print(json.dumps(dict(ticker=ticker, applied=args.apply, records=counts)), flush=True)


if __name__ == "__main__":
    main()
