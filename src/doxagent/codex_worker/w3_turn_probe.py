"""Opt-in, bounded W3 turn receipts; no model calls or retained tool/reasoning bodies.

SDK clocks measure item intervals. Native rollout timestamps supply response usage
and code-mode calls. Neither source exposes provider reasoning/prefill durations.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sqlite3
import time
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .turn_usage import KEYS, _usage, rollout_path

LOG = logging.getLogger(__name__)
VERSION = "w3-turn-observation-v1"
MAX_NATIVE_BYTES = 32 * 1024 * 1024
MAX_ROWS = 1000


def _text(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _time(value: str | None) -> float | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() if value else None
    except (ValueError, AttributeError):
        return None


def classify(name: str, arguments: str) -> dict[str, Any]:
    """Explicit surface evidence, with overlapping labels; never parse generated JS."""
    text = (name + " " + arguments).lower()
    labels = []
    evidence = []
    rules = (
        ("CONTROL_CONTRACT", r"agents\.md|skill\.md|task\.json|output_schema", "control_path"),
        (
            "RESEARCH_NAVIGATION",
            r"research_index|index\.json|\bfind\b|\bls\b|\bpwd\b|w3_research[_\.]+(?:catalog|search)",
            "navigation",
        ),
        ("D1_RESEARCH", r"document1|/d1[/_.]|\bd1\b|d1_section|company_profile", "d1_path_or_term"),
        (
            "D2_EXPECTATIONS",
            r"document2|/d2[/_.]|\bd2\b|d2_unit|expectation|shells?/|units?/",
            "d2_path_or_term",
        ),
        ("D3_POLICY", r"document3|/d3[/_.]|\bd3\b|polic(?:y|ies)", "policy_path_or_term"),
        (
            "EVENT_HISTORY",
            r"reference_view|reference_event|historical_message|trade_intent|runtime_state|reference\.md|event_library|recent_trade|watch|pin_candidates",
            "history_term",
        ),
        (
            "MARKET_PRICE",
            r"ibkr|historical_bars|market_history|market_view|candles|k.line",
            "market_tool_or_term",
        ),
        (
            "CAPABILITY_DISCOVERY",
            r"all_tools|catalog|ibkrtwssession|tools/providers|tools\.keys",
            "capability_term",
        ),
        (
            "FIRST_SOURCE_SEARCH",
            r"earliest|original source|first (?:report|publish)|最早|原发|首发",
            "first_source_term",
        ),
        (
            "SOURCE_VERIFICATION",
            r"investor.relations|press.release|\bsec\.gov\b|\bir\b|核实|确认|verify",
            "verification_term",
        ),
        (
            "OUTPUT_WORK",
            r"apply_patch|decision\.json|result\.json|operations\.json",
            "output_path_or_tool",
        ),
    )
    for label, pattern, reason in rules:
        if label == "CAPABILITY_DISCOVERY" and re.search(r"w3_research[_\.]+(?:catalog|search)", text) and not re.search(r"all_tools|ibkrtwssession|tools/providers|tools\.keys", text):
            continue
        if re.search(pattern, text):
            labels.append(label)
            evidence.append(reason)
    is_web = bool(re.search(r"web(?:__|\.|_)?(?:run|search)|search_query|image_query", text))
    if is_web:
        labels.append("WEB_RESEARCH")
        evidence.append("web_tool")
    if not labels:
        labels = ["OTHER_TOOL"]
    # A generic Web search is not evidence of source verification or original-source intent.
    return {
        "business_labels": labels,
        "classification_evidence": evidence,
        "classification_quality": "surface_heuristic",
    }


def union_ms(intervals: list[tuple[float, float]]) -> float:
    total = 0.0
    end = None
    for left, right in sorted(intervals):
        if right < left:
            continue
        total += max(0, right - max(left, end if end is not None else left))
        end = max(end if end is not None else right, right)
    return round(total, 3)


def project_native(records: list[dict], thread_id: str, turn_id: str) -> dict:
    """Only the selected turn is supplied; keep every response up to an explicit cap."""
    calls: dict[str, dict] = {}
    responses: dict[str, dict] = {}
    compactions = []
    origin = next(
        (
            _time(r.get("timestamp"))
            for r in records
            if r.get("payload", {}).get("type") == "task_started"
        ),
        None,
    )
    signature_count: dict[str, int] = {}
    cells: dict[str, str] = {}
    declared_intent = []
    last_response_input = None
    for record in records:
        payload = record.get("payload", {})
        if not isinstance(payload, dict):
            continue
        kind = payload.get("type")
        stamp = record.get("timestamp")
        ts = _time(stamp)
        if record.get("type") == "token_usage_record":
            if payload.get("thread_id") != thread_id or payload.get("turn_id") != turn_id:
                continue
            key = payload.get("response_id")
            usage = _usage(payload.get("usage"))
            if not key or usage is None:
                continue
            if key in responses:
                if responses[key]["usage"] != usage:
                    responses[key]["conflicting_usage"] = True
                continue
            if len(responses) >= MAX_ROWS:
                continue
            returned = [
                c
                for c in calls.values()
                if c.get("end_epoch") is not None
                and ts is not None
                and c["end_epoch"] <= ts
                and (not responses or c["end_epoch"] > list(responses.values())[-1]["epoch"])
            ]
            previous = list(responses.values())[-1]["epoch"] if responses else origin
            issued = [
                c
                for c in calls.values()
                if c.get("start_epoch") is not None
                and ts is not None
                and c["start_epoch"] <= ts
                and (previous is None or c["start_epoch"] > previous)
            ]
            round_intervals = [
                (max(previous, c["start_epoch"]) * 1000, min(ts, c["end_epoch"]) * 1000)
                for c in calls.values()
                if previous is not None
                and ts is not None
                and c.get("start_epoch") is not None
                and c.get("end_epoch") is not None
                and c["end_epoch"] > previous
                and c["start_epoch"] < ts
            ]
            last_output = max((c["end_epoch"] for c in returned), default=previous)
            responses[key] = {
                "response_id": key,
                "sequence": len(responses),
                "at_utc": stamp,
                "epoch": ts,
                "usage": usage,
                "input_growth_tokens": None
                if last_response_input is None
                else usage["input_tokens"] - last_response_input,
                "uncached_input_tokens": usage["input_tokens"] - usage["cached_input_tokens"],
                "issued_tool_call_ids": [c["call_id"] for c in issued],
                "action_business_labels": sorted(
                    {label for c in issued for label in c["business_labels"]}
                ),
                "observed_round_tool_ms": union_ms(round_intervals),
                "observed_round_residual_ms": None
                if previous is None or ts is None
                else round(max(0, (ts - previous) * 1000 - union_ms(round_intervals)), 3),
                "preceding_tool_call_ids": [c["call_id"] for c in returned],
                "preceding_tool_output_bytes": sum(c.get("output_bytes", 0) for c in returned),
                "after_business_labels": sorted(
                    {label for c in returned for label in c["business_labels"]}
                ),
                "since_previous_usage_ms": None
                if previous is None or ts is None
                else round(max(0, ts - previous) * 1000, 3),
                "after_last_tool_return_ms": None
                if last_output is None or ts is None
                else round(max(0, ts - last_output) * 1000, 3),
                "measurement_quality": "usage_end_anchor_not_provider_latency",
            }
            last_response_input = usage["input_tokens"]
        elif record.get("type") == "response_item" and kind in {
            "function_call",
            "custom_tool_call",
            "web_search_call",
        }:
            key = payload.get("call_id") or payload.get("id")
            if not key or key in calls or len(calls) >= MAX_ROWS:
                continue
            name = str(payload.get("name") or kind)[:120]
            args = _text(payload.get("arguments", payload.get("input", payload.get("action", {}))))
            signature = _hash(name + "\n" + args)
            signature_count[signature] = signature_count.get(signature, 0) + 1
            nested = sorted(set(re.findall(r"tools\.([A-Za-z0-9_]+)\s*\(", args)))[:32]
            resources = sorted(
                set(
                    re.findall(
                        r"(?:d[123]/|originals/document[123])"
                        r"[^\s\"'`,;)}\\]+",
                        args,
                    )
                )
            )[:32]
            calls[key] = {
                "call_id": key,
                "name": name,
                "nested_tool_names": nested,
                "research_resources": [r[:256] for r in resources],
                "source_hosts": sorted(set(re.findall(r"https?://([A-Za-z0-9.-]+)", args)))[:32],
                "start_utc": stamp,
                "start_epoch": ts,
                "end_epoch": None,
                "input_bytes": len(args.encode("utf-8")),
                "input_sha256": signature,
                "exact_repeat_ordinal": signature_count[signature],
                **classify(name, args),
                "measurement_quality": "native_outer_call_interval",
                "declared_intent_labels": declared_intent,
            }
            match = re.search(r'cell_id["\s:]+([A-Za-z0-9_-]+)', args)
            if match and match[1] in cells:
                parent = cells[match[1]]
                calls[key]["continuation_of_call_id"] = parent
                calls[key]["business_labels"] = calls[parent]["business_labels"]
                calls[key]["classification_evidence"] = ["continuation_cell"]
            if kind == "web_search_call":
                # Native web items often contain only completion, not an observable start.
                calls[key]["measurement_quality"] = "completion_only"
                calls[key]["start_epoch"] = None
                calls[key]["end_epoch"] = ts
        elif record.get("type") == "response_item" and kind in {
            "function_call_output",
            "custom_tool_call_output",
        }:
            key = payload.get("call_id")
            output = _text(payload.get("output", ""))
            if key not in calls:
                if not key or len(calls) >= MAX_ROWS:
                    continue
                calls[key] = {
                    "call_id": key,
                    "name": "unknown",
                    "start_epoch": None,
                    "business_labels": ["OTHER_TOOL"],
                    "measurement_quality": "output_only",
                }
            call = calls[key]
            lower_output = output.lower()
            call.update(
                end_utc=stamp,
                end_epoch=ts,
                output_bytes=len(output.encode("utf-8")),
                output_sha256=_hash(output),
                output_truncated_marker="truncated" in output[:500].lower(),
                failure_markers=[
                    marker
                    for marker in (
                        "ibkr_gateway_unavailable",
                        "nextvalidid",
                        "timed out",
                        "timeout",
                        "no such file",
                        "syntaxerror",
                        "traceback",
                        "permission denied",
                        "rate_limit",
                        "tool_not_allowed",
                    )
                    if marker in lower_output
                ],
            )
            cell = re.search(r"cell ID\s+([A-Za-z0-9_-]+)", output)
            if cell:
                cells[cell[1]] = key
            call["duration_ms"] = (
                round(max(0, ts - call["start_epoch"]) * 1000, 3)
                if ts is not None and call.get("start_epoch") is not None
                else None
            )
        elif (
            record.get("type") == "response_item"
            and kind == "message"
            and (payload.get("role") == "assistant" and payload.get("phase") == "commentary")
        ):
            content = _text(payload.get("content", []))
            declared_intent = classify("", content)["business_labels"]
        elif record.get("type") == "compacted" or (kind and "compact" in kind.lower()):
            compactions.append(
                {
                    "at_utc": stamp,
                    "event": kind or "compacted",
                    "measurement_quality": "completion_only",
                }
            )
    intervals = [
        (c["start_epoch"] * 1000, c["end_epoch"] * 1000)
        for c in calls.values()
        if c.get("start_epoch") is not None and c.get("end_epoch") is not None
    ]
    groups = {}
    for call in calls.values():
        for label in call["business_labels"]:
            g = groups.setdefault(label, {"calls": 0, "output_bytes": 0, "intervals": []})
            g["calls"] += 1
            g["output_bytes"] += call.get("output_bytes", 0)
            if call.get("start_epoch") is not None and call.get("end_epoch") is not None:
                g["intervals"].append((call["start_epoch"] * 1000, call["end_epoch"] * 1000))
    for g in groups.values():
        g["observed_tool_union_ms"] = union_ms(g.pop("intervals"))
    response_rows = list(responses.values())
    for r in response_rows:
        r.pop("epoch")
    call_rows = list(calls.values())
    for c in call_rows:
        c.pop("start_epoch", None)
        c.pop("end_epoch", None)
    return {
        "tools": call_rows,
        "responses": response_rows,
        "compactions": compactions,
        "business_groups": groups,
        "tool_union_ms": union_ms(intervals),
        "response_count": len(response_rows),
        "tool_count": len(call_rows),
        "row_cap_reached": len(calls) >= MAX_ROWS or len(responses) >= MAX_ROWS,
        "total_output_bytes": sum(c.get("output_bytes", 0) for c in call_rows),
        "repeat_candidates": [
            c["call_id"] for c in call_rows if c.get("exact_repeat_ordinal", 1) > 1
        ],
        "usage_total": {k: sum(r["usage"][k] for r in response_rows) for k in KEYS},
    }


def read_native(path: Path, turn_id: str) -> tuple[list[dict], dict]:
    records = []
    read = 0
    found_start = False
    with path.open("rb") as stream:
        size = stream.seek(0, 2)
        offset = max(0, size - MAX_NATIVE_BYTES)
        stream.seek(offset)
        tail = stream.read(MAX_NATIVE_BYTES)
    # Bound even an unusually huge single tool output line; skip the partial first line.
    lines = tail.splitlines()
    if offset and lines:
        lines.pop(0)
    invalid_lines = 0
    for line in reversed(lines):
        read += len(line)
        if read > MAX_NATIVE_BYTES:
            break
        try:
            row = json.loads(line)
        except (ValueError, UnicodeError):
            invalid_lines += 1
            continue
        if not isinstance(row, dict) or not isinstance(row.get("payload"), dict):
            continue
        payload = row["payload"]
        if row.get("type") == "event_msg" and payload.get("type") == "task_started":
            if payload.get("turn_id") == turn_id:
                records.append(row)
                found_start = True
                break
            # A trailing later turn is not part of this attempt.
        records.append(row)
    records.reverse()
    active = False
    selected = []
    for row in records:
        payload = row["payload"]
        if row.get("type") == "turn_context" or payload.get("type") == "task_started":
            active = payload.get("turn_id") == turn_id
        if active or (
            row.get("type") == "token_usage_record" and payload.get("turn_id") == turn_id
        ):
            selected.append(row)
    return selected, {
        "turn_start_found": found_start,
        "native_bytes_scanned": read,
        "native_scan_truncated": not found_start and offset > 0,
        "invalid_native_lines": invalid_lines,
    }


def control_root(workspace: Path) -> Path:
    return workspace / "w3-observation"


def read_control(root: Path) -> dict:
    try:
        config = json.loads((root / "control.json").read_text(encoding="utf-8"))
        if not isinstance(config, dict):
            return {}
        deadline = _time(config.get("until_utc"))
        if not config.get("enabled") or deadline is None or deadline <= time.time():
            return {}
        return config
    except (OSError, ValueError, TypeError):
        return {}


def prune(root: Path, config: dict) -> None:
    files = []
    for path in (root / "receipts").glob("*.json"):
        try:
            files.append((path.stat().st_mtime, path, path.stat().st_size))
        except FileNotFoundError:
            continue
    files.sort()
    cutoff = time.time() - min(14, max(1, config.get("retention_days", 14))) * 86400
    budget = min(256, max(1, config.get("max_megabytes", 128))) * 1024 * 1024
    count = min(2000, max(1, config.get("max_samples", 500)))
    total = sum(size for _, _, size in files)
    for index, (mtime, path, size) in enumerate(files):
        if mtime < cutoff or len(files) - index > count or total > budget:
            path.unlink(missing_ok=True)
            total -= size
        else:
            break


def maintenance(workspace: Path) -> None:
    root = control_root(workspace)
    if (root / "control.json").is_file():
        try:
            prune(root, json.loads((root / "control.json").read_text(encoding="utf-8")))
        except Exception as exc:
            LOG.warning("W3 observation retention unavailable: %s", type(exc).__name__)


class TurnProbe:
    def __init__(self, root: Path, identity: dict):
        self.root = root
        self.identity = identity
        self.start = time.perf_counter_ns()
        self.start_utc = datetime.now(UTC).isoformat()
        self.items: dict[str, dict] = {}
        self.usage_events: list[dict] = []

    @classmethod
    def create(cls, cwd: Path, request: Any, thread_id: str, turn_id: str) -> TurnProbe | None:
        if request is None or request.task_phase != "trade_decision":
            return None
        root = control_root(cwd.parent)
        if not read_control(root):
            return None
        identity = {
            "run_id": request.run_id,
            "attempt_id": request.attempt_id,
            "thread_id": thread_id,
            "turn_id": turn_id,
            "phase": request.task_phase,
            "model": request.model,
            "effort": request.effort,
            "release_revision": os.environ.get("DOXAGENT_BUILD_COMMIT", "unknown"),
        }
        try:
            task = json.loads(
                (cwd / "attempts" / request.attempt_id / "task.json").read_text(encoding="utf-8")
            )
            source = task.get("input", {})
            for key in ("case_id", "ticker", "case_type", "additional_tags"):
                if key in source:
                    identity[key] = source[key]
            binding = cwd / "attempts" / request.attempt_id / "audit" / "w3_identity.json"
            if binding.is_file():
                identity.update(json.loads(binding.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
        probe = cls(root, identity)
        target = root / "receipts" / (_hash(turn_id) + ".json")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(
                {
                    "schema_version": VERSION,
                    **identity,
                    "status": "observing_partial",
                    "started_at": probe.start_utc,
                    "coverage": {"turn_start_found": False},
                }
            ),
            encoding="utf-8",
        )
        prune(root, read_control(root))
        return probe

    def provider_calls(self) -> list[dict]:
        path = (
            self.root.parent
            / ".control"
            / self.identity["run_id"]
            / self.identity["attempt_id"]
            / "observations.sqlite3"
        )
        if not path.is_file():
            return []
        with closing(
            sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.05)
        ) as db:
            rows = db.execute("SELECT record_json FROM calls LIMIT ?", (MAX_ROWS,)).fetchall()
        keys = (
            "tool_call_id",
            "tool_name",
            "provider",
            "execution_status",
            "availability",
            "raw_payload_bytes",
            "inline_bytes",
            "pack_bytes",
            "latency_ms",
            "created_at",
        )
        return [
            {k: value.get(k) for k in keys}
            for (raw,) in rows
            if isinstance(value := json.loads(raw), dict)
        ]

    def observe(self, payload: Any) -> None:
        # No synchronous disk I/O in the event loop; scalar metadata only.
        if getattr(payload, "turn_id", None) != self.identity["turn_id"]:
            return
        kind = type(payload).__name__
        offset = round((time.perf_counter_ns() - self.start) / 1e6, 3)
        item = getattr(getattr(payload, "item", None), "root", None)
        if item is not None and kind in {"ItemStartedNotification", "ItemCompletedNotification"}:
            key = str(getattr(item, "id", "unknown"))
            if key not in self.items and len(self.items) >= MAX_ROWS:
                return
            row = self.items.setdefault(
                key, {"item_id": key, "item_type": getattr(item, "type", "unknown")}
            )
            row["start_ms" if kind == "ItemStartedNotification" else "end_ms"] = offset
            row["status"] = str(getattr(getattr(item, "status", None), "value", "observed"))
        elif kind == "ThreadTokenUsageUpdatedNotification" and len(self.usage_events) < MAX_ROWS:
            last = getattr(getattr(payload, "token_usage", None), "last", None)
            self.usage_events.append(
                {
                    "offset_ms": offset,
                    "usage": {k: getattr(last, k, None) for k in KEYS},
                    "quality": "sdk_notification_not_response_identity",
                }
            )

    def finish(self, home: Path | None, status: str) -> dict | None:
        began = time.perf_counter()
        try:
            config = read_control(self.root)
            if not config:
                return None
            native = {}
            coverage = {"turn_start_found": False, "native_rollout_available": False}
            if home is not None:
                path = rollout_path(home, self.identity["thread_id"])
                if path is not None and path.is_file():
                    records, flags = read_native(path, self.identity["turn_id"])
                    coverage.update(flags, native_rollout_available=True)
                    native = project_native(
                        records, self.identity["thread_id"], self.identity["turn_id"]
                    )
            duration = round((time.perf_counter_ns() - self.start) / 1e6, 3)
            receipt = {
                "schema_version": VERSION,
                **self.identity,
                "started_at": self.start_utc,
                "status": status,
                "elapsed_ms": duration,
                "sdk_items": list(self.items.values()),
                "sdk_usage_notifications": self.usage_events,
                "coverage": coverage,
                **native,
                "unattributed_ms": max(0, duration - native.get("tool_union_ms", 0)),
                "notes": [
                    "business labels overlap; do not sum group times",
                    "residual includes model/service/transport; not pure reasoning",
                    "after-tool response gap is an anchor proxy, not provider duration",
                    "repeat signatures are investigation candidates, not proven waste",
                ],
            }
            try:
                receipt["provider_calls"] = self.provider_calls()
            except (OSError, ValueError, sqlite3.Error):
                receipt["coverage"]["provider_calls_unavailable"] = True
            receipt["projection_ms"] = round((time.perf_counter() - began) * 1000, 3)
            target = self.root / "receipts" / (_hash(self.identity["turn_id"]) + ".json")
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_suffix(f".{os.getpid()}.tmp")
            tmp.write_text(
                json.dumps(receipt, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
            )
            tmp.replace(target)
            prune(self.root, config)
            return {
                "schema_version": VERSION,
                "receipt": str(target.relative_to(self.root.parent)),
                "response_count": native.get("response_count"),
                "tool_count": native.get("tool_count"),
                "projection_ms": receipt["projection_ms"],
                "coverage": coverage,
            }
        except Exception as exc:
            LOG.warning("W3 turn observation unavailable: %s", type(exc).__name__)
            return {"schema_version": VERSION, "degraded": type(exc).__name__}


def report(root: Path) -> str:
    rows = []
    for path in (root / "receipts").glob("*.json"):
        try:
            rows.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    rows.sort(key=lambda r: r.get("started_at", ""))
    lines = [
        "# W3 SDK 交易 turn 自然流量观测",
        "",
        "业务分类来自工具参数和公开 commentary 的表面证据，不包含隐藏推理。",
        "分类可重叠，时间不可跨组相加；response 间隔包含模型、服务、传输等混合时间。",
        "",
        "| Case / turn | 类型 | 状态 | response 数 | 工具批次 | 输出 KB | 观测秒 |",
        "|---|---|---|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row.get('case_id', row['turn_id'])} | {row.get('case_type', '?')} | "
            f"{row['status']} | {row.get('response_count', '?')} | "
            f"{row.get('tool_count', '?')} | {row.get('total_output_bytes', 0) / 1024:.1f} | "
            f"{row.get('elapsed_ms', 0) / 1000:.2f} |"
        )
    for row in rows:
        lines.extend(
            [
                "",
                f"## {row.get('case_id', row['turn_id'])}",
                "",
                f"Turn: `{row['turn_id']}`；覆盖：`{json.dumps(row.get('coverage', {}))}`",
                "",
                "| 业务组 | 批次 | 工具区间并集 ms | 输出 KB |",
                "|---|---:|---:|---:|",
            ]
        )
        for label, group in row.get("business_groups", {}).items():
            lines.append(
                f"| {label} | {group['calls']} | {group['observed_tool_union_ms']} | "
                f"{group['output_bytes'] / 1024:.1f} |"
            )
        lines.extend(
            [
                "",
                "| 工具批次 | 动作及业务标签 | 资源 / 来源域名 | 返回 KB | 区间 ms | 线索 |",
                "|---|---|---|---:|---:|---|",
            ]
        )
        for call in row.get("tools", []):
            names = call.get("nested_tool_names") or [call["name"]]
            labels = call["business_labels"] + call.get("declared_intent_labels", [])
            resources = call.get("research_resources", []) + call.get("source_hosts", [])
            lines.append(
                f"| {call['call_id']} | {', '.join(names)} / "
                f"{', '.join(sorted(set(labels)))} | {', '.join(resources)} | "
                f"{call.get('output_bytes', 0) / 1024:.1f} | {call.get('duration_ms')} | "
                f"repeat={call.get('exact_repeat_ordinal', 1)}, "
                f"{', '.join(call.get('failure_markers', []))} |"
            )
        lines.extend(
            [
                "",
                "| 轮 | 输入 token | 增量 | 缓存 token | 前序返回 KB | "
                "usage 间隔 ms | 工具外剩余 ms | 返回后间隔 ms | 本轮动作 / 前序业务 |",
                "|---:|---:|---:|---:|---:|---:|---:|---:|---|",
            ]
        )
        for response in row.get("responses", []):
            usage = response["usage"]
            labels = response["action_business_labels"] or response["after_business_labels"]
            lines.append(
                f"| {response['sequence'] + 1} | {usage['input_tokens']} | "
                f"{response['input_growth_tokens']} | {usage['cached_input_tokens']} | "
                f"{response['preceding_tool_output_bytes'] / 1024:.1f} | "
                f"{response['since_previous_usage_ms']} | "
                f"{response['observed_round_residual_ms']} | "
                f"{response['after_last_tool_return_ms']} | {', '.join(labels)} |"
            )
        lines.extend(
            [
                "",
                "完全重复调用候选：`"
                + ", ".join(row.get("repeat_candidates", []))
                + "`；需结合业务复核其必要性。",
            ]
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["enable", "disable", "status", "prune", "report"])
    parser.add_argument("--workspace-root", type=Path, default=Path("/data/workspaces"))
    parser.add_argument("--hours", type=float, default=24)
    parser.add_argument("--max-samples", type=int, default=500)
    args = parser.parse_args()
    root = control_root(args.workspace_root)
    if args.action == "report":
        print(report(root))
        return
    root.mkdir(parents=True, exist_ok=True)
    path = root / "control.json"
    if args.action in {"enable", "disable"}:
        if not 0 < args.hours <= 168 or not 1 <= args.max_samples <= 2000:
            parser.error("hours must be (0,168], max-samples [1,2000]")
        config = {
            "enabled": args.action == "enable",
            "retention_days": 14,
            "max_megabytes": 128,
            "max_samples": args.max_samples,
            "until_utc": (datetime.now(UTC) + timedelta(hours=args.hours)).isoformat(),
        }
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(config), encoding="utf-8")
        tmp.replace(path)
    config = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if args.action == "prune":
        prune(root, config)
    files = list((root / "receipts").glob("*.json"))
    print(
        json.dumps(
            {
                "control": config,
                "active": bool(read_control(root)),
                "receipt_count": len(files),
                "bytes": sum(p.stat().st_size for p in files),
            }
        )
    )


if __name__ == "__main__":
    main()
