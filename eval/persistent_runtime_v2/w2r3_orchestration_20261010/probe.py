"""Synthetic provider contract probe; never reads production news or writes runtime state."""

import json
from pathlib import Path

from doxagent.persistent_runtime_v2.schema import (
    W1FactExtractionResult,
    W1NoveltyResult,
    W2NovelFactAlignmentResult,
    W2PolicyAssessmentResult,
)
from doxagent.persistent_runtime_v2.transport import (
    BailianRuntimeResponsesClient,
    RuntimeResponsesError,
    RuntimeResponsesRequest,
)
from doxagent.settings import DoxAgentSettings


def main():
    settings = DoxAgentSettings()
    client = BailianRuntimeResponsesClient(
        api_key=settings.require_dashscope_api_key(),
        base_url=settings.dashscope_chat_base_url,
        model=settings.persistent_runtime_v2_model,
        timeout_seconds=90,
    )
    receipts = []

    def call(name, model, payload, instructions, **kwargs):
        result = client.complete(
            RuntimeResponsesRequest(
                instructions=instructions,
                payload=payload,
                output_model=model,
                schema_name=name,
                **kwargs,
            )
        )
        receipts.append(
            {
                "name": name,
                "model": client.model,
                "response_id": result.response_id,
                "output": result.value.model_dump(mode="json"),
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "latency_ms": result.latency_ms,
            }
        )
        return result

    w1 = call(
        "probe_w1_r2",
        W1NoveltyResult,
        {"article": "Yesterday ALPHA opened a warehouse. Today ALPHA completed qualification."},
        "Synthetic test. Judge NEW because only qualification completed today is new. "
        "Use confidence normal, no references, fact_attributions null. "
        "State the new boundary in reason.",
    )
    extraction = call(
        "probe_w1_r3",
        W1FactExtractionResult,
        {},
        "Extract only the new fact identified in the preceding response. "
        "Use assertion_state ACTUAL, occurrence_date null, entities ALPHA.",
        previous_response_id=w1.response_id,
        inherit_context=True,
    )
    assert "qualification" in extraction.value.candidates[0].proposition.lower()
    for label, hit, related, condition, fact, expected in (
        ("hit", True, False, "C1", "ALPHA completed qualification today", True),
        ("related", False, True, "C2", "ALPHA completed qualification today", True),
        ("old", True, False, "C1", "ALPHA opened a warehouse yesterday", False),
    ):
        groups = [{"policy_id": "P1", "condition_ids": [condition]}]
        output = {
            "policy_hit": hit,
            "policy_related": related,
            "policy_ids": ["P1"] if hit else [],
            "hit_condition_ids": groups if hit else [],
            "matched_condition_ids": groups if related else [],
            "confidence": "normal",
            "reason": fact,
        }
        parent = call(
            f"probe_w2_r2_{label}",
            W2PolicyAssessmentResult,
            {"fact": fact, "condition_ref": f"P1/{condition}"},
            "Synthetic test. Return exactly this assessment: " + json.dumps(output),
        )
        child = call(
            f"probe_w2_r3_{label}",
            W2NovelFactAlignmentResult,
            {"w1_r2_reason": w1.value.reason},
            "Assess whether the fact supporting the preceding assessment belongs to the new "
            "fact boundary in w1_r2_reason. Return the inherited qualified condition_ref "
            "in condition_ids and is_new as a JSON boolean. No other task.",
            previous_response_id=parent.response_id,
            inherit_context=True,
        )
        assert child.value.condition_results[0].condition_ids == [f"P1/{condition}"]
        assert child.value.condition_results[0].is_new is expected
    try:
        call(
            "probe_invalid_parent",
            W2NovelFactAlignmentResult,
            {},
            "Return an empty condition_results array.",
            previous_response_id="resp_definitely_missing_w2r3_probe",
            inherit_context=True,
        )
        raise AssertionError("Invalid parent unexpectedly accepted")
    except RuntimeResponsesError as exc:
        receipts.append(
            {"name": "probe_invalid_parent", "error_code": exc.code, "retryable": exc.retryable}
        )
        assert exc.code == "provider_previous_response_unavailable", exc.code
    replay = call(
        "probe_replay",
        W2NovelFactAlignmentResult,
        {"w1_r2_reason": w1.value.reason},
        "Assess whether the preceding assessment fact is new within w1_r2_reason. "
        "Return P1/C1 with is_new false.",
        inherit_context=True,
        previous_response_id=parent.response_id,
        replay_parent=(
            json.dumps({"fact": fact, "condition_ref": "P1/C1"}),
            parent.value.model_dump_json(),
        ),
    )
    assert replay.value.condition_results[0].is_new is False
    Path(__file__).with_name("receipts.json").write_text(
        json.dumps(
            {"synthetic_only": True, "passed": True, "receipts": receipts},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(json.dumps({"passed": True, "calls": len(receipts), "model": client.model}))


if __name__ == "__main__":
    main()
