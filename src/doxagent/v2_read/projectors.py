"""Explicit native-to-wire adapters. Native snapshots never escape through HTTP."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from doxagent.api_v2.dto import available, missing, validate
from doxagent.semantic_clock import semantic_day

from .projector import native
from .repository import ReadStore, instant
from .ordering import message_anchor


def timestamp(value: str) -> str:
    return instant(datetime.fromisoformat(value))


def state_wire(state: dict[str, Any]) -> dict[str, Any]:
    running, removed = state["analysis_allowed"], state["removed"]
    initializing = state.get("initialization_incomplete", False)

    def permission(allowed: bool, reason: str) -> dict[str, Any]:
        return {"allowed": allowed, "reason": None if allowed else reason}

    value = {
        "ticker": state["ticker"],
        "requested_mode": state["requested_mode"],
        "effective_mode": available(state["mode"])
        if state["activation_id"]
        else missing("NO_ACTIVE_REVISION", "UNAVAILABLE"),
        "run_state": state["status"],
        "health": "UNKNOWN",
        "health_reasons": ["NOT_RECORDED"],
        "initialization_id": state["initialization_id"],
        "initialization_incomplete": state.get(
            "initialization_incomplete",
            bool(state["initialization_id"] and not state["activation_id"]),
        ),
        "removed": removed,
        "removed_at": timestamp(state["removed_at"]) if state["removed_at"] else None,
        "control_etag": '"' + str(state["revision"]) + '"',
        "control_revision": state["revision"],
        "work_epoch": state["epoch"],
        "cutoff_at": timestamp(state["cutoff_at"]) if state["cutoff_at"] else None,
        "mode_effective_at": timestamp(state["mode_effective_at"])
        if state.get("mode_effective_at")
        else None,
        "existing_trades_continue": True,
        "actions": {
            "pause": permission(
                running and not removed and not initializing,
                "INITIALIZATION_IN_PROGRESS" if initializing else "NOT_RUNNING",
            ),
            "restart": permission(
                not running and not removed and not initializing and bool(state["activation_id"]),
                "INITIALIZATION_IN_PROGRESS" if initializing else "ALREADY_RUNNING" if running else "NO_ACTIVE_REVISION",
            ),
            "remove": permission(not removed, "TICKER_REMOVED"),
            "retry_initialization": permission(
                bool(state.get("initialization_failed")) and not removed, "NO_FAILED_INITIALIZATION"
            ),
        },
    }
    return validate("TickerState", value)


class DomainProjectors:
    def __init__(self, store: ReadStore) -> None:
        self.store = store

    def source_label(self, source: dict[str, Any]) -> dict[str, Any]:
        definition = self.store.get("native:source_definitions", "", source["source_id"])
        if not definition:
            raise ValueError("source definition must be indexed before messages")
        return {
            "source_id": source["source_id"],
            "binding_id": source["binding_id"],
            "name": definition["display_name"],
            "kind": definition["kind"],
        }

    def __call__(self, event: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        table, row, value = event["table_name"], event["row"], native(event)
        ticker = value.get("ticker", row.get("ticker", ""))
        initialization_run = None
        if table.startswith("initialization_"):
            if table == "initialization_runs":
                initialization_run = value
            else:
                with self.store.connect() as db:
                    found = db.execute(
                        "SELECT payload FROM objects WHERE kind='native:initialization_runs' "
                        "AND id=? AND valid_to IS NULL",
                        (row["run_id"],),
                    ).fetchone()
                if not found:
                    raise ValueError("initialization run must be indexed first")
                initialization_run = json.loads(found[0])
            ticker = initialization_run["ticker"]
        if table == "runtime_v2_cases":
            ticker = value["source"]["snapshot"]["ticker"]
        related_case = None
        if table == "runtime_v2_turns":
            with self.store.connect() as db:
                found = db.execute(
                    "SELECT ticker,payload FROM objects WHERE kind='native:runtime_v2_cases' "
                    "AND id=? AND valid_to IS NULL",
                    (value["case_id"],),
                ).fetchone()
            if not found:
                raise ValueError("turn Case must be indexed first")
            ticker, related_case = found[0], json.loads(found[1])
        identity = next(
            (
                value[key]
                for key in (
                    "case_id",
                    "standard_message_id",
                    "source_id",
                    "binding_id",
                    "initialization_id",
                    "revision_id",
                )
                if key in value
            ),
            event["entity_id"],
        )
        # Keep a turn's primary identity separate from its Case foreign key.
        if table == "v2_ticker_control":
            identity = ticker
        if table == "runtime_values":
            identity = row["namespace"] + ":" + row["key"]
        if table in {"ticker_source_bindings", "poll_states"}:
            identity = value["binding_id"]
        if table == "ticker_monitoring_states":
            identity = ticker
        if table == "v2_control_ack":
            identity = f"{row['epoch']}:{row['consumer']}"
        if table == "runtime_tasks":
            identity = value["id"]
            value = {
                **value,
                "inputs": json.loads(value["inputs"]),
                "receipt": json.loads(value["receipt"]),
            }
        if table == "stream_items":
            identity = value["stream_item_id"]
        if table in {"runtime_v2_turns", "runtime_v2_effects"}:
            identity = value["turn_id"] if table.endswith("turns") else value["effect_id"]
        if table in {
            "runtime_v2_candidates",
            "runtime_v2_archives",
            "runtime_v2_badcases",
            "runtime_v2_policy_activations",
        }:
            identity = event["entity_id"]
        record = {
            "kind": "native:" + table,
            "ticker": ticker,
            "id": identity,
            "data": None if event["operation"] == "DELETE" else value,
            "parent": value.get("case_id"),
            "source_id": value.get("source_id"),
        }
        records, metrics = [record], []
        if table == "v2_analysis_admission":
            records.append(
                {
                    "kind": "business_provenance",
                    "ticker": row["ticker"],
                    "id": row["identity"],
                    "data": {
                        "basis": "V2_CONTROL_ADMISSION",
                        "origin_epoch": value["origin_epoch"],
                        "origin_mode": value["origin_mode"],
                    },
                }
            )
        if table.startswith("te_"):
            from .executions import ExecutionProjector

            executed, counts = ExecutionProjector(self.store).project(event, record)
            records.extend(executed)
            metrics.extend(counts)
            from .pnl import project as project_pnl

            realized, counts = project_pnl(self.store, event, records)
            records.extend(realized)
            metrics.extend(counts)
        if (
            table == "runtime_v2_candidates"
            and value.get("case_id")
            and value.get("originating_node")
        ):
            candidate = validate(
                "Candidate",
                {
                    **value["candidate"],
                    "candidate_id": identity,
                    "dedupe_key": value["runtime_signature"],
                    "originating_node": value["originating_node"],
                    "provisional_event_id": value["provisional_event_id"],
                    "created_at": timestamp(value["created_at"]),
                },
            )
            records.append(
                {
                    "kind": "candidate",
                    "ticker": ticker,
                    "id": identity,
                    "parent": value["case_id"],
                    "sort": candidate["created_at"],
                    "data": candidate,
                }
            )
            metrics.append(
                {
                    "metric": "new_fact_candidates",
                    "ticker": ticker,
                    "entity": identity,
                    "day": value["trading_date"],
                    "value": "1",
                }
            )
        result_kind = {
            "runtime_v2_candidates": "EVENT_DISCOVERY",
            "runtime_v2_archives": "ARCHIVE",
            "runtime_v2_badcases": "BADCASE",
        }.get(table)
        if result_kind and value.get("case_id"):
            summary = self.store.get("case", ticker, value["case_id"])
            if not summary:
                raise ValueError("result Case must be indexed first")
            summary["results"] = sorted(set(summary["results"]) | {result_kind})
            records.append(
                {
                    "kind": "case",
                    "ticker": ticker,
                    "id": summary["case_id"],
                    "data": summary,
                    "sort": summary["received_at"],
                    "day": summary["semantic_day"],
                    "parent": summary["stream_item_id"],
                    "source_id": summary["source"]["source_id"],
                    "route": summary["resolved_route"] or summary["initial_route"],
                }
            )
        if related_case:
            from .runtime import turn_records

            turns, usage = turn_records(value, related_case)
            records.extend(turns)
            metrics.extend(usage)
        if initialization_run:
            if table == "initialization_runs":
                record["id"] = initialization_run["initialization_id"]
            else:
                record["parent"] = row["run_id"]
                record["id"] = event["entity_id"]
                if table == "initialization_events":
                    record["data"] = {**row, "payload": value}
            from .initialization import progress

            records.append(progress(self.store, initialization_run, record))
        if table == "source_definitions":
            record.update(ticker="", id=value["source_id"])
        elif table == "runtime_v2_policy_activations":
            key = value["policy_id"] + ":" + value["activation_revision"]
            record["id"] = key
            if not self.store.get("business_provenance", ticker, value["case_id"]):
                raise ValueError("PROVENANCE_UNVERIFIED")
            records.append(
                {"kind": "policy_consumption", "ticker": ticker, "id": key, "data": value}
            )
            prior = self.store.get("policy_catalog", ticker, key)
            if prior:
                prior.update(
                    consumed=available(True),
                    consumed_at=available(timestamp(value["activated_at"])),
                    effective=available(False),
                )
                records.append(
                    {"kind": "policy_catalog", "ticker": ticker, "id": key, "data": prior}
                )
        elif table == "v2_ticker_control":
            wire = state_wire(value)
            records.append({"kind": "ticker", "ticker": ticker, "id": ticker, "data": wire})
            records.append(
                {
                    "kind": "navigation",
                    "ticker": "",
                    "id": ticker,
                    "data": {
                        key: wire[key]
                        for key in (
                            "ticker",
                            "run_state",
                            "health",
                            "initialization_incomplete",
                            "removed",
                        )
                    }
                    if not wire["removed"] and not wire["initialization_incomplete"]
                    else None,
                }
            )
        elif table == "runtime_v2_cases":
            if not self.store.get("business_provenance", ticker, value["case_id"]):
                raise ValueError("PROVENANCE_UNVERIFIED")
            records.extend(self.case(value, event["seq"]))
            summary = records[1]["data"]
            final = summary["final_novelty"].get("value")
            hit = summary["final_policy_hit"].get("value")
            counts = {
                "processed_cases": True,
                "new_cases": final == "NEW",
                "old_cases": final == "OLD",
                "hit_cases": hit is True,
                "policy_decided_cases": hit is not None,
                "w3_cases": summary["initial_route"] == "W3",
            }
            metrics.extend(
                {
                    "metric": key,
                    "ticker": ticker,
                    "entity": summary["case_id"],
                    "day": summary["semantic_day"],
                    "value": "1" if count else "0",
                }
                for key, count in counts.items()
            )
            from .runtime import hot_path_contributions, w3_contributions

            metrics.extend(hot_path_contributions(self.store, value))
            metrics.extend(w3_contributions(self.store, value))
            from .policy_hits import project as policy_hits

            hits, counts = policy_hits(self.store, value, summary, event)
            records.extend(hits)
            metrics.extend(counts)
        elif table == "runtime_values" and row["namespace"] == "worker_invocations":
            if not value.get("case_id") and value.get("control_epoch") is None:
                raise ValueError("PROVENANCE_UNVERIFIED")
            if value.get("case_id") and not self.store.get(
                "business_provenance", ticker, value["case_id"]
            ):
                raise ValueError("PROVENANCE_UNVERIFIED")
            from .runtime import worker_records

            attempts, counts = worker_records(value)
            records.extend(attempts)
            metrics.extend(counts)
        elif table == "standard_messages":
            content = self.store.put_content(ticker, value["body"], "text/plain")
            record["data"] = {**value, "body_ref": content}
            records.append(
                {
                    "kind": "message_raw_link",
                    "ticker": ticker,
                    "id": value["raw_message_id"],
                    "parent": value["standard_message_id"],
                    "data": {"standard_message_id": value["standard_message_id"]},
                }
            )
        elif table == "audit_log" and value["entity_type"] == "body_completion":
            attempt = value["payload"]
            if attempt.get("v2_control_origin"):
                records.append(
                    {
                        "kind": "body_attempt",
                        "ticker": attempt["ticker"],
                        "id": attempt["attempt_id"],
                        "parent": attempt["raw_message_id"],
                        "source_id": attempt["source_id"],
                        "day": semantic_day(
                            datetime.fromisoformat(attempt["started_at"])
                        ).isoformat(),
                        "data": attempt,
                    }
                )
        elif table == "stream_members":
            records.extend(self.message(value, event["seq"]))
        elif table == "runtime_values" and row["namespace"] in {
            "trade_intents",
            "analysis_trade_decisions",
            "candidates",
        }:
            if not self.store.get("business_provenance", ticker, value["case_id"]):
                raise ValueError("PROVENANCE_UNVERIFIED")
            if row["namespace"] == "trade_intents" and value.get("released_at"):
                from .executions import pending_intent

                records.extend(pending_intent(self.store, ticker, value))
            prior = self.store.get("case", ticker, value["case_id"])
            if prior:
                disposition = value.get("trade_disposition") or (
                    "CANDIDATE"
                    if row["namespace"] == "candidates"
                    else value.get("status", "NOT_EVALUATED")
                )
                if disposition == "UNKNOWN":
                    disposition = "READY"
                prior = {**prior, "trade_disposition": disposition, "revision": event["seq"]}
                validate("CaseSummary", prior)
                records.append(
                    {
                        "kind": "case",
                        "ticker": ticker,
                        "id": prior["case_id"],
                        "data": prior,
                        "sort": prior["received_at"],
                        "day": prior["semantic_day"],
                        "parent": prior["stream_item_id"],
                        "source_id": prior["source"]["source_id"],
                        "route": prior["resolved_route"] or prior["initial_route"],
                    }
                )
            if value.get("released_at") and value.get("status") not in {
                "DUPLICATE_POLICY",
                "DUPLICATE_REALTIME_OUTPUT",
                "EXPIRED_SEMANTIC_DAY",
            }:
                metrics.append(
                    {
                        "metric": "trade_triggered",
                        "ticker": ticker,
                        "entity": value["intent_id"],
                        "day": semantic_day(
                            datetime.fromisoformat(value["released_at"])
                        ).isoformat(),
                        "value": "1",
                    }
                )
        # Route attribution can settle after publication; replace the same contribution.
        for message in records:
            if message["kind"] == "message" and message["data"]:
                metrics.append(
                    {
                        "metric": "messages",
                        "ticker": message["ticker"],
                        "entity": message["id"],
                        "day": message["day"],
                        "value": "1",
                        "dimensions": {
                            "source_id": message["source_id"],
                            "kind": message["data"]["source"]["kind"],
                            "route": message["data"]["case"]["route_group"],
                        },
                    }
                )
        changed_cases = {
            r["id"]: (r["ticker"], r["data"]) for r in records if r["kind"] == "case" and r["data"]
        }
        for item in records:
            if item["kind"] == "attempt" and item.get("parent") not in changed_cases:
                summary = self.store.get("case", ticker, item["parent"])
                if summary:
                    changed_cases[summary["case_id"]] = (ticker, summary)
        if changed_cases:
            from .graph import project

            for case_ticker, summary in changed_cases.values():
                members, counts = project(self.store, case_ticker, summary, records)
                records.extend(members)
                metrics.extend(counts)
        if table in {
            "v2_ticker_control",
            "v2_control_ack",
            "ticker_monitoring_states",
            "poll_states",
        }:
            from .health import project as health

            health(self.store, ticker, records)
        return records, metrics

    def case(self, value: dict[str, Any], revision: int) -> list[dict[str, Any]]:
        source, ticker = value["source"], value["source"]["snapshot"]["ticker"]
        w1, w2 = value.get("w1_final"), value.get("w2_final")
        initial = (value.get("route") or {}).get("primary_route")
        resolved = (value.get("resolved_route") or {}).get("primary_route")
        final = value.get("w3_result")
        if final:
            w1, w2 = final.get("novelty", w1), final.get("policy", w2)
        pending = initial == "W3" and not resolved
        old = self.store.get("case", ticker, value["case_id"])
        settled = value["status"] in {"COMPLETED", "FAILED", "UNAVAILABLE"}
        if settled:
            with self.store.connect() as db:
                pending_execution = db.execute(
                    "SELECT 1 FROM objects WHERE kind='execution' AND ticker=? AND parent=? "
                    "AND valid_to IS NULL AND json_extract(payload,'$.entry_result') IS NULL LIMIT 1",
                    (ticker, value["case_id"]),
                ).fetchone()
            settled = not pending_execution
        completed = value.get("completed_at")
        created = value["created_at"]
        summary = {
            "case_id": value["case_id"],
            "revision": revision,
            "semantic_day": value["trading_date"],
            "runtime_mode": value["runtime_mode"],
            "first_round_shape": (
                "PARALLEL"
                if value["runtime_mode"] == "REALTIME"
                else "W1_ONLY"
                if value["w2_skipped"]
                else "SEQUENTIAL"
            ),
            "status": value["status"],
            "technical_status": value["technical_status"],
            "title": available(source["snapshot"]["title"])
            if source["snapshot"].get("title")
            else missing(),
            "source": self.source_label(source),
            "received_at": timestamp(created),
            "completed_at": available(timestamp(completed)) if completed else missing(),
            "duration_seconds": available(
                (
                    datetime.fromisoformat(completed) - datetime.fromisoformat(created)
                ).total_seconds()
            )
            if completed
            else missing(),
            "initial_route": initial,
            "resolved_route": resolved,
            "w3_status": "RESOLVED" if final else "PENDING" if pending else None,
            "final_novelty": available(w1["result"]) if w1 and not pending else missing(),
            "final_policy_hit": available(bool(w2["policy_ids"]))
            if w2 and not pending
            else missing(),
            "results": [result for result in old["results"] if result != "FAILURE"] if old else [],
            "result_settled": settled,
            "trade_disposition": old["trade_disposition"] if old else "NOT_EVALUATED",
            "stream_item_id": source["stream_item_id"],
            "member_count": source["member_count"],
        }
        if value["status"] in {"FAILED", "UNAVAILABLE"}:
            summary["results"].append("FAILURE")
        validate("CaseSummary", summary)
        records = [
            {
                "kind": "case",
                "ticker": ticker,
                "id": summary["case_id"],
                "data": summary,
                "parent": source["stream_item_id"],
                "sort": summary["received_at"],
                "day": summary["semantic_day"],
                "source_id": source["source_id"],
                "route": resolved or initial,
            }
        ]
        reasons = {}
        for name, result in (("w1", value.get("w1_final")), ("w2", value.get("w2_final"))):
            if result and result.get("reason"):
                reasons[name] = self.store.put_content(ticker, result["reason"])
        for name in ("novelty", "policy", "expert_trade"):
            result = (value.get("w3_result") or {}).get(name)
            if result and result.get("reason"):
                reasons["w3_" + name] = self.store.put_content(ticker, result["reason"])
        records.append(
            {"kind": "case_reasoning", "ticker": ticker, "id": value["case_id"], "data": reasons}
        )
        for identity in source.get("member_message_ids") or [source["source_message_id"]]:
            message = self.store.get("message", ticker, identity)
            if message:
                standard = self.store.get("native:standard_messages", ticker, identity) or {}
                message["case"] = self.case_link(summary)
                message["row_revision"] = revision
                records.append(
                    {
                        "kind": "message",
                        "ticker": ticker,
                        "id": identity,
                        "data": message,
                        "parent": summary["case_id"],
                        "source_id": source["source_id"],
                        "route": message["case"]["route_group"],
                        "sort": message_anchor(message),
                        "search": (standard.get("title") or "") + "\n" + standard.get("body", ""),
                        "day": semantic_day(
                            datetime.fromisoformat(message["stream_published_at"])
                        ).isoformat(),
                    }
                )
        return records

    @staticmethod
    def case_link(summary: dict[str, Any] | None) -> dict[str, Any]:
        return {
            "case_id": summary["case_id"] if summary else None,
            "status": summary["status"] if summary else None,
            "initial_route": summary["initial_route"] if summary else None,
            "w3_status": summary["w3_status"] if summary else None,
            "resolved_route": summary["resolved_route"] if summary else None,
            "route_group": (
                "NOT_PROCESSED"
                if not summary
                else "FAILED"
                if summary["status"] in {"FAILED", "UNAVAILABLE"}
                else "W3_PENDING"
                if summary["w3_status"] == "PENDING"
                else summary["resolved_route"] or summary["initial_route"] or "NOT_PROCESSED"
            ),
        }

    def message(self, member: dict[str, Any], revision: int) -> list[dict[str, Any]]:
        # stream_members has no ticker; the stream identity is indexed on publication.
        with self.store.connect() as db:
            row = db.execute(
                "SELECT ticker,payload FROM objects WHERE kind='native:stream_items' "
                "AND id=? AND valid_to IS NULL",
                (member["stream_item_id"],),
            ).fetchone()
        if not row:
            raise ValueError("stream not indexed")
        ticker, stream = row[0], json.loads(row[1])
        message = self.store.get("native:standard_messages", ticker, member["standard_message_id"])
        if not message:
            raise ValueError("standard message not indexed")
        if not message.get("metadata", {}).get("v2_control_origin") and not self.store.get(
            "business_provenance", ticker, message["standard_message_id"]
        ):
            raise ValueError("PROVENANCE_UNVERIFIED")
        body_ref = message["body_ref"]
        with self.store.connect() as db:
            seq = self.store.highwater(db)
        cases = self.store.page("case", ticker, seq, parent=member["stream_item_id"], limit=1)
        case = cases[0]["data"] if cases else None
        summary = {key: message[key] for key in ("standard_message_id", "revision", "url")}
        summary.update(
            row_revision=revision,
            title=available(message["title"]) if message.get("title") else missing(),
            source=self.source_label(message),
            source_published_at=timestamp(message["published_at"]),
            collected_at=timestamp(message["collected_at"]),
            normalized_at=timestamp(message["normalized_at"]),
            stream_published_at=timestamp(stream["published_at"]),
            stream_item_id=member["stream_item_id"],
            stream_offset=stream["stream_offset"],
            member_index=member["member_index"],
            exact_duplicate_count=missing(),
            case=self.case_link(case),
            body=body_ref,
        )
        validate("MessageSummary", summary)
        return [
            {
                "kind": "message",
                "ticker": ticker,
                "id": message["standard_message_id"],
                "data": summary,
                "sort": message_anchor(summary),
                "source_id": message["source_id"],
                "route": summary["case"]["route_group"],
                "parent": case["case_id"] if case else None,
                "search": (message.get("title") or "") + "\n" + message["body"],
                "day": semantic_day(datetime.fromisoformat(stream["published_at"])).isoformat(),
            }
        ]
