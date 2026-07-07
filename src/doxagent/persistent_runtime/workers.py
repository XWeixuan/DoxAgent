"""Default W1/W2 worker implementations for Persistent Runtime Execution."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from doxagent.agents.runner import AgentRunner
from doxagent.models import (
    AgentName,
    AgentPermissions,
    AgentTask,
    ResultStatus,
    RunMetadata,
    TaskType,
    new_id,
)
from doxagent.monitoring.schema import SourceType
from doxagent.persistent_runtime.schema import (
    A2Result,
    O3Result,
    O3RuntimeBudget,
    RuntimeSourceMessage,
    W1Confidence,
    W1NoveltyLabel,
    W1Result,
    W2Result,
    W2Type,
)

JsonObject = dict[str, object]

if TYPE_CHECKING:
    from doxagent.settings import DoxAgentSettings

_RUNTIME_CLOCK_TZ_NAME = "America/New_York"
_RUNTIME_CLOCK_TZ = ZoneInfo(_RUNTIME_CLOCK_TZ_NAME)


class AgentRunnerW1Worker:
    """Run W1 through the standard prompt-backed AgentRunner contract."""

    def __init__(self, runner: AgentRunner) -> None:
        self.runner = runner

    def classify(self, message: RuntimeSourceMessage, context: JsonObject) -> W1Result:
        result = self.runner.run(
            _runtime_worker_task(
                agent_name=AgentName.W1_RUNTIME_NOVELTY,
                task_type=TaskType.RUNTIME_W1_NOVELTY,
                ticker=message.ticker,
                message=message,
                context=_w1_context(context),
                output_schema="W1Result",
            )
        )
        if result.status is not ResultStatus.SUCCEEDED:
            fallback = _fallback_w1_from_failed_result(result.error)
            if fallback is not None:
                return fallback
            message_text = result.error.message if result.error else "W1 runner failed."
            raise RuntimeError(message_text)
        return W1Result.model_validate(_w1_structured_payload(result.payload))


class LazyAgentRunnerW1Worker:
    """Create the production W1 AgentRunner only when W1 classification is reached."""

    def __init__(self, settings: DoxAgentSettings | None = None) -> None:
        self.settings = settings
        self._delegate: AgentRunnerW1Worker | None = None

    def classify(self, message: RuntimeSourceMessage, context: JsonObject) -> W1Result:
        if self._delegate is None:
            from doxagent.agents.runner import default_real_agent_runner

            self._delegate = AgentRunnerW1Worker(
                default_real_agent_runner(settings=self.settings)
            )
        return self._delegate.classify(message, context)


class AgentRunnerW2Worker:
    """Run W2 through the standard prompt-backed AgentRunner contract."""

    def __init__(self, runner: AgentRunner) -> None:
        self.runner = runner

    def classify(self, message: RuntimeSourceMessage, context: JsonObject) -> W2Result:
        result = self.runner.run(
            _runtime_worker_task(
                agent_name=AgentName.W2_RUNTIME_POLICY,
                task_type=TaskType.RUNTIME_W2_POLICY,
                ticker=message.ticker,
                message=message,
                context=_w2_context(context),
                output_schema="W2Result",
            )
        )
        if result.status is not ResultStatus.SUCCEEDED:
            fallback = _fallback_w2_from_failed_result(result.error)
            if fallback is not None:
                return fallback
            message_text = result.error.message if result.error else "W2 runner failed."
            raise RuntimeError(message_text)
        return W2Result.model_validate(_w2_structured_payload(result.payload, context))


class LazyAgentRunnerW2Worker:
    """Create the production W2 AgentRunner only when W2 classification is reached."""

    def __init__(self, settings: DoxAgentSettings | None = None) -> None:
        self.settings = settings
        self._delegate: AgentRunnerW2Worker | None = None

    def classify(self, message: RuntimeSourceMessage, context: JsonObject) -> W2Result:
        if self._delegate is None:
            from doxagent.agents.runner import default_real_agent_runner

            self._delegate = AgentRunnerW2Worker(
                default_real_agent_runner(settings=self.settings)
            )
        return self._delegate.classify(message, context)


class AgentRunnerO3Worker:
    """Run O3 through the standard prompt-backed AgentRunner contract."""

    def __init__(self, runner: AgentRunner) -> None:
        self.runner = runner

    def judge(
        self,
        message: RuntimeSourceMessage,
        context: JsonObject,
        budget: O3RuntimeBudget,
    ) -> O3Result:
        result = self.runner.run(
            _runtime_worker_task(
                agent_name=AgentName.O3_TRADING_STRATEGY,
                task_type=TaskType.RUNTIME_O3_JUDGMENT,
                ticker=message.ticker,
                message=message,
                context={
                    **dict(context),
                    "o3_runtime_budget": budget.model_dump(mode="json"),
                },
                output_schema="O3Result",
            )
        )
        if result.status is not ResultStatus.SUCCEEDED:
            message_text = result.error.message if result.error else "O3 runner failed."
            raise RuntimeError(message_text)
        return O3Result.model_validate(_structured_payload(result.payload))


class LazyAgentRunnerO3Worker:
    """Create the production O3 AgentRunner only when an O3 route is reached."""

    def __init__(self, settings: DoxAgentSettings | None = None) -> None:
        self.settings = settings
        self._delegate: AgentRunnerO3Worker | None = None

    def judge(
        self,
        message: RuntimeSourceMessage,
        context: JsonObject,
        budget: O3RuntimeBudget,
    ) -> O3Result:
        if self._delegate is None:
            from doxagent.agents.runner import default_real_agent_runner

            self._delegate = AgentRunnerO3Worker(
                default_real_agent_runner(settings=self.settings)
            )
        return self._delegate.judge(message, context, budget)


class AgentRunnerA2Worker:
    """Run runtime A2 verification through the standard AgentRunner contract."""

    def __init__(self, runner: AgentRunner) -> None:
        self.runner = runner

    def verify(self, message: RuntimeSourceMessage, context: JsonObject) -> A2Result:
        result = self.runner.run(
            _runtime_worker_task(
                agent_name=AgentName.A2_FACT_CHECK,
                task_type=TaskType.FACT_CHECK,
                ticker=message.ticker,
                message=message,
                context=context,
                output_schema="A2Result",
            )
        )
        if result.status is not ResultStatus.SUCCEEDED:
            message_text = result.error.message if result.error else "A2 runner failed."
            raise RuntimeError(message_text)
        return A2Result.model_validate(_structured_payload(result.payload))


class LazyAgentRunnerA2Worker:
    """Create the production A2 AgentRunner only when A2 verification is reached."""

    def __init__(self, settings: DoxAgentSettings | None = None) -> None:
        self.settings = settings
        self._delegate: AgentRunnerA2Worker | None = None

    def verify(self, message: RuntimeSourceMessage, context: JsonObject) -> A2Result:
        if self._delegate is None:
            from doxagent.agents.runner import default_real_agent_runner

            self._delegate = AgentRunnerA2Worker(
                default_real_agent_runner(settings=self.settings)
            )
        return self._delegate.verify(message, context)


class HeuristicW1Worker:
    """Deterministic fallback W1 for local dry-runs when no model runner is configured."""

    def classify(self, message: RuntimeSourceMessage, context: JsonObject) -> W1Result:
        text = _message_text(message)
        matched_ids: list[str] = []
        for event in _known_event_items(context):
            if _known_event_matches(text, event):
                event_id = str(event.get("event_id") or event.get("id") or "").strip()
                if event_id:
                    matched_ids.append(event_id)
        if matched_ids:
            return W1Result(
                is_new=False,
                novelty_label=W1NoveltyLabel.OLD_DUPLICATE,
                matched_known_event_ids=matched_ids,
                confidence=W1Confidence.HIGH,
                reasoning="Message matches Known Events duplicate detection keys.",
            )
        return W1Result(
            is_new=True,
            novelty_label=W1NoveltyLabel.NEW_EVENT,
            matched_known_event_ids=[],
            confidence=W1Confidence.MEDIUM,
            reasoning="No Known Events match was found in the provided context.",
        )


class HeuristicW2Worker:
    """Deterministic fallback W2 for local dry-runs when no model runner is configured."""

    def classify(self, message: RuntimeSourceMessage, context: JsonObject) -> W2Result:
        text = _message_text(message)
        for policy in _policy_items(context):
            if not _policy_matches(text, policy):
                continue
            policy_id = str(
                policy.get("policy_id") or policy.get("rule_id") or policy.get("id") or ""
            ).strip()
            policy_type = _policy_type(policy)
            if policy_id and policy_type == "direct_trade":
                return W2Result(
                    matched_policy_code=policy_id,
                    type=W2Type.DIRECT_TRADE_CANDIDATE,
                    reasoning="Message matches a direct_trade Monitoring Execution Policy.",
                )
            if policy_id and policy_type == "escalate":
                return W2Result(
                    matched_policy_code=policy_id,
                    type=W2Type.ESCALATE_TO_BACKGROUND_AGENT,
                    reasoning="Message matches an escalate Monitoring Execution Policy.",
                )
        if _is_relevant_without_policy(message, text):
            return W2Result(
                matched_policy_code=None,
                type=W2Type.NULL,
                reasoning="Message is relevant but no Monitoring Execution Policy matched.",
            )
        return W2Result(
            matched_policy_code=None,
            type=W2Type.IRRELEVANT,
            reasoning="Message is recall noise, low relevance, or low quality.",
        )


def _runtime_worker_task(
    *,
    agent_name: AgentName,
    task_type: TaskType,
    ticker: str,
    message: RuntimeSourceMessage,
    context: JsonObject,
    output_schema: str,
) -> AgentTask:
    runtime_context = dict(context)
    runtime_context["runtime_clock"] = _runtime_clock()
    return AgentTask(
        task_id=new_id("task"),
        ticker=ticker,
        agent_name=agent_name,
        task_type=task_type,
        input_context={
            "source_message": message.model_dump(mode="json"),
            "runtime_context": runtime_context,
        },
        required_output_schema=output_schema,
        permissions=AgentPermissions(),
        run_metadata=RunMetadata(
            run_id=new_id("run"),
            ticker=ticker,
            workflow_node="persistent_runtime_execution",
            created_at=datetime.now(UTC),
        ),
    )


def _runtime_clock() -> JsonObject:
    now_et = datetime.now(_RUNTIME_CLOCK_TZ)
    offset = now_et.strftime("%z")
    formatted_offset = f"{offset[:3]}:{offset[3:]}" if len(offset) == 5 else offset
    return {
        "now_et": now_et.isoformat(),
        "tz_abbrev": now_et.tzname() or "ET",
        "utc_offset": formatted_offset,
    }


def _structured_payload(payload: object) -> object:
    if not isinstance(payload, dict):
        return payload
    for key in ("structured", "structured_response", "final_payload"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            return nested
    return payload


def _w1_structured_payload(payload: object) -> object:
    structured = _structured_payload(payload)
    if not isinstance(structured, dict):
        return structured
    if _has_keys(structured, {"is_new", "novelty_label", "confidence", "reasoning"}):
        return structured
    nested = structured.get("W1Result") or structured.get("w1_result")
    if isinstance(nested, dict):
        return _w1_structured_payload(nested)
    if isinstance(structured.get("event_id"), str) and isinstance(
        structured.get("core_fact"), str
    ):
        event_id = str(structured["event_id"])
        return {
            "is_new": True,
            "novelty_label": W1NoveltyLabel.MATERIAL_UPDATE.value,
            "matched_known_event_ids": [event_id],
            "confidence": _coerce_confidence(structured.get("confidence")).value,
            "reasoning": _optional_str_value(structured.get("core_fact"))
            or "模型输出了 Known Events patch 形态，已保守归一化为 material_update。",
        }
    if isinstance(structured.get("event_id"), str):
        event_id = str(structured["event_id"])
        reasoning = _joined_text_fields(
            structured,
            (
                "reasoning",
                "reason",
                "rationale",
                "summary",
                "description",
                "status",
                "novelty_status",
                "classification",
                "assessment",
            ),
        )
        label = _coerce_w1_label(
            _optional_str_value(
                structured.get("novelty_label")
                or structured.get("novelty_status")
                or structured.get("classification")
                or structured.get("assessment")
            )
        ) or _coerce_w1_label_from_text(reasoning)
        return {
            "is_new": label
            in {W1NoveltyLabel.MATERIAL_UPDATE, W1NoveltyLabel.NEW_EVENT},
            "novelty_label": label.value,
            "matched_known_event_ids": [event_id] if event_id.strip() else [],
            "confidence": _coerce_confidence(structured.get("confidence")).value,
            "reasoning": reasoning
            or "W1 returned a Known Event id without the full schema; normalized conservatively.",
        }
    if structured.get("source_message_id") or structured.get("missing_body") is True:
        matched = structured.get("matched_known_event_ids") or []
        if not isinstance(matched, list):
            matched = []
        summary_label = (
            W1NoveltyLabel.KNOWN_EVENT_RECAP
            if matched and structured.get("is_new") is False
            else W1NoveltyLabel.NEW_EVENT
        )
        return {
            "is_new": summary_label
            in {W1NoveltyLabel.MATERIAL_UPDATE, W1NoveltyLabel.NEW_EVENT},
            "novelty_label": summary_label.value,
            "matched_known_event_ids": [str(item) for item in matched if str(item).strip()],
            "confidence": W1Confidence.LOW.value,
            "reasoning": _optional_str_value(
                structured.get("reasoning") or structured.get("summary")
            )
            or "W1 模型输出输入摘要形态，未明确命中 Known Event，已保守归一化。",
        }
    raw_is_new = (
        structured.get("is_new")
        if isinstance(structured.get("is_new"), bool)
        else structured.get("novelty_detected")
        if isinstance(structured.get("novelty_detected"), bool)
        else structured.get("is_novel")
        if isinstance(structured.get("is_novel"), bool)
        else None
    )
    if isinstance(raw_is_new, bool):
        matched = (
            structured.get("matched_known_event_ids")
            or structured.get("matched_events")
            or []
        )
        if not isinstance(matched, list):
            matched = []
        novelty_label = (
            W1NoveltyLabel.NEW_EVENT
            if raw_is_new
            else W1NoveltyLabel.KNOWN_EVENT_RECAP
        )
        return {
            "is_new": raw_is_new,
            "novelty_label": novelty_label.value,
            "matched_known_event_ids": [
                str(item.get("event_id") if isinstance(item, dict) else item)
                for item in matched
                if str(item).strip()
            ],
            "confidence": _coerce_confidence(structured.get("confidence")).value,
            "reasoning": _optional_str_value(
                structured.get("reasoning")
                or structured.get("novelty_reason")
                or structured.get("summary")
            )
            or "W1 模型输出 novelty flag 形态，已保守归一化。",
        }
    novelty = _optional_str_value(
        structured.get("novelty_label")
        or structured.get("novelty_assessment")
        or structured.get("novelty_status")
        or structured.get("novelty")
        or structured.get("assessment")
    )
    coerced_label = _coerce_w1_label(novelty)
    if coerced_label is None:
        raw_score = structured.get("novelty_score")
        if isinstance(raw_score, int | float):
            reasoning = _joined_text_fields(
                structured,
                (
                    "reasoning",
                    "reason",
                    "rationale",
                    "summary",
                    "description",
                ),
            )
            coerced_label = _coerce_w1_label_from_score(raw_score, reasoning)
        else:
            return structured
    matched = structured.get("matched_known_event_ids") or structured.get("matched_event_ids") or []
    if not isinstance(matched, list):
        matched = []
    return {
        "is_new": coerced_label
        in {W1NoveltyLabel.MATERIAL_UPDATE, W1NoveltyLabel.NEW_EVENT},
        "novelty_label": coerced_label.value,
        "matched_known_event_ids": [str(item) for item in matched if str(item).strip()],
        "confidence": _coerce_confidence(structured.get("confidence")).value,
        "reasoning": _optional_str_value(structured.get("reasoning"))
        or "模型输出已按 W1 schema 做保守归一化。",
    }


def _w2_structured_payload(payload: object, context: JsonObject) -> object:
    structured = _structured_payload(payload)
    if not isinstance(structured, dict):
        return structured
    if _has_keys(structured, {"matched_policy_code", "type", "reasoning"}):
        return structured
    nested = structured.get("W2Result") or structured.get("w2_result")
    if isinstance(nested, dict):
        if _has_keys(nested, {"matched_policy_code", "type", "reasoning"}):
            return nested
        coerced_nested = _coerce_w2_analysis_payload(nested, context)
        if coerced_nested is not None:
            return coerced_nested
    coerced = _coerce_w2_analysis_payload(structured, context)
    return coerced if coerced is not None else structured


def _coerce_w2_analysis_payload(
    payload: dict[str, object],
    context: JsonObject,
) -> dict[str, object] | None:
    assessment = payload.get("policy_trigger_assessment") or payload.get("triggers")
    if isinstance(assessment, dict):
        for policy_code, detail in assessment.items():
            if not isinstance(detail, dict) or detail.get("triggered") is not True:
                continue
            code = str(policy_code)
            w2_type = _w2_type_for_policy_code(code, context)
            if w2_type is None:
                continue
            return {
                "matched_policy_code": code,
                "type": w2_type.value,
                "reasoning": _optional_str_value(detail.get("reasoning"))
                or "模型触发评估命中具体 Monitoring Execution Policy。",
            }
        return {
            "matched_policy_code": None,
            "type": _coerce_w2_unmatched_type(payload).value,
            "reasoning": _optional_str_value(
                payload.get("recommendation")
                or payload.get("raw_reasoning")
                or payload.get("reasoning")
            )
            or "消息相关但未命中任何 Monitoring Execution Policy。",
        }
    evaluations = payload.get("policy_evaluations") or payload.get("triggered_policies")
    if isinstance(evaluations, list):
        for item in evaluations:
            if not isinstance(item, dict):
                continue
            triggered = item.get("triggered") is True or item.get("matched") is True
            if not triggered:
                continue
            policy_id = _optional_str_value(
                item.get("policy_id") or item.get("rule_id") or item.get("policy_code")
            )
            if policy_id is None:
                continue
            w2_type = _w2_type_for_policy_code(policy_id, context)
            if w2_type is None:
                continue
            return {
                "matched_policy_code": policy_id,
                "type": w2_type.value,
                "reasoning": _optional_str_value(item.get("reasoning"))
                or "模型 policy_evaluations 命中具体 Monitoring Execution Policy。",
            }
        return {
            "matched_policy_code": None,
            "type": _coerce_w2_unmatched_type(payload).value,
            "reasoning": _optional_str_value(
                payload.get("summary") or payload.get("reasoning") or payload.get("recommendation")
            )
            or "模型输出 policy_evaluations 但未命中任何 policy。",
        }
    if isinstance(payload.get("policy_triggered"), bool):
        if payload.get("policy_triggered") is True:
            raw_ids = payload.get("triggered_policy_ids") or payload.get("matched_policy_ids") or []
            if not isinstance(raw_ids, list):
                raw_ids = [raw_ids]
            for raw_id in raw_ids:
                policy_id = _optional_str_value(raw_id)
                if policy_id is None:
                    continue
                w2_type = _w2_type_for_policy_code(policy_id, context)
                if w2_type is None:
                    continue
                return {
                    "matched_policy_code": policy_id,
                    "type": w2_type.value,
                    "reasoning": _optional_str_value(payload.get("reasoning"))
                    or "W2 returned policy_triggered=true with a concrete policy id.",
                }
            return None
        return {
            "matched_policy_code": None,
            "type": _coerce_w2_unmatched_type(payload).value,
            "reasoning": _optional_str_value(
                payload.get("reasoning")
                or payload.get("summary")
                or payload.get("recommendation")
                or payload.get("rationale")
            )
            or "W2 returned policy_triggered=false; no Monitoring Execution Policy matched.",
        }
    single_policy_id = _optional_str_value(
        payload.get("policy_id") or payload.get("rule_id") or payload.get("policy_code")
    )
    if single_policy_id is not None and isinstance(payload.get("triggered"), bool):
        if payload.get("triggered") is True:
            w2_type = _w2_type_for_policy_code(single_policy_id, context)
            if w2_type is None:
                return None
            return {
                "matched_policy_code": single_policy_id,
                "type": w2_type.value,
                "reasoning": _optional_str_value(payload.get("reasoning"))
                or "模型单 policy 评估命中 Monitoring Execution Policy。",
            }
        return {
            "matched_policy_code": None,
            "type": _coerce_w2_unmatched_type(payload).value,
            "reasoning": _optional_str_value(payload.get("reasoning"))
            or "模型单 policy 评估未触发任何 Monitoring Execution Policy。",
        }
    raw_type = _optional_str_value(
        payload.get("type") or payload.get("recommendation") or payload.get("classification")
    )
    if raw_type:
        w2_type = _coerce_w2_type(raw_type)
        if w2_type is not None:
            policy_code = payload.get("matched_policy_code") or payload.get("policy_code")
            return {
                "matched_policy_code": str(policy_code)
                if w2_type
                in {
                    W2Type.DIRECT_TRADE_CANDIDATE,
                    W2Type.ESCALATE_TO_BACKGROUND_AGENT,
                }
                and policy_code
                else None,
                "type": w2_type.value,
                "reasoning": _optional_str_value(payload.get("reasoning"))
                or "模型输出已按 W2 schema 做保守归一化。",
            }
    if any(
        key in payload
        for key in (
            "ticker",
            "source_message_id",
            "policy_checks",
            "policy_matches",
            "policy_results",
            "policy_triggered",
            "triggered_policy_ids",
            "overall_assessment",
            "rationale",
            "recommendation",
        )
    ):
        return {
            "matched_policy_code": None,
            "type": _coerce_w2_unmatched_type(payload).value,
            "reasoning": _optional_str_value(
                payload.get("summary")
                or payload.get("reasoning")
                or payload.get("recommendation")
                or payload.get("rationale")
                or payload.get("overall_assessment")
            )
            or "W2 模型输出分析 wrapper，未识别到具体 policy 命中，已保守归一化。",
        }
    return None


def _w1_context(context: JsonObject) -> JsonObject:
    return _context_subset(
        context,
        (
            "ticker",
            "document_source_run_id",
            "document3_run_id",
            "known_events",
            "known_events_document",
        ),
    )


def _w2_context(context: JsonObject) -> JsonObject:
    return _context_subset(
        context,
        (
            "ticker",
            "document_source_run_id",
            "document3_run_id",
            "monitoring_policies",
            "monitoring_policy",
            "monitoring_config",
            "source_confidence_policy",
        ),
    )


def _context_subset(context: JsonObject, keys: tuple[str, ...]) -> JsonObject:
    return {key: context[key] for key in keys if key in context}


def _has_keys(payload: dict[str, object], keys: set[str]) -> bool:
    return keys <= set(payload)


def _optional_str_value(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _joined_text_fields(payload: dict[str, object], keys: tuple[str, ...]) -> str:
    parts: list[str] = []
    for key in keys:
        value = _optional_str_value(payload.get(key))
        if value is not None:
            parts.append(value)
    return " ".join(parts)


def _coerce_w1_label(value: str | None) -> W1NoveltyLabel | None:
    if value is None:
        return None
    normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
    if normalized in {"old_duplicate", "duplicate", "old", "already_known"}:
        return W1NoveltyLabel.OLD_DUPLICATE
    if normalized in {"known_event_recap", "recap", "summary", "review"}:
        return W1NoveltyLabel.KNOWN_EVENT_RECAP
    if normalized in {"material_update", "update", "incremental_update"}:
        return W1NoveltyLabel.MATERIAL_UPDATE
    if normalized in {"new_event", "novel", "new", "novel_event"}:
        return W1NoveltyLabel.NEW_EVENT
    return None


def _coerce_confidence(value: object) -> W1Confidence:
    text = str(value or "").strip().lower()
    if text == W1Confidence.HIGH.value:
        return W1Confidence.HIGH
    if text == W1Confidence.LOW.value:
        return W1Confidence.LOW
    return W1Confidence.MEDIUM


def _coerce_w2_type(value: str) -> W2Type | None:
    normalized = value.strip().lower().replace("_", " ")
    if "direct trade" in normalized or normalized in {"dtc", "direct"}:
        return W2Type.DIRECT_TRADE_CANDIDATE
    if "escalate" in normalized or "background agent" in normalized:
        return W2Type.ESCALATE_TO_BACKGROUND_AGENT
    if "irrelevant" in normalized or "not relevant" in normalized:
        return W2Type.IRRELEVANT
    if normalized == "null" or "no policy" in normalized or "not triggered" in normalized:
        return W2Type.NULL
    return None


def _coerce_w2_unmatched_type(payload: dict[str, object]) -> W2Type:
    text = " ".join(
        str(payload.get(key) or "")
        for key in ("recommendation", "raw_reasoning", "reasoning", "overall_risk_rating")
    ).lower()
    if any(token in text for token in ("irrelevant", "无关", "低相关", "噪音", "spam")):
        return W2Type.IRRELEVANT
    return W2Type.NULL


def _fallback_w1_from_failed_result(error: object) -> W1Result | None:
    details = getattr(error, "details", {}) if error is not None else {}
    code = getattr(error, "code", "") if error is not None else ""
    if _is_timeout_error(error):
        return W1Result(
            is_new=True,
            novelty_label=W1NoveltyLabel.NEW_EVENT,
            matched_known_event_ids=[],
            confidence=W1Confidence.LOW,
            reasoning="W1 模型请求超时，已按低置信度新事件保守归一化。",
        )
    if code != "model_gateway_error" or not isinstance(details, dict):
        return None
    gateway_error = details.get("gateway_error")
    if not isinstance(gateway_error, dict) or gateway_error.get("code") != "invalid_json":
        return None
    text = _optional_str_value(
        (gateway_error.get("details") or {}).get("text")
        if isinstance(gateway_error.get("details"), dict)
        else None
    ) or _optional_str_value(
        (gateway_error.get("details") or {}).get("text_preview")
        if isinstance(gateway_error.get("details"), dict)
        else None
    )
    label = _coerce_w1_label_from_text(text)
    return W1Result(
        is_new=label in {W1NoveltyLabel.MATERIAL_UPDATE, W1NoveltyLabel.NEW_EVENT},
        novelty_label=label,
        matched_known_event_ids=[],
        confidence=W1Confidence.LOW,
        reasoning="W1 模型返回非 JSON 文本，已按文本语义保守归一化。",
    )


def _fallback_w2_from_failed_result(error: object) -> W2Result | None:
    details = getattr(error, "details", {}) if error is not None else {}
    code = getattr(error, "code", "") if error is not None else ""
    if _is_timeout_error(error):
        return W2Result(
            matched_policy_code=None,
            type=W2Type.NULL,
            reasoning="W2 模型请求超时，已按相关但未命中 policy 的 NULL 保守归一化。",
        )
    if code != "model_gateway_error" or not isinstance(details, dict):
        return None
    gateway_error = details.get("gateway_error")
    if not isinstance(gateway_error, dict) or gateway_error.get("code") != "invalid_json":
        return None
    raw_details = gateway_error.get("details")
    text = ""
    if isinstance(raw_details, dict):
        text = str(raw_details.get("text") or raw_details.get("text_preview") or "")
    return W2Result(
        matched_policy_code=None,
        type=_coerce_w2_type_from_text(text),
        reasoning="W2 模型返回非 JSON 文本，已按文本语义保守归一化。",
    )


def _coerce_w1_label_from_text(text: str | None) -> W1NoveltyLabel:
    lowered = (text or "").lower()
    if any(
        token in lowered
        for token in (
            "duplicate",
            "old duplicate",
            "released earlier",
            "already released",
            "already reported",
            "already covered",
            "already known",
            "previously known",
            "not new",
            "not novel",
            "完全重复",
        )
    ):
        return W1NoveltyLabel.OLD_DUPLICATE
    if any(
        token in lowered
        for token in ("recap", "summary", "known event", "same event", "回顾", "复述")
    ):
        return W1NoveltyLabel.KNOWN_EVENT_RECAP
    if any(token in lowered for token in ("material update", "incremental", "更新", "补充")):
        return W1NoveltyLabel.MATERIAL_UPDATE
    return W1NoveltyLabel.NEW_EVENT


def _coerce_w1_label_from_score(
    score: int | float,
    reasoning: str | None,
) -> W1NoveltyLabel:
    lowered = (reasoning or "").lower()
    if any(token in lowered for token in ("very low", "low novelty", "not novel", "新颖性很低")):
        return W1NoveltyLabel.KNOWN_EVENT_RECAP
    if any(token in lowered for token in ("material update", "incremental", "补充", "更新")):
        return W1NoveltyLabel.MATERIAL_UPDATE
    if score <= 5:
        return W1NoveltyLabel.KNOWN_EVENT_RECAP
    if score >= 8:
        return W1NoveltyLabel.NEW_EVENT
    return W1NoveltyLabel.NEW_EVENT


def _coerce_w2_type_from_text(text: str) -> W2Type:
    lowered = text.lower()
    if any(token in lowered for token in ("irrelevant", "无关", "低相关", "噪音", "spam")):
        return W2Type.IRRELEVANT
    return W2Type.NULL


def _is_timeout_error(error: object) -> bool:
    if error is None:
        return False
    message = str(getattr(error, "message", "") or "").lower()
    if "timed out" in message or "timeout" in message or "超时" in message:
        return True
    details = getattr(error, "details", {})
    if not isinstance(details, dict):
        return False
    gateway_error = details.get("gateway_error")
    if not isinstance(gateway_error, dict):
        return False
    gateway_message = str(gateway_error.get("message") or "").lower()
    gateway_code = str(gateway_error.get("code") or "").lower()
    return (
        "timed out" in gateway_message
        or "timeout" in gateway_message
        or "timeout" in gateway_code
    )


def _w2_type_for_policy_code(policy_code: str, context: JsonObject) -> W2Type | None:
    for policy in _policy_items(context):
        candidate = str(
            policy.get("policy_id") or policy.get("rule_id") or policy.get("id") or ""
        ).strip()
        if candidate != policy_code:
            continue
        policy_type = _policy_type(policy)
        if policy_type == "direct_trade":
            return W2Type.DIRECT_TRADE_CANDIDATE
        if policy_type == "escalate":
            return W2Type.ESCALATE_TO_BACKGROUND_AGENT
    return None


def _message_text(message: RuntimeSourceMessage) -> str:
    parts = [
        message.ticker,
        message.title or "",
        message.body or "",
        message.author or "",
        message.username or "",
        " ".join(message.symbols),
        " ".join(message.keywords),
    ]
    return " ".join(parts).lower()


def _known_event_items(context: JsonObject) -> list[dict[str, Any]]:
    raw = context.get("known_events") or context.get("known_events_document") or []
    if isinstance(raw, dict):
        raw = raw.get("events") or raw.get("known_events") or raw.get("items") or []
    if not isinstance(raw, list):
        return []
    return [dict(item) for item in raw if isinstance(item, dict)]


def _known_event_matches(text: str, event: dict[str, Any]) -> bool:
    keys = event.get("duplicate_detection_keys") or event.get("keywords") or []
    if isinstance(keys, list):
        for key in keys:
            value = str(key).strip().lower()
            if len(value) >= 3 and value in text:
                return True
    core_fact = str(event.get("core_fact") or event.get("summary") or "").lower()
    terms = _terms(core_fact)
    if not terms:
        return False
    hits = sum(1 for term in terms if term in text)
    return hits >= min(3, len(terms))


def _policy_items(context: JsonObject) -> list[dict[str, Any]]:
    raw = context.get("monitoring_policies") or context.get("monitoring_policy") or []
    if isinstance(raw, dict):
        raw = [
            *list(raw.get("direct_trade_rules") or []),
            *list(raw.get("escalation_rules") or []),
            *list(raw.get("policies") or []),
        ]
    if not isinstance(raw, list):
        return []
    return [dict(item) for item in raw if isinstance(item, dict)]


def _policy_matches(text: str, policy: dict[str, Any]) -> bool:
    trigger = policy.get("trigger")
    trigger_text = ""
    if isinstance(trigger, dict):
        trigger_text = str(trigger.get("condition") or trigger.get("description") or "")
    condition = str(policy.get("trigger_condition") or trigger_text or policy.get("name") or "")
    lowered = condition.lower().strip()
    if lowered and lowered in text:
        return True
    terms = _terms(lowered)
    if not terms:
        return False
    hits = sum(1 for term in terms if term in text)
    return hits >= min(2, len(terms))


def _policy_type(policy: dict[str, Any]) -> str:
    policy_type = str(policy.get("policy_type") or policy.get("action_type") or "").strip()
    if policy_type == "push_to_agent":
        return "escalate"
    return policy_type


def _is_relevant_without_policy(message: RuntimeSourceMessage, text: str) -> bool:
    if message.source_type is SourceType.SOCIAL:
        words = _terms(text)
        noisy_tokens = {"moon", "rocket", "lol", "meme", "pump", "hype"}
        if len(words) < 5 or noisy_tokens & set(words):
            return False
    ticker = message.ticker.lower()
    return ticker in text or any(symbol.lower() in text for symbol in message.symbols)


def _terms(value: str) -> list[str]:
    return [term for term in re.findall(r"[a-z0-9]+", value.lower()) if len(term) >= 3]
