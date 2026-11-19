"""On-demand global directory. Bounded existing facts, no writes or history reads."""

import json
import math
from collections import defaultdict
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

from fastapi import Request

from doxagent.message_bus_v2.collection_plane.observability import (
    states_from_heads,
    store_from_environment,
)
from doxagent.message_bus_v2.collection_plane.source_rounds import recent_rounds_many
from doxagent.semantic_clock import semantic_day
from doxagent.v2_control.bindings import embedded_secret
from doxagent.v2_read.calendar import PageCalendar
from doxagent.v2_read.repository import instant

from .article_samples import select_article_facts_many
from .bus import source_status
from .dto import available, missing


@lru_cache(maxsize=1)
def classification_catalog():
    path = Path(__file__).parents[1] / "message_bus_v2" / "source_catalog.json"
    return json.loads(path.read_text(encoding="utf-8"))


def entry_points(source, bindings, metadata):
    """Only public entry fields, never credentials, schemas or browser addresses."""
    result = {}

    def add(url, ticker=None):
        if not isinstance(url, str):
            return
        parsed = urlsplit(url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or embedded_secret(url, {})
            or any(
                k.lower() in {"token", "key", "signature", "sig"}
                for k, _ in parse_qsl(parsed.query)
            )
        ):
            return
        if ticker:
            url = url.replace("{ticker}", ticker).replace("{ticker_lower}", ticker.lower())
        result.setdefault((url, ticker), {"url": url, "label": parsed.hostname, "ticker": ticker})

    def parameters(params, ticker=None):
        for key in ("listing_url", "entry_url", "rss_url"):
            add(params.get(key), ticker)
        for url in params.get("rss_urls", []):
            add(url, ticker)
        for endpoint in params.get("endpoints", []):
            add(
                endpoint.get("url") or endpoint.get("entry_url") or endpoint.get("listing_url"),
                endpoint.get("ticker", ticker),
            )
        for target, url in params.get("ticker_pages", {}).items():
            if ticker is None or target == ticker:
                add(url, target)

    for url in metadata.get("entry_points", []):
        add(url)
    add(source.get("entry_url"))
    parameters(source.get("default_parameters", {}))
    for binding in bindings:
        parameters(binding.get("source_parameters", {}), binding["ticker"])
    return list(result.values())


def state_bucket(status):
    state = status["countdown_state"]
    if state == "DISABLED":
        return "disabled"
    if state in {"CLOSED_SLEEP", "WINDOW_CLOSED", "PAUSED"}:
        return "sleeping"
    if status["poll_status"] in {"partial", "failed"}:
        return "abnormal"
    return "running" if state == "SCHEDULED" else "unknown"


def aggregate_metrics(facts, statuses, rounds, today, *, body_applicable=True):
    # One article per source is the global business unit, regardless of ticker fanout.
    unique = {}
    for row in facts:
        key = row["article_key"]
        prior = unique.get(key)
        if prior is None or (row.get("completed_at") and (not prior.get("completed_at") or row["completed_at"] < prior["completed_at"])):
            unique[key] = row
    facts = [{**r, "e2e":r.get("global_e2e",r["e2e"]),"capture":r.get("global_capture",r["capture"]),
              "eligible":r["eligible"] and r.get("global_fresh", True)} for r in unique.values()]
    from .article_samples import reduce_articles
    detail = reduce_articles(facts)
    times = sorted(detail["latencies"])
    capture = detail["capture"]
    # BODY and global latency both count the shared article once.
    bodies = {r["article_key"]: r for r in facts if r["eligible"]}
    total, full = len(bodies), sum(r["full"] for r in bodies.values())
    gaps = sum(r.get("samples", 0) for r in rounds)
    gap_seconds = sum((r.get("refresh_seconds") or 0) * r.get("samples", 0) for r in rounds)
    successes = [
        datetime.fromisoformat(s["last_success_at"]["value"])
        for s in statuses
        if s["last_success_at"]["state"] == "AVAILABLE"
    ]
    return {
        "last_success_at": available(instant(max(successes))) if successes else missing(),
        "published_today": available(today) if today is not None else missing(),
        "mean_e2e_seconds": sum(times) / len(times) if times else None,
        "p95_e2e_seconds": times[math.ceil(0.95 * len(times)) - 1] if times else None,
        "e2e_samples": len(times),
        "mean_capture_seconds": sum(capture) / len(capture) if capture else None,
        "capture_samples": len(capture),
        "refresh_seconds": gap_seconds / gaps if gaps else None,
        "refresh_samples": gaps,
        "body_total": total,
        "body_full": full,
        "body_ratio": full / total if total else None,
        "body_applicable": body_applicable,
        "article_samples": detail["article_samples"],
        "window_hours": 24,
        "first_body_samples":len(detail["first_body_seconds"]),
        "mean_first_body_seconds":sum(detail["first_body_seconds"])/len(detail["first_body_seconds"]) if detail["first_body_seconds"] else None,
        "last_historical":detail["last_historical"],
        "last_historical_body":detail["last_historical_body"],
        "last_historical_first_body":detail["last_historical_first_body"],
        "publication_classes":{k:{"samples":len(v),"mean_seconds":sum(v)/len(v) if v else None} for k,v in detail["publication_classes"].items()},
        "poll_expected":sum(r.get("expected",0) for r in rounds),
        "poll_succeeded":sum(r.get("succeeded",0) for r in rounds),
    }


