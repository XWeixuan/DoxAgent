"""Explicit historical classification backed by an exact captured source receipt."""

import hashlib
import json
from pathlib import Path

from .maintenance import digest
from .projector import native
from .repository import encode

CLASSIFICATIONS = {"BUSINESS_V2", "CANDIDATE", "ACCEPTANCE_TEST", "PROVENANCE_UNVERIFIED"}


def import_manifest(runtime_path, sources, manifest_path, *, dry_run=False):
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    if manifest.get("format") != "doxagent.v2.history.1" or not manifest.get("actor"):
        raise ValueError("history manifest format and accountable actor are required")
    by_source = {source.source: source for source in sources}
    verified = []
    for item in manifest["entries"]:
        if item["classification"] not in CLASSIFICATIONS:
            raise ValueError("unknown historical classification")
        event = by_source[item["source"]].event(int(item["source_seq"]))
        if not event:
            raise ValueError("historical source receipt is missing")
        value = native(event)
        key = {
            "runtime_v2_cases": "case_id",
            "standard_messages": "standard_message_id",
            "initialization_runs": "initialization_id",
            "activation_revisions": "revision_id",
        }.get(event["table_name"])
        if key is None:
            raise ValueError("unsupported historical entity; no implicit bulk classification")
        ticker = value.get("ticker") or value.get("source", {}).get("snapshot", {}).get("ticker")
        if ticker != item["ticker"] or value[key] != item["entity_id"]:
            raise ValueError("history identity/ticker does not match the source receipt")
        if not item.get("evidence"):
            raise ValueError("explicit business/test provenance evidence is required")
        for evidence in item["evidence"]:
            if digest(evidence["path"]) != evidence["sha256"]:
                raise ValueError("historical evidence checksum mismatch")
        verified.append(
            {
                **item,
                "actor": manifest["actor"],
                "source_receipt_sha256": hashlib.sha256(encode(event["row"]).encode()).hexdigest(),
            }
        )
    if dry_run:
        return {"verified": len(verified), "written": 0}
    from doxagent.persistent_runtime_v2.journal import RuntimeJournal

    with RuntimeJournal(runtime_path, initialize=False).transaction() as db:
        for value in verified:
            identity = value["ticker"] + ":" + value["entity_id"]
            prior = db.execute(
                "SELECT payload FROM v2_business_imports WHERE id=?", (identity,)
            ).fetchone()
            if prior and prior[0] != encode(value):
                raise ValueError("historical classification is immutable; conflicting manifest")
            db.execute(
                "INSERT OR IGNORE INTO v2_business_imports VALUES(?,?,?)",
                (identity, value["ticker"], encode(value)),
            )
    return {"verified": len(verified), "written": len(verified)}
