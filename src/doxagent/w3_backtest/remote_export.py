"""Read-only dataset acquisition. Executed through SSH stdin, emits JSONL."""

import gzip
import hashlib
import json
import random
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path("/var/lib/docker/volumes/doxagent-v2_v2-data/_data")
SELECTION = "__SELECTION_JSON__"


def ro(path):
    db = sqlite3.connect("file:" + str(path) + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    db.execute("BEGIN")
    return db


def emit(kind, **kwargs):
    print(json.dumps(dict(kind=kind, **kwargs), ensure_ascii=False), flush=True)


def decode(value):
    if isinstance(value, dict) and "$doxagent_content_v1" in value:
        sha = value["$doxagent_content_v1"]
        for base in (
            ROOT / "read/native-files",
            ROOT / "runtime/native-files",
            ROOT / "research/native-files",
        ):
            folder = base / sha[:2] / sha
            if not (folder / "manifest.json").exists():
                continue
            manifest = json.loads((folder / "manifest.json").read_text())
            raw = b"".join(
                gzip.decompress((folder / str(i)).read_bytes())
                for i in range(len(manifest["chunks"]))
            )
            assert hashlib.sha256(raw).hexdigest() == sha
            return decode(json.loads(raw))
        raise FileNotFoundError("external content " + sha)
    if isinstance(value, dict):
        return {k: decode(v) for k, v in value.items()}
    if isinstance(value, list):
        return [decode(v) for v in value]
    return value


def load(value):
    return decode(json.loads(value))


def at(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


if __name__ == "__main__":
    rt = ro(ROOT / "runtime/runtime.sqlite3")
    research = ro(ROOT / "research/research.sqlite3")
    windows = []
    for ticker, mode, payload in rt.execute("SELECT ticker,mode,payload FROM v2_mode_binding"):
        binding = load(payload)
        row = rt.execute(
            "SELECT payload FROM te_profiles WHERE revision=?", (binding["revision"],)
        ).fetchone()
        profile = load(row[0]) if row else None
        windows.append(
            dict(
                ticker=ticker,
                mode=mode,
                binding_revision=binding["revision"],
                exit_offset_minutes=profile["strategy"]["exit_offset_minutes"] if profile else None,
            )
        )
    emit(
        "execution_window",
        bindings=windows,
        basis="current production mode binding configuration, replay uses historical cutoff clock",
    )
    cases = {
        r["case_id"]: dict(json.loads(r["payload_json"]), ticker=r["ticker"])
        for r in rt.execute("SELECT case_id,ticker,payload_json FROM runtime_v2_cases")
    }
    focus = json.loads(SELECTION)
    excluded = {c["case_id"] for c in focus}
    pool = sorted(
        k
        for k, v in cases.items()
        if k not in excluded
        and v.get("ticker") in ["MU", "BE", "INTC", "RKLB"]
        and v.get("status") == "COMPLETED"
        and v.get("w1_final")
        and v.get("w2_final")
        and "2026-09-22" <= v.get("created_at", "")[:10] <= "2026-10-09"
    )
    selected = random.Random(20261014).sample(pool, 25)
    focus += [
        dict(case_id=cid, case_key=f"NOISE-{i:02d}", group="noise")
        for i, cid in enumerate(selected, 1)
    ]
    emit("selection", seed=20261014, candidate_count=len(pool), cases=focus)
    w3 = {}
    for row in rt.execute("SELECT payload_json FROM runtime_v2_w3_cases"):
        value = load(row[0])
        w3.setdefault(value["case_id"], []).append(value)
    values = {}
    for row in rt.execute(
        "SELECT namespace,key,payload FROM runtime_values WHERE namespace IN "
        "('worker_receipts','trade_intents','analysis_trade_decisions',"
        "'candidates','watches','pins','policy_consumptions')"
    ):
        values.setdefault(row["namespace"], []).append(load(row["payload"]))

    research_keys = set()
    for selected_case in focus:
        cid = selected_case["case_id"]
        c = decode(cases[cid])
        ws = sorted(w3.get(cid, []), key=lambda x: x["created_at"])
        original_w3 = ws[0] if ws else None
        cutoff = (
            original_w3["created_at"] if original_w3 else (c.get("updated_at") or c["created_at"])
        )
        basis = "w3_route_created_at" if original_w3 else "w1_w2_route_completed_at"
        if original_w3:
            receipts = [
                v
                for v in values.get("worker_receipts", [])
                if original_w3["w3_case_id"] in json.dumps(v) and v.get("started_at")
            ]
            if receipts:
                cutoff = min(v["started_at"] for v in receipts)
                basis = "original_worker_started_at"
        t0 = at(cutoff)
        history = []
        for intent in values.get("trade_intents", []):
            issued = (
                intent.get("released_at") or intent.get("recorded_at") or intent.get("created_at")
            )
            if (
                not issued
                or intent.get("case_id") == cid
                or (intent.get("trade") or {}).get("ticker") not in (None, c["ticker"])
            ):
                continue
            if t0 - timedelta(hours=72) <= at(issued) < t0:
                trade = intent.get("trade") or {}
                hc = cases.get(intent.get("case_id"))
                if hc and hc["ticker"] != c["ticker"]:
                    continue
                # Prior W3 decisions are historical inputs; current execution status is not.
                safe_trade = {
                    k: v for k, v in trade.items() if k not in ("daily_status", "w3_case_id")
                }
                history.append(
                    dict(
                        intent_id=intent.get("intent_id"),
                        case_id=intent.get("case_id"),
                        released_at=issued,
                        status="UNKNOWN",
                        status_basis=(
                            "present terminal status excluded; "
                            "no timestamped transition reconstructed"
                        ),
                        trade=safe_trade,
                        source=hc.get("source") if hc else None,
                    )
                )
        frozen = c.get("frozen_inputs", {})
        state = dict(
            recent_trade_intents=history,
            history_matches=[],
            watches=[],
            pins=[],
            policy_consumptions=[],
            provisional=frozen.get("provisional", {}),
            limitations=[
                "Legacy dataset has no PIN; unavailable historical WATCH/consumption "
                "kept empty, not populated from current state."
            ],
        )
        pin = c["version_pin"]
        key = hashlib.sha256(
            json.dumps(
                [
                    c["ticker"],
                    pin.get("document1_run_id"),
                    pin.get("document2_run_id"),
                    pin.get("policy_set_version"),
                    pin.get("event_library_root"),
                    pin.get("event_library_version"),
                ]
            ).encode()
        ).hexdigest()[:20]
        from doxagent.event_library.provider import PublishedEventLibraryReader

        reader = PublishedEventLibraryReader(
            ROOT / (pin.get("event_library_root") or "/data/events").removeprefix("/data/")
        )
        refs = list(dict.fromkeys((c.get("w1_final") or {}).get("reference_ids", [])))
        detail = reader.event_details(c["ticker"], refs, version=pin["event_library_version"])
        from doxagent.persistent_runtime_v2.service import _w1_canonical_event_business_payload

        state["event_details"] = {
            "canonical_events": [_w1_canonical_event_business_payload(e) for e in detail.events]
            if detail
            else [],
            "provisional_events": [],
        }
        emit(
            "case",
            selection=selected_case,
            case=c,
            original_w3=original_w3,
            evidence_cutoff_at=cutoff,
            cutoff_basis=basis,
            research_key=key,
            historical_state=state,
        )
        if key in research_keys:
            continue
        research_keys.add(key)
        texts = {}
        for name, run in [
            ("document1", pin.get("document1_run_id")),
            ("document2", pin.get("document2_run_id")),
        ]:
            row = research.execute(
                "SELECT payload_json FROM codex_runtime_records "
                "WHERE record_type='bundles' AND record_key=?",
                (run,),
            ).fetchone()
            if not row:
                raise ValueError("bundle missing " + str(run))
            bundle = load(row[0])
            handoff = bundle["handoff"]
            aid = handoff.get("document_artifact_id") or handoff.get("document2_artifact_id")
            row = research.execute(
                "SELECT payload_json FROM codex_runtime_records "
                "WHERE record_type='published_documents' AND run_id=? AND record_key=?",
                (run, aid),
            ).fetchone()
            doc = load(row[0])
            text = doc.get("content_text")
            if text is None:
                path = doc.get("storage_path")
                text = (ROOT / path.removeprefix("/data/")).read_text() if path else None
            if not text:
                raise ValueError("body missing " + str(run))
            texts[name] = text
        row = research.execute(
            "SELECT policy_set_json FROM codex_document3_policy_sets "
            "WHERE ticker=? AND policy_set_version=?",
            (c["ticker"], pin["policy_set_version"]),
        ).fetchone()
        if not row:
            raise ValueError("policy missing")
        texts["policy_set"] = load(row[0])
        event_root = ROOT / (pin.get("event_library_root") or "/data/events").removeprefix("/data/")
        # Reader uses an explicitly read-only EventLibraryRepository and performs no migration.
        from doxagent.event_library.provider import PublishedEventLibraryReader

        reader = PublishedEventLibraryReader(event_root)
        ref = reader.reference_view(c["ticker"], version=pin["event_library_version"])
        texts["reference_view"] = ref.reference_view if ref else ""
        texts["known_event_index"] = reader.known_index(
            c["ticker"], version=pin["event_library_version"]
        ).known_event_index
        refs = list(dict.fromkeys((c.get("w1_final") or {}).get("reference_ids", [])))
        detail = reader.event_details(c["ticker"], refs, version=pin["event_library_version"])
        texts["events"] = detail.model_dump(mode="json") if detail else {}
        emit("research", research_key=key, ticker=c["ticker"], version_pin=pin, **texts)