def status_clock(calendar, now, journal=None):
    """Status needs today's session fact, not bounds or previous trading dates."""
    if not isinstance(calendar, PageCalendar):
        return calendar.clock(now)
    market = calendar.calendar
    key = f"{market.name}:{semantic_day(now).isoformat()}"
    if journal is not None:
        # Reuse Runtime's facts in the existing calendar cache, with no SQL writes.
        for namespace in ("calendar_overrides", "calendar"):
            if market.journal.get(namespace, key) is None:
                fact = journal.get(namespace, key)
                if fact is not None:
                    market.journal.set(namespace, key, fact)
    return {"session": missing(), "is_trading_day": available(market.is_session(semantic_day(now)))}


def catalog_data(store, calendar, now=None, *, journal=None):
    now = now or datetime.now(UTC)
    day = semantic_day(now).isoformat()
    static = classification_catalog()
    with store.connect() as db:
        seq = store.highwater(db)
        definitions = {
            r[0]: json.loads(r[1])
            for r in db.execute(
                "SELECT id,payload FROM object_current INDEXED BY current_identity "
                "WHERE kind='native:source_definitions' AND ticker='' ORDER BY id"
            )
        }
        bindings = [
            json.loads(r[0])
            for r in db.execute(
                "SELECT payload FROM object_current INDEXED BY current_identity "
                "WHERE kind='native:ticker_source_bindings' "
                "AND json_extract(payload,'$.tombstoned_at') IS NULL"
            )
        ]
        tickers = {
            r[0]: json.loads(r[1])
            for r in db.execute(
                "SELECT ticker,payload FROM object_current INDEXED BY current_identity "
                "WHERE kind='ticker'"
            )
        }
        # Legacy heads are small and batched. A missing ledger row never triggers per-card get().
        polls = {
            r[0]: json.loads(r[1])
            for r in db.execute(
                "SELECT id,payload FROM object_current INDEXED BY current_identity "
                "WHERE kind='native:poll_states' AND ticker IN (SELECT value FROM json_each(?))",
                (json.dumps(sorted({b["ticker"] for b in bindings})),),
            )
        }
        facts = select_article_facts_many(
            store, [(b["ticker"], b["source_id"]) for b in bindings], now, db=db, global_only=True
        )
        covered = db.execute(
            "SELECT value FROM read_meta WHERE key='bus_aggregates_from'"
        ).fetchone()
        today = (
            None
            if not covered or seq < int(covered[0])
            else {
                r[0]: int(r[1])
                for r in db.execute(
                    "SELECT source_id,sum(CAST(value AS INTEGER)) FROM metric_buckets_current "
                    "WHERE metric='bus_messages' AND day=? "
                    "AND ticker IN (SELECT value FROM json_each(?)) GROUP BY source_id",
                    (day, json.dumps(sorted(tickers))),
                )
            }
        )
    acq = store_from_environment()
    rounds = {}
    if acq is not None and bindings:
        with acq.connect() as db:
            units = db.execute(
                "SELECT binding_id,unit,required,head_json,"
                "json_extract(state,'$.target_due_at') epoch,"
                "json_extract(state,'$.schedule_interval_seconds') period "
                "FROM units WHERE binding_id IN (SELECT value FROM json_each(?))",
                (json.dumps([b["binding_id"] for b in bindings]),),
            ).fetchall()
            conf = db.execute("SELECT value FROM meta WHERE key='configuration'").fetchone()
            polls.update(
                states_from_heads(
                    [u for u in units if u["required"] != 0],
                    json.loads(conf[0]).get("at") if conf else None,
                )
            )
            round_bindings, shared = [], set()
            for binding in sorted(bindings, key=lambda b:not (b["enabled"] and b["polling"]["enabled"])):
                sid = binding["source_id"]
                if definitions.get(sid,{}).get("acquisition_mode") in {"by_distribution","massive_distribution"}:
                    if sid in shared:
                        continue
                    shared.add(sid)
                round_bindings.append(binding)
            rounds = recent_rounds_many(acq, round_bindings, now, units=units, db=db)
    by_source = defaultdict(list)
    for binding in bindings:
        by_source[binding["source_id"]].append(binding)
        polls.setdefault(binding["binding_id"], {})
    view = {"as_of": instant(now), "wire": {"clock": status_clock(calendar, now, journal)}}
    result = []
    for sid, source in definitions.items():
        subscribed = sorted(by_source[sid], key=lambda b: b["ticker"])
        statuses = [
            source_status(
                store,
                b,
                seq,
                view,
                live_states=polls,
                source=source,
                ticker_state=tickers.get(b["ticker"], {}),
            )
            for b in subscribed
        ]
        counts = dict(running=0, sleeping=0, disabled=0, abnormal=0, unknown=0)
        poll_counts = dict(succeeded=0, partial=0, failed=0, unknown=0)
        intervals = set()
        for status in statuses:
            counts[state_bucket(status)] += 1
        for binding, status in zip(subscribed, statuses, strict=True):
            if binding["enabled"] and binding["polling"]["enabled"] and source["enabled"]:
                intervals.add(binding["polling"]["target_interval_seconds"])
                key = status["poll_status"]
                poll_counts[key if key in poll_counts else "unknown"] += 1
        metadata = static["sources"].get(sid, {})
        classification = (
            {k: metadata[k] for k in ["listing", "body", "applicability", "sector_ids"]}
            if metadata
            else None
        )
        source_facts = [r for b in subscribed for r in facts[(b["ticker"], sid)]]
        metric_subscriptions = [b for b in subscribed if b["binding_id"] in rounds] if source.get("acquisition_mode") in {"by_distribution","massive_distribution"} else subscribed
        default_interval = source.get("default_polling_config", {}).get("target_interval_seconds")
        result.append(
            {
                "source_id": sid,
                "name": source["display_name"],
                "description": metadata.get("description", ""),
                "short_name": metadata.get("short_name"),
                "poll_counts": poll_counts,
                "configured_intervals_seconds": sorted(intervals)
                or ([default_interval] if default_interval is not None else []),
                "enabled": source["enabled"],
                "delivery_mode": source.get("delivery_mode", "production"),
                "acquisition_mode": source.get("acquisition_mode", "by_ticker"),
                "classification": classification,
                "entry_points": entry_points(source, subscribed, metadata),
                "subscriptions": [
                    {
                        "ticker": b["ticker"],
                        "binding_id": b["binding_id"],
                        "enabled": b["enabled"] and b["polling"]["enabled"] and source["enabled"],
                    }
                    for b in subscribed
                ],
                "state_counts": counts,
                "errors": [
                    {
                        "ticker": b["ticker"],
                        "code": s["current_error"]["code"],
                        "message": s["current_error"]["message"],
                    }
                    for b, s in zip(subscribed, statuses, strict=True)
                    if s["current_error"]
                ],
                "metrics": aggregate_metrics(
                    source_facts,
                    statuses,
                    [rounds.get(b["binding_id"], {}) for b in metric_subscriptions],
                    today.get(sid, 0) if today is not None else None,
                    body_applicable=source.get("content_enrichment_mode") != "skip",
                ),
            }
        )
    return {
        "snapshot_at": instant(now),
        "semantic_day": day,
        "sectors": static["sectors"],
        "sources": result,
    }


def install(app):
    @app.get("/api/doxagent/v2/message-bus/source-catalog")
    async def source_catalog(request: Request):
        app.state.query(request, set())
        return app.state.respond(
            request,
            "SourceCatalog",
            catalog_data(
                app.state.store, app.state.views.calendar, journal=app.state.control.journal
            ),
        )
