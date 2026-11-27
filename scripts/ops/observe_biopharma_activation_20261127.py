"""One bounded read-only production snapshot; no scheduler or persistent watcher."""

import argparse
import json
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

from doxagent.message_bus_v2.collection_plane.store import path_for
from doxagent.message_bus_v2.monitoring_terms import MonitoringTermsService
from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.settings import DoxAgentSettings

TICKERS = ("CGEM", "RGNX", "PRAX", "XENE", "SMMT")
NEW = {
    "fda_release",
    "endpoints_biopharma",
    "fiercebiotech_biotech",
    "stat_biotech_trials",
    "hkex_akeso",
    "akeso_press_releases",
    "taiho_press_releases",
    "ash_cullinan",
    "aes_abstract_search",
    "corporate_ir_press_releases",
}


def dt(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def seconds(a, b):
    return (dt(b) - dt(a)).total_seconds() if a and b else None


def stats(values):
    values = sorted(x for x in values if x is not None)
    return (
        {
            "n": len(values),
            "mean": round(sum(values) / len(values), 3),
            "p95": round(values[min(len(values) - 1, int(len(values) * 0.95))], 3),
            "max": round(values[-1], 3),
        }
        if values
        else {"n": 0}
    )


def redact(value):
    if isinstance(value, str):
        return re.sub(
            r"([?&](?:token|api_key|apikey|key)=)[^&\s'\"<>]*", r"\1[REDACTED]", value, flags=re.I
        )
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items()}
    return value


