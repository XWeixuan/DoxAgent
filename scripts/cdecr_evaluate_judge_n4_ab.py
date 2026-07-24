"""Run a resumable real-model A/B of the legacy and refactored N4 Judge protocols."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from cdecr.config import CDECRSettings
from cdecr.contracts import EventFamily, ParticipantRole, SourceMessage, TimePrecision
from cdecr.models import DashScopeStructuredModelClient, ModelAdapterError, ModelTier
from cdecr.ports import StructuredModelRequest, StructuredModelResult
from cdecr.preprocessing import (
    grounder_context,
    locate_unique_evidence_text,
    preprocess_source,
)
from cdecr.single_document import _compact_model_schema, _published_at_utc
from cdecr.single_document_contracts import (
    EvidenceText,
    JudgeCommandOutput,
    JudgeMentionDraft,
    validate_event_time_semantics,
)

EXPERIMENT_VERSION = "cdecr-judge-n4-ab-v1"
LEGACY_GIT_REVISION = "e72c779b1a679b7c914bde454011a43d9f72b930"
FROZEN_CORPUS_SHA256 = "5fd6c5f74e352da763b6767bfb6b39b359cfdca4dc5a5f5d1d4c94b3388bb467"
Arm = Literal["legacy", "refactored"]


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _json_hash(value: object) -> str:
    return _sha256_bytes(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )


def _git_text(revision: str, path: str) -> str:
    completed = subprocess.run(
        ["git", "show", f"{revision}:{path}"],
        check=True,
        capture_output=True,
    )
    return completed.stdout.decode("utf-8-sig")


def _load_legacy_contract_module() -> ModuleType:
    """Load the exact pre-refactor contracts without changing the current imports."""

    contracts_name = "_cdecr_n4_ab_legacy_contracts"
    single_name = "_cdecr_n4_ab_legacy_single_document_contracts"
    cached = sys.modules.get(single_name)
    if isinstance(cached, ModuleType):
        return cached

    contracts_module = ModuleType(contracts_name)
    contracts_module.__file__ = f"{LEGACY_GIT_REVISION}:src/cdecr/contracts.py"
    sys.modules[contracts_name] = contracts_module
    exec(
        compile(
            _git_text(LEGACY_GIT_REVISION, "src/cdecr/contracts.py"),
            contracts_module.__file__,
            "exec",
        ),
        contracts_module.__dict__,
    )

    single_module = ModuleType(single_name)
    single_module.__file__ = f"{LEGACY_GIT_REVISION}:src/cdecr/single_document_contracts.py"
    sys.modules[single_name] = single_module
    current_contracts = sys.modules.get("cdecr.contracts")
    sys.modules["cdecr.contracts"] = contracts_module
    try:
        exec(
            compile(
                _git_text(
                    LEGACY_GIT_REVISION,
                    "src/cdecr/single_document_contracts.py",
                ),
                single_module.__file__,
                "exec",
            ),
            single_module.__dict__,
        )
    finally:
        if current_contracts is None:
            del sys.modules["cdecr.contracts"]
        else:
            sys.modules["cdecr.contracts"] = current_contracts
    return single_module


def _model_client(settings: CDECRSettings) -> DashScopeStructuredModelClient:
    return DashScopeStructuredModelClient(
        tier=ModelTier.M4,
        api_key=settings.require_dashscope(),
        base_url=settings.dashscope_base_url,
        model=settings.model_m4,
        timeout_seconds=settings.model_timeout_seconds,
        fallback_api_keys=settings.dashscope_fallback_api_keys(),
    )


def _source_rows(path: Path) -> dict[str, SourceMessage]:
    rows: dict[str, SourceMessage] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        payload = json.loads(line)
        rows[str(payload["source_row_id"])] = SourceMessage.model_validate(payload["message"])
    return rows


def _document_context(source: SourceMessage, raw_drafts: list[dict[str, Any]]) -> str:
    document = preprocess_source(source).document
    mention_proxies = [
        SimpleNamespace(
            evidence_locations=[
                SimpleNamespace(
                    segment_id=str(evidence["segment_id"]),
                    text=str(evidence["text"]),
                )
                for evidence in draft["mention"]["evidence_locations"]
            ]
        )
        for draft in raw_drafts
    ]
    return grounder_context(document, mention_proxies)


def _normalize_mention_enums(value: object, counts: Counter[str]) -> None:
    if not isinstance(value, dict):
        return
    family = value.get("event_family")
    if isinstance(family, str) and family not in {item.value for item in EventFamily}:
        value["event_family"] = EventFamily.OTHER.value
        counts["event_family"] += 1
    participants = value.get("participants")
    if isinstance(participants, list):
        valid_roles = {item.value for item in ParticipantRole}
        for participant in participants:
            if not isinstance(participant, dict):
                continue
            role = participant.get("role")
            if isinstance(role, str) and role not in valid_roles:
                participant["role"] = ParticipantRole.OTHER.value
                counts["participants.role"] += 1
    event_time = value.get("time")
    if isinstance(event_time, dict):
        precision = event_time.get("precision")
        if isinstance(precision, str) and precision not in {item.value for item in TimePrecision}:
            event_time["precision"] = TimePrecision.UNKNOWN.value
            counts["time.precision"] += 1


def _normalize_output_enums(
    payload: dict[str, object],
    *,
    arm: Arm,
) -> tuple[dict[str, object], dict[str, int]]:
    cloned = json.loads(json.dumps(payload))
    counts: Counter[str] = Counter()
    if arm == "legacy":
        decisions = cloned.get("decisions")
        if isinstance(decisions, list):
            for decision in decisions:
                if not isinstance(decision, dict):
                    continue
                _normalize_mention_enums(decision.get("revised_mention"), counts)
                split_mentions = decision.get("split_mentions")
                if isinstance(split_mentions, list):
                    for mention in split_mentions:
                        _normalize_mention_enums(mention, counts)
    else:
        for group_name in (
            "accepted",
            "rejected",
            "split",
            "duplicates",
            "attribute_merges",
        ):
            commands = cloned.get(group_name)
            if not isinstance(commands, list):
                continue
            for command in commands:
                if not isinstance(command, dict):
                    continue
                reason = command.get("reason")
                if isinstance(reason, str) and len(reason) > 240:
                    command["reason"] = reason[:240]
                    counts["reason.truncated_to_240"] += 1
        accepted = cloned.get("accepted")
        if isinstance(accepted, list):
            for command in accepted:
                if not isinstance(command, dict):
                    continue
                changes = command.get("changes")
                if changes is None or changes == {}:
                    if "changes" in command:
                        command.pop("changes")
                        counts["accepted.changes.omitted_empty"] += 1
                    continue
                _normalize_mention_enums(changes, counts)
        split_commands = cloned.get("split")
        if isinstance(split_commands, list):
            for command in split_commands:
                if not isinstance(command, dict):
                    continue
                mentions = command.get("mentions")
                if isinstance(mentions, list):
                    for mention in mentions:
                        _normalize_mention_enums(mention, counts)
    return cloned, dict(counts)


def _evidence_text(value: object) -> EvidenceText:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return EvidenceText.model_validate(value)


def _validate_mention_evidence(
    mention: Any,
    *,
    source: SourceMessage,
) -> None:
    document = preprocess_source(source).document
    evidence_locations = mention.evidence_locations
    open_attributes = mention.open_attributes
    for evidence in evidence_locations:
        locate_unique_evidence_text(_evidence_text(evidence), document, source)
    for attribute in open_attributes:
        locate_unique_evidence_text(
            _evidence_text(attribute.evidence_location),
            document,
            source,
        )


def _new_mention_payload(raw_mention: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in raw_mention.items() if key != "local_package_hint"}


def _apply_new_changes(
    raw_mention: dict[str, Any],
    changes: object | None,
) -> JudgeMentionDraft:
    payload = _new_mention_payload(raw_mention)
    if isinstance(changes, BaseModel):
        for field_name in changes.model_fields_set:
            value = getattr(changes, field_name)
            payload[field_name] = (
                value.model_dump(mode="json")
                if isinstance(value, BaseModel)
                else [
                    item.model_dump(mode="json") if isinstance(item, BaseModel) else item
                    for item in value
                ]
                if isinstance(value, list)
                else value.value
                if hasattr(value, "value")
                else value
            )
    return JudgeMentionDraft.model_validate(payload)


def _validate_legacy_output(
    output: Any,
    *,
    raw_drafts: list[dict[str, Any]],
    source: SourceMessage,
) -> None:
    full_ids = {str(item["draft_id"]) for item in raw_drafts}
    draft_by_id = {str(item["draft_id"]): item for item in raw_drafts}
    decisions = output.decisions
    targets = [str(item.target_draft_id) for item in decisions]
    if set(targets) != full_ids or len(targets) != len(set(targets)):
        raise ValueError("legacy Judge must cover every draft exactly once")
    for decision in decisions:
        target_id = str(decision.target_draft_id)
        keep_id = decision.target_mention_id
        if keep_id is not None and (str(keep_id) not in full_ids or str(keep_id) == target_id):
            raise ValueError("legacy Judge target must name another visible draft")
        if decision.revised_mention is not None:
            _validate_mention_evidence(decision.revised_mention, source=source)
        for mention in decision.split_mentions:
            _validate_mention_evidence(mention, source=source)
        if decision.attribute is not None:
            document = preprocess_source(source).document
            locate_unique_evidence_text(
                _evidence_text(decision.attribute.evidence_location),
                document,
                source,
            )
        if str(decision.action) == "ACCEPT" and decision.revised_mention is None:
            legacy_module = _load_legacy_contract_module()
            original = legacy_module.MentionDraft.model_validate(draft_by_id[target_id]["mention"])
            _validate_mention_evidence(original, source=source)


def _validate_refactored_output(
    output: JudgeCommandOutput,
    *,
    raw_drafts: list[dict[str, Any]],
    source: SourceMessage,
) -> None:
    short_to_draft = {f"d{index}": draft for index, draft in enumerate(raw_drafts, start=1)}
    visible_ids = set(short_to_draft)
    targets = [
        *[item.id for item in output.accepted],
        *[item.id for item in output.rejected],
        *[item.id for item in output.split],
        *[item.id for item in output.duplicates],
        *[item.id for item in output.attribute_merges],
    ]
    if set(targets) != visible_ids or len(targets) != len(set(targets)):
        raise ValueError("refactored Judge must cover every draft exactly once")
    accepted_ids = {item.id for item in output.accepted}
    semantic_errors: list[str] = []
    keep_targets = [(command.id, command.keep_id) for command in output.duplicates]
    keep_targets.extend((command.id, command.keep_id) for command in output.attribute_merges)
    for command_id, keep_id in keep_targets:
        if keep_id not in accepted_ids or keep_id == command_id:
            semantic_errors.append(f"{command_id}: keep_id must name another final ACCEPT")
    for accepted_command in output.accepted:
        try:
            mention = _apply_new_changes(
                short_to_draft[accepted_command.id]["mention"],
                accepted_command.changes,
            )
            validate_event_time_semantics(mention.time)
            _validate_mention_evidence(mention, source=source)
        except (ValidationError, ValueError) as exc:
            semantic_errors.append(f"ACCEPT {accepted_command.id}: {exc}")
    for split_command in output.split:
        for split_index, mention in enumerate(split_command.mentions, start=1):
            try:
                validate_event_time_semantics(mention.time)
                _validate_mention_evidence(mention, source=source)
            except (ValidationError, ValueError) as exc:
                semantic_errors.append(f"SPLIT {split_command.id} replacement {split_index}: {exc}")
    document = preprocess_source(source).document
    for merge_command in output.attribute_merges:
        try:
            locate_unique_evidence_text(
                merge_command.attribute.evidence_location,
                document,
                source,
            )
        except ValueError as exc:
            semantic_errors.append(f"MERGE_AS_ATTRIBUTE {merge_command.id}: {exc}")
    if semantic_errors:
        raise ValueError("Judge semantic validation failed for: " + " | ".join(semantic_errors))


def _validation_error(exc: Exception) -> object:
    if isinstance(exc, ValidationError):
        return json.loads(
            json.dumps(
                exc.errors(include_input=False, include_url=False),
                ensure_ascii=False,
                default=str,
            )
        )
    return str(exc)


def _attempt_record(
    *,
    attempt: int,
    result: StructuredModelResult | None = None,
    error: Exception | None = None,
    validation_error: object | None = None,
    normalized_fields: dict[str, int] | None = None,
) -> dict[str, object]:
    return {
        "attempt": attempt,
        "provider_status": "SUCCEEDED" if result is not None else "FAILED",
        "input_tokens": (
            result.input_tokens
            if result is not None
            else error.input_tokens
            if isinstance(error, ModelAdapterError)
            else None
        ),
        "output_tokens": (
            result.output_tokens
            if result is not None
            else error.output_tokens
            if isinstance(error, ModelAdapterError)
            else None
        ),
        "latency_ms": (
            result.latency_ms
            if result is not None
            else error.latency_ms
            if isinstance(error, ModelAdapterError)
            else 0
        ),
        "request_id": result.request_id if result is not None else None,
        "provider_error_code": (error.code if isinstance(error, ModelAdapterError) else None),
        "validation_error": validation_error,
        "normalized_fields": normalized_fields or {},
    }


def _legacy_common_decisions(
    output: Any,
    *,
    raw_drafts: list[dict[str, Any]],
) -> list[dict[str, object]]:
    full_to_short = {
        str(draft["draft_id"]): f"d{index}" for index, draft in enumerate(raw_drafts, start=1)
    }
    draft_by_id = {str(draft["draft_id"]): draft for draft in raw_drafts}
    decisions: list[dict[str, object]] = []
    for decision in output.decisions:
        target = str(decision.target_draft_id)
        changed_fields: list[str] = []
        revised = decision.revised_mention
        if revised is not None:
            before = draft_by_id[target]["mention"]
            after = revised.model_dump(mode="json")
            changed_fields = sorted(
                key for key in set(before) | set(after) if before.get(key) != after.get(key)
            )
        decisions.append(
            {
                "id": full_to_short[target],
                "action": str(decision.action),
                "reason": decision.reason,
                "changed_fields": changed_fields,
                "keep_id": (
                    full_to_short.get(str(decision.target_mention_id))
                    if decision.target_mention_id is not None
                    else None
                ),
                "split_count": len(decision.split_mentions),
                "revised_mention": (
                    revised.model_dump(mode="json") if revised is not None else None
                ),
                "split_mentions": [
                    item.model_dump(mode="json") for item in decision.split_mentions
                ],
                "attribute": (
                    decision.attribute.model_dump(mode="json")
                    if decision.attribute is not None
                    else None
                ),
            }
        )
    return decisions


def _refactored_common_decisions(
    output: JudgeCommandOutput,
) -> list[dict[str, object]]:
    decisions: list[dict[str, object]] = []
    for accepted_command in output.accepted:
        decisions.append(
            {
                "id": accepted_command.id,
                "action": "ACCEPT",
                "reason": accepted_command.reason,
                "changed_fields": (
                    sorted(accepted_command.changes.model_fields_set)
                    if accepted_command.changes is not None
                    else []
                ),
                "keep_id": None,
                "split_count": 0,
                "changes": (
                    accepted_command.changes.model_dump(
                        mode="json",
                        exclude_unset=True,
                    )
                    if accepted_command.changes is not None
                    else None
                ),
            }
        )
    for rejected_command in output.rejected:
        decisions.append(
            {
                "id": rejected_command.id,
                "action": "REJECT",
                "reason": rejected_command.reason,
                "changed_fields": [],
                "keep_id": None,
                "split_count": 0,
            }
        )
    for split_command in output.split:
        decisions.append(
            {
                "id": split_command.id,
                "action": "SPLIT",
                "reason": split_command.reason,
                "changed_fields": [],
                "keep_id": None,
                "split_count": len(split_command.mentions),
                "split_mentions": [item.model_dump(mode="json") for item in split_command.mentions],
            }
        )
    for duplicate_command in output.duplicates:
        decisions.append(
            {
                "id": duplicate_command.id,
                "action": "DUPLICATE",
                "reason": duplicate_command.reason,
                "changed_fields": [],
                "keep_id": duplicate_command.keep_id,
                "split_count": 0,
            }
        )
    for merge_command in output.attribute_merges:
        decisions.append(
            {
                "id": merge_command.id,
                "action": "MERGE_AS_ATTRIBUTE",
                "reason": merge_command.reason,
                "changed_fields": [],
                "keep_id": merge_command.keep_id,
                "split_count": 0,
                "attribute": merge_command.attribute.model_dump(mode="json"),
            }
        )
    return sorted(decisions, key=lambda item: int(str(item["id"])[1:]))


def _request_for_arm(
    *,
    arm: Arm,
    source: SourceMessage,
    raw_drafts: list[dict[str, Any]],
    legacy_module: ModuleType,
) -> tuple[StructuredModelRequest, type[BaseModel]]:
    document = _document_context(source, raw_drafts)
    if arm == "legacy":
        output_type = legacy_module.JudgeModelOutput
        request = StructuredModelRequest(
            system_prompt=_git_text(
                LEGACY_GIT_REVISION,
                "src/cdecr/prompts/v1/judge.md",
            ),
            user_prompt=json.dumps(
                {
                    "batch_index": 0,
                    "batch_count": 1,
                    "document": document,
                    "drafts": raw_drafts,
                },
                ensure_ascii=False,
            ),
            json_schema=output_type.model_json_schema(),
        )
        return request, output_type

    output_type = JudgeCommandOutput
    model_drafts = [
        {
            "id": f"d{index}",
            "mention": _new_mention_payload(draft["mention"]),
        }
        for index, draft in enumerate(raw_drafts, start=1)
    ]
    request = StructuredModelRequest(
        system_prompt=(
            Path("src/cdecr/prompts/v1/judge.md").read_text(encoding="utf-8").lstrip("\ufeff")
        ),
        user_prompt=json.dumps(
            {
                "published_at": _published_at_utc(source.published_at),
                "batch_index": 0,
                "batch_count": 1,
                "document": document,
                "drafts": model_drafts,
            },
            ensure_ascii=False,
        ),
        json_schema=_compact_model_schema(JudgeCommandOutput.model_json_schema()),
    )
    return request, output_type


def _run_arm_document(
    *,
    arm: Arm,
    document_payload: dict[str, Any],
    source: SourceMessage,
    settings: CDECRSettings,
) -> dict[str, object]:
    raw_drafts = document_payload["grounder"]["drafts"]
    legacy_module = _load_legacy_contract_module()
    request, output_type = _request_for_arm(
        arm=arm,
        source=source,
        raw_drafts=raw_drafts,
        legacy_module=legacy_module,
    )
    client = _model_client(settings)
    attempts: list[dict[str, object]] = []
    invalid_payload: object | None = None
    validation_error: object | None = None
    invalid_outputs: list[object] = []
    final_output: BaseModel | None = None
    final_payload: dict[str, object] | None = None

    for attempt in (1, 2):
        current_request = request
        if attempt == 2:
            current_request = StructuredModelRequest(
                system_prompt=(
                    "Repair the previous invalid structured output. Return only a corrected "
                    "object matching the schema and every evidence/candidate constraint."
                ),
                user_prompt=json.dumps(
                    {
                        "original_request": request.user_prompt,
                        "invalid_payload": invalid_payload,
                        "validation_error": validation_error,
                    },
                    ensure_ascii=False,
                ),
                json_schema=output_type.model_json_schema(),
            )
        try:
            result = client.complete(current_request)
        except ModelAdapterError as exc:
            attempts.append(_attempt_record(attempt=attempt, error=exc))
            if attempt == 1 and exc.code in {"invalid_json", "invalid_json_shape"}:
                invalid_payload = exc.raw_response_text
                invalid_outputs.append(exc.raw_response_text)
                validation_error = exc.code
                continue
            break

        normalized_payload, normalized_fields = _normalize_output_enums(
            result.payload,
            arm=arm,
        )
        try:
            parsed = output_type.model_validate(normalized_payload)
            if arm == "legacy":
                _validate_legacy_output(
                    parsed,
                    raw_drafts=raw_drafts,
                    source=source,
                )
            else:
                if not isinstance(parsed, JudgeCommandOutput):
                    raise TypeError("refactored output has the wrong contract type")
                _validate_refactored_output(
                    parsed,
                    raw_drafts=raw_drafts,
                    source=source,
                )
        except (ValidationError, ValueError, TypeError) as exc:
            validation_error = _validation_error(exc)
            invalid_payload = result.payload
            invalid_outputs.append(result.payload)
            attempts.append(
                _attempt_record(
                    attempt=attempt,
                    result=result,
                    validation_error=validation_error,
                    normalized_fields=normalized_fields,
                )
            )
            if attempt == 1:
                continue
            break
        attempts.append(
            _attempt_record(
                attempt=attempt,
                result=result,
                normalized_fields=normalized_fields,
            )
        )
        final_output = parsed
        final_payload = normalized_payload
        break

    succeeded = final_output is not None
    common_decisions: list[dict[str, object]] = []
    if final_output is not None and arm == "legacy":
        common_decisions = _legacy_common_decisions(
            final_output,
            raw_drafts=raw_drafts,
        )
    elif isinstance(final_output, JudgeCommandOutput):
        common_decisions = _refactored_common_decisions(final_output)
    return {
        "experiment_version": EXPERIMENT_VERSION,
        "arm": arm,
        "legacy_git_revision": LEGACY_GIT_REVISION,
        "source_row_id": document_payload["source_row_id"],
        "message_id": document_payload["message_id"],
        "draft_count": len(raw_drafts),
        "status": "SUCCEEDED" if succeeded else "FAILED",
        "first_pass_valid": succeeded and len(attempts) == 1,
        "repaired": succeeded and len(attempts) == 2,
        "model": settings.model_m4,
        "prompt_sha256": _sha256_bytes(request.system_prompt.encode("utf-8")),
        "schema_sha256": _json_hash(request.json_schema),
        "schema_chars": len(
            json.dumps(
                request.json_schema,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        ),
        "user_prompt_chars": len(request.user_prompt),
        "attempts": attempts,
        "invalid_outputs": invalid_outputs,
        "output": final_payload,
        "decisions": common_decisions,
    }


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _aggregate(output_dir: Path) -> dict[str, object]:
    arms: dict[str, object] = {}
    for arm in ("legacy", "refactored"):
        records = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in sorted((output_dir / "arms" / arm).glob("*.json"))
        ]
        succeeded = [item for item in records if item["status"] == "SUCCEEDED"]
        action_counts: Counter[str] = Counter()
        changed_fields: Counter[str] = Counter()
        total_input_tokens = 0
        total_output_tokens = 0
        total_latency_ms = 0
        for record in records:
            for attempt in record["attempts"]:
                total_input_tokens += int(attempt["input_tokens"] or 0)
                total_output_tokens += int(attempt["output_tokens"] or 0)
                total_latency_ms += int(attempt["latency_ms"] or 0)
            for decision in record["decisions"]:
                action_counts[str(decision["action"])] += 1
                changed_fields.update(decision.get("changed_fields") or [])
        arms[arm] = {
            "document_count": len(records),
            "draft_count": sum(int(item["draft_count"]) for item in records),
            "succeeded_count": len(succeeded),
            "failed_count": len(records) - len(succeeded),
            "first_pass_valid_count": sum(bool(item["first_pass_valid"]) for item in records),
            "repair_success_count": sum(bool(item["repaired"]) for item in records),
            "input_tokens": total_input_tokens,
            "output_tokens": total_output_tokens,
            "latency_ms": total_latency_ms,
            "action_counts": dict(action_counts),
            "changed_field_counts": dict(changed_fields),
            "schema_chars": sorted(
                {
                    int(item["schema_chars"])
                    for item in records
                    if item.get("schema_chars") is not None
                }
            ),
            "user_prompt_chars": sum(int(item.get("user_prompt_chars") or 0) for item in records),
        }
    return {
        "experiment_version": EXPERIMENT_VERSION,
        "legacy_git_revision": LEGACY_GIT_REVISION,
        "frozen_corpus_sha256": FROZEN_CORPUS_SHA256,
        "arms": arms,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--corpus",
        type=Path,
        default=Path(".tmp/cdecr/grounder_quality_v5/grounder_30.json"),
    )
    parser.add_argument(
        "--sources",
        type=Path,
        default=Path(".tmp/cdecr/grounder_quality_v5/live_all.jsonl"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(".tmp/cdecr/judge_n4_ab_v1"),
    )
    parser.add_argument("--max-workers", type=int, default=2)
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--limit-documents", type=int)
    args = parser.parse_args()

    corpus_bytes = args.corpus.read_bytes()
    actual_hash = _sha256_bytes(corpus_bytes)
    if actual_hash != FROZEN_CORPUS_SHA256:
        raise ValueError(
            f"frozen corpus hash mismatch: expected {FROZEN_CORPUS_SHA256}, got {actual_hash}"
        )
    corpus = json.loads(corpus_bytes)
    documents = list(corpus["documents"])
    if args.limit_documents is not None:
        documents = documents[: args.limit_documents]
    if args.limit_documents is None:
        draft_count = sum(len(item["grounder"]["drafts"]) for item in documents)
        if len(documents) != 30 or draft_count != 229:
            raise ValueError("frozen corpus must contain exactly 30 documents / 229 drafts")

    source_by_row = _source_rows(args.sources)
    settings = CDECRSettings()
    settings.require_dashscope()
    _load_legacy_contract_module()

    jobs: list[tuple[Arm, dict[str, Any], SourceMessage, Path]] = []
    for document_index, document in enumerate(documents):
        source = source_by_row[str(document["source_row_id"])]
        arm_order: tuple[Arm, Arm] = (
            ("legacy", "refactored") if document_index % 2 == 0 else ("refactored", "legacy")
        )
        for arm in arm_order:
            path = args.output_dir / "arms" / arm / f"{document['source_row_id']}.json"
            if path.exists():
                existing = json.loads(path.read_text(encoding="utf-8"))
                if existing.get("status") == "SUCCEEDED" or not args.retry_failed:
                    continue
            jobs.append((arm, document, source, path))

    completed = 0
    with ThreadPoolExecutor(max_workers=max(1, args.max_workers)) as executor:
        futures = {
            executor.submit(
                _run_arm_document,
                arm=arm,
                document_payload=document,
                source=source,
                settings=settings,
            ): (arm, document, path)
            for arm, document, source, path in jobs
        }
        for future in as_completed(futures):
            arm, document, path = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                result = {
                    "experiment_version": EXPERIMENT_VERSION,
                    "arm": arm,
                    "source_row_id": document["source_row_id"],
                    "message_id": document["message_id"],
                    "draft_count": len(document["grounder"]["drafts"]),
                    "status": "FAILED",
                    "first_pass_valid": False,
                    "repaired": False,
                    "attempts": [],
                    "decisions": [],
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            _write_json(path, result)
            completed += 1
            print(
                json.dumps(
                    {
                        "completed": completed,
                        "scheduled": len(jobs),
                        "arm": arm,
                        "message_id": document["message_id"],
                        "status": result["status"],
                        "first_pass_valid": result.get("first_pass_valid"),
                        "repaired": result.get("repaired"),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )

    summary = _aggregate(args.output_dir)
    _write_json(args.output_dir / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