def connect(path):
    db = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=1)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    # Bound accidental historical scans, leaving short indexed reads unaffected.
    deadline = __import__("time").monotonic() + 15
    db.set_progress_handler(lambda: __import__("time").monotonic() > deadline, 10000)
    return db


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--since", required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    settings = DoxAgentSettings()
    now = datetime.now(UTC).isoformat()
    out = {
        "at": now,
        "since": args.since,
        "elapsed_seconds": seconds(args.since, now),
        "tickers": {},
    }
    repo = MessageBusV2Repository(settings.message_bus_v2_sqlite_path, initialize=False)
    db = connect(path_for(settings.message_bus_v2_sqlite_path))
    for ticker in TICKERS:
        sources = defaultdict(
            lambda: {
                "statuses": Counter(),
                "reasons": Counter(),
                "errors": [],
                "dispatch": [],
                "list": [],
                "gaps": [],
                "units": set(),
            }
        )
        previous = {}
        for row in db.execute(
            "SELECT * FROM cycles WHERE ticker=? AND dispatch_at>=? AND status!='SCHEDULE' ORDER BY dispatch_at",
            (ticker, args.since),
        ):
            r = sources[row["source_id"]]
            r["statuses"][row["status"]] += 1
            if row["reason"]:
                r["reasons"][row["reason"]] += 1
            d = json.loads(row["data_json"])
            error = d.get("terminal_error_message")
            if error and error not in r["errors"] and len(r["errors"]) < 3:
                r["errors"].append(error)
            r["dispatch"].append(seconds(row["due_at"], row["dispatch_at"]))
            if d.get("list_validated"):
                received = d.get("listing_received_at")
                r["list"].append(seconds(row["dispatch_at"], received))
                key = (row["source_id"], row["unit"])
                if received and previous.get(key):
                    r["gaps"].append(seconds(previous[key], received))
                if received:
                    previous[key] = received
            r["units"].add(row["unit"])
        terms = MonitoringTermsService(repo).get(ticker)
        bindings = repo.list_bindings(ticker=ticker)
        out["tickers"][ticker] = {
            "terms_revision": terms[0] if terms else 0,
            "state": repo.get_ticker_state(ticker).status.value,
            "bindings": len(bindings),
            "disabled_bindings": [
                b.source_id for b in bindings if not b.enabled or not b.polling.enabled
            ],
            "sources": {
                sid: {
                    "new_source": sid in NEW,
                    "statuses": dict(r["statuses"]),
                    "reasons": dict(r["reasons"]),
                    "errors": r["errors"],
                    "units": sorted(r["units"]),
                    "dispatch_wait_seconds": stats(r["dispatch"]),
                    "dispatch_to_list_seconds": stats(r["list"]),
                    "successful_list_gap_seconds": stats(r["gaps"]),
                }
                for sid, r in sources.items()
            },
        }
    stream = json.loads(
        (db.execute("SELECT value FROM meta WHERE key='x_official_stream'").fetchone() or ["{}"])[0]
    )
    out["x_stream"] = {
        k: v
        for k, v in stream.items()
        if k
        in {
            "state",
            "accounts",
            "at",
            "connected_at",
            "last_post_at",
            "received",
            "error",
            "http_status",
        }
    }
    out["captures"] = [
        dict(r)
        for r in db.execute(
            "SELECT source_id,status,count(*) AS n FROM captures WHERE observed_at>=? GROUP BY source_id,status",
            (args.since,),
        )
    ]
    db.close()
    db = connect(settings.message_bus_v2_sqlite_path)
    # first_seen has an index; delivery joins use their (article_key,ticker) primary key.
    out["new_deliveries"] = [
        dict(r)
        for r in db.execute(
            "SELECT d.ticker,d.source_id,d.state,d.last_error,a.outcome,count(*) AS n FROM live_articles a "
            "JOIN live_deliveries d ON a.article_key=d.article_key WHERE a.first_seen_at>=? "
            "AND d.ticker IN (?,?,?,?,?) GROUP BY d.ticker,d.source_id,d.state,d.last_error,a.outcome",
            (args.since, *TICKERS),
        )
    ]
    out["new_article_samples"] = [
        dict(r)
        for r in db.execute(
            "SELECT a.title,a.url,a.source_id,a.first_seen_at,a.first_fetch_at,a.acquired_at,a.state,a.outcome,a.last_error,"
            "a.fetch_total,a.full_total,a.input_ref FROM live_articles a WHERE a.first_seen_at>=? "
            "AND EXISTS(SELECT 1 FROM live_deliveries d WHERE d.article_key=a.article_key AND d.ticker IN (?,?,?,?,?)) "
            "ORDER BY a.first_seen_at DESC LIMIT 40",
            (args.since, *TICKERS),
        )
    ]
    out["publications"] = []
    for ticker in TICKERS:
        # ticker/offset index, bounded recent stream history; no whole-Bus scan.
        for r in db.execute(
            "SELECT stream_item_id,published_at FROM stream_items WHERE ticker=? ORDER BY stream_offset DESC LIMIT 500",
            (ticker,),
        ):
            if dt(r["published_at"]) < dt(args.since):
                break
            for d in db.execute(
                "SELECT d.source_id,d.created_at,d.first_discovered_at,d.first_poll_due_at,d.consumable_at,"
                "a.first_seen_at,a.first_fetch_at,a.acquired_at,a.outcome,a.title,a.url,s.data_json "
                "FROM stream_members m JOIN standard_messages s ON s.standard_message_id=m.standard_message_id "
                "JOIN live_deliveries d ON d.article_key=json_extract(s.data_json,'$.metadata.live_article_key') AND d.ticker=? "
                "JOIN live_articles a ON a.article_key=d.article_key WHERE m.stream_item_id=?",
                (ticker, r["stream_item_id"]),
            ):
                item = dict(d)
                standard = json.loads(item.pop("data_json"))
                msg = standard.get("message", standard)
                published = msg.get("published_at")
                metadata = msg.get("metadata", {})
                basis = msg.get("publication_time_basis") or metadata.get("publication_time_basis")
                item.update(
                    ticker=ticker,
                    bus_at=r["published_at"],
                    official_published_at=published,
                    publication_time_basis=basis,
                    discovery_to_bus_seconds=seconds(
                        item["first_discovered_at"] or item["first_seen_at"], r["published_at"]
                    ),
                    publish_to_bus_seconds=seconds(published, r["published_at"])
                    if basis == "EXACT"
                    else None,
                    body_wait_seconds=seconds(item["first_seen_at"], item["first_fetch_at"]),
                    body_acquisition_seconds=seconds(item["first_fetch_at"], item["acquired_at"]),
                )
                out["publications"].append(item)
    db.close()
    out["windows"] = {
        s.source_id: s.activation_window.model_dump(mode="json")
        for s in repo.list_sources()
        if s.activation_window
    }
    repo.close()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(redact(out), ensure_ascii=False, indent=2))
    print(
        json.dumps(
            {
                "at": now,
                "elapsed_seconds": out["elapsed_seconds"],
                "publications": len(out["publications"]),
                "new_deliveries": out["new_deliveries"],
                "new_article_samples": len(out["new_article_samples"]),
            }
        )
    )


if __name__ == "__main__":
    main()
