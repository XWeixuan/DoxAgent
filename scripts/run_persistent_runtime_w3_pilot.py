"""Run the fixed W3 real-model Pilot without writing production business state."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter, sleep
from typing import Any
from uuid import uuid4

from doxagent.codex_runtime.client import HttpCodexWorkerClient
from doxagent.monitoring.schema import SourceType
from doxagent.persistent_runtime_v2.repository import InMemoryPersistentRuntimeV2Repository
from doxagent.persistent_runtime_v2.schema import (
    RuntimeCase,
    RuntimeCaseStatus,
    RuntimeConfidence,
    RuntimePrimaryRoute,
    RuntimeRouteDecision,
    RuntimeSideEffect,
    RuntimeTechnicalStatus,
    RuntimeVersionPin,
    SourceMessageEnvelope,
    SourceMessageSnapshot,
    W1NoveltyResult,
    W1NoveltyVerdict,
    W2PolicyResult,
    W3ContextVersionPin,
    W3Mode,
    W3RouteCase,
)
from doxagent.persistent_runtime_v2.w3 import (
    CodexW3AgentRunner,
    W3Error,
    W3PreparedContext,
)
from doxagent.settings import DoxAgentSettings
from doxagent.workflows.codex_document2.schema import (
    Document2Document,
    Document2InputManifest,
    ExpectationShell,
    ExpectationState,
    ExpectationUnit,
    InputAvailability,
    InputManifestEntry,
    PotentialGap,
)
from doxagent.workflows.codex_document3.schema import (
    ActivationCondition,
    Calibration,
    Document2Ref,
    Policy,
    PolicyDecision,
    PolicySet,
    PolicySourceRef,
    PublicationState,
)


class FixedContextProvider:
    def __init__(self, context: W3PreparedContext) -> None:
        self.context = context

    async def load(self, _case: RuntimeCase) -> W3PreparedContext:
        return self.context


def _context() -> W3PreparedContext:
    as_of = datetime(2026, 8, 31, tzinfo=UTC)
    manifest = Document2InputManifest(
        global_research=InputManifestEntry(
            status=InputAvailability.AVAILABLE,
            source_run_id="w3-pilot-d1-v1",
            as_of=as_of,
        ),
        narrative_research=InputManifestEntry(status=InputAvailability.ABSENT),
        event_library=InputManifestEntry(status=InputAvailability.AVAILABLE, as_of=as_of),
    )
    document2 = Document2Document(
        document2_run_id="w3-pilot-d2-v1",
        ticker="MU",
        as_of=as_of,
        source_global_run_id="w3-pilot-d1-v1",
        input_manifest=manifest,
        shells=[
            ExpectationShell(
                shell_id="S1",
                core_question="Will HBM adoption and supply economics exceed the baseline?",
                boundary_rule=(
                    "Track binding customer adoption, material supply loss, and margin "
                    "breaks."
                ),
                units=[
                    ExpectationUnit(
                        expectation_id="U1",
                        proposition=(
                            "HBM demand is strong, but planned capacity and ordinary qualification "
                            "progress are already expected; only material timing, volume, or "
                            "economics "
                            "changes create surprise."
                        ),
                        horizon="2026-2027",
                        state=ExpectationState(),
                        potential_gaps=[
                            PotentialGap(
                                gap_id="G1",
                                possible_occurrence="Binding customer commitment exceeds baseline.",
                                derivation=(
                                    "Adoption moves from expected ramp to secured economics."
                                ),
                                expected_revision="Raise realized demand and utilization baseline.",
                            ),
                            PotentialGap(
                                gap_id="G2",
                                possible_occurrence="Material supply or margin break.",
                                derivation="Directly changes sellable volume or unit economics.",
                                expected_revision="Lower earnings and delivery baseline.",
                            ),
                        ],
                    )
                ],
            )
        ],
    )
    d2_ref = Document2Ref(
        run_id="w3-pilot-d2-v1",
        artifact_id="w3-pilot-d2-document",
        sha256="a" * 64,
        published_at=as_of,
        publication_state=PublicationState.COMPLETE,
    )
    policies = [
        Policy(
            policy_id="pol_hbm_commitment",
            title="Binding material HBM customer commitment",
            source_refs=[PolicySourceRef(shell_id="S1", expectation_id="U1", gap_id="G1")],
            decision=PolicyDecision.LONG,
            match_scope="Named customer binding HBM qualification or purchase commitment.",
            activation_conditions=[
                ActivationCondition(
                    condition_id="C1",
                    criterion="Binding named-customer volume commitment is material.",
                    calibration=Calibration(
                        reference_state="Planned qualification and capacity ramp are expected.",
                        trigger_boundary=(
                            "Binding commitment with material volume or timing uplift."
                        ),
                        qualifying_evidence="Company/customer filing or official release.",
                    ),
                )
            ],
            activation_summary=(
                "Trade LONG when adoption becomes binding and materially exceeds baseline."
            ),
        ),
        Policy(
            policy_id="pol_margin_break",
            title="Gross margin downside break",
            source_refs=[PolicySourceRef(shell_id="S1", expectation_id="U1", gap_id="G2")],
            decision=PolicyDecision.SHORT,
            match_scope="Management gross margin guidance below calibrated floor.",
            activation_conditions=[
                ActivationCondition(
                    condition_id="C2",
                    criterion="Next-quarter gross margin guide is below 25 percent.",
                    calibration=Calibration(
                        reference_state="Baseline gross margin remains above 25 percent.",
                        trigger_boundary="Formal guide below 25 percent.",
                        qualifying_evidence="Official earnings release or call.",
                    ),
                )
            ],
            activation_summary="Trade SHORT on a formal sub-25-percent margin guide.",
        ),
    ]
    return W3PreparedContext(
        version_pin=W3ContextVersionPin(
            document1_run_id="w3-pilot-d1-v1",
            document2_run_id="w3-pilot-d2-v1",
            event_library_version=1,
            policy_set_version=1,
        ),
        document1=(
            "# Fixed W3 Pilot D1\n"
            "MU is expected to benefit from strong HBM demand. The baseline already includes "
            "the announced capacity ramp, routine customer qualifications, and elevated AI-memory "
            "expectations. Material binding volume, a large timing change, a direct supply loss, "
            "or a major margin break can still change the earnings path.\n"
        ),
        document2=document2,
        policy_set=PolicySet(
            ticker="MU",
            policy_set_version=1,
            document2_ref=d2_ref,
            policies=policies,
            published_at=as_of,
        ),
        reference_view=(
            "# Reference View — MU v1\n"
            "## E1\nKnown fact: the company previously announced its baseline HBM production "
            "milestone; later repetition without changed timing, customer, volume, or economics "
            "is OLD.\n"
            "## E2\nKnown fact: management previously guided gross margin to 20 percent; an "
            "unchanged repetition is OLD even though pol_margin_break describes that state.\n"
        ),
        document2_publication_state="COMPLETE",
    )


def _case(
    row: dict[str, Any],
    index: int,
    *,
    pilot_run_token: str,
) -> tuple[RuntimeCase, W3RouteCase]:
    occurred = datetime(2026, 8, 31, 13, index, tzinfo=UTC)
    title = str(row["title"])
    body = str(row["body"])
    if title.startswith("Hypothetical:"):
        title = title.removeprefix("Hypothetical:").strip()
        body = (
            "Synthetic evaluation scenario: assume the described event has been "
            "authentically reported by a qualifying authoritative source as of the Case "
            f"cutoff; do not reject it merely because the corpus is synthetic. {body}"
        )
    source = SourceMessageEnvelope(
        source_message_id=f"w3-pilot-{row['case_id']}",
        source_id="w3-fixed-pilot",
        published_at=occurred,
        collected_at=occurred,
        message_bus_event_time=occurred,
        snapshot=SourceMessageSnapshot(
            ticker="MU",
            source_type=SourceType.MEDIA,
            interface_type="pilot",
            title=title,
            body=body,
        ),
    )
    w1 = W1NoveltyResult(
        result=W1NoveltyVerdict(row["w1_result"]),
        confidence=RuntimeConfidence(row["w1_confidence"]),
        reference_ids=(
            ["E1"] if row["w1_result"] == "OLD" else []
        ),
        reason="Fixed Pilot hot-path verdict.",
    )
    w2 = W2PolicyResult(
        policy_ids=row["w2_policy_ids"],
        confidence=RuntimeConfidence(row["w2_confidence"]),
        reason="Fixed Pilot hot-path verdict.",
    )
    case = RuntimeCase(
        case_id=f"case-w3-pilot-{index:02d}",
        trading_date=occurred.date(),
        source=source,
        version_pin=RuntimeVersionPin(
            event_library_version=1,
            provisional_snapshot_version=0,
            policy_set_version=1,
            runtime_projection_version=1,
        ),
        status=RuntimeCaseStatus.PENDING_W3,
        technical_status=RuntimeTechnicalStatus.OK,
        w1_final=w1,
        w2_final=w2,
        route=RuntimeRouteDecision(
            primary_route=RuntimePrimaryRoute.W3,
            side_effects=[RuntimeSideEffect.ROUTE_TO_W3],
            reason="Fixed W3 Pilot route.",
        ),
    )
    w3_case = W3RouteCase(
        w3_case_id=f"w3-pilot-{pilot_run_token}-{index:02d}",
        case_id=case.case_id,
        ticker="MU",
        source=source,
        w1_final=w1,
        w2_final=w2,
        event_library_version=1,
        policy_set_version=1,
        route_reason="Fixed W3 Pilot route.",
        mode=W3Mode(row["mode"]),
        attempt_count=1,
    )
    return case, w3_case


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker-url", default="http://127.0.0.1:8791")
    parser.add_argument(
        "--corpus", default="eval/persistent_runtime_w3/corpus_v1.json"
    )
    parser.add_argument(
        "--output", default=".tmp/persistent-runtime-w3-pilot/report.json"
    )
    args = parser.parse_args()
    settings = DoxAgentSettings()
    if not settings.codex_worker_bearer_token or not settings.codex_capability_secret:
        raise ValueError("Codex worker credentials are required")
    rows = json.loads(Path(args.corpus).read_text(encoding="utf-8"))
    pilot_run_token = uuid4().hex[:12]
    repository = InMemoryPersistentRuntimeV2Repository()
    worker = HttpCodexWorkerClient(
        args.worker_url,
        settings.codex_worker_bearer_token,
        capability_secret=settings.codex_capability_secret,
    )
    runner = CodexW3AgentRunner(
        worker=worker,
        workspace=worker,
        context_provider=FixedContextProvider(_context()),
        prompt_root=settings.persistent_runtime_v2_w3_prompt_root,
        model=settings.persistent_runtime_v2_w3_model,
        model_provider=settings.codex_model_provider,
        effort=settings.persistent_runtime_v2_w3_reasoning_effort,
        timeout_seconds=settings.persistent_runtime_v2_w3_timeout_seconds,
        workspace_namespace=f"w3-pilot-{pilot_run_token}",
    )
    outcomes: list[dict[str, Any]] = []
    try:
        for index, row in enumerate(rows, start=1):
            case, w3_case = _case(
                row,
                index,
                pilot_run_token=pilot_run_token,
            )
            repository.save_case(case)
            started = perf_counter()
            retry_errors: list[dict[str, Any]] = []
            for attempt_number in range(1, 4):
                w3_case.attempt_count = attempt_number
                slot = repository.acquire_w3_slot(ticker="MU", case_id=case.case_id)
                if slot is None:
                    raise RuntimeError("Sequential Pilot could not acquire main W3 slot")
                try:
                    result, thread_id, pin = runner.run(
                        case=case,
                        w3_case=w3_case,
                        slot=slot,
                    )
                    repository.release_w3_slot(slot, thread_id=thread_id)
                    break
                except W3Error as exc:
                    repository.release_w3_slot(
                        slot,
                        clear_main_thread=exc.invalid_thread,
                    )
                    retry_errors.append(
                        {
                            "attempt": attempt_number,
                            "code": exc.code,
                            "message": str(exc),
                        }
                    )
                    if attempt_number == 3:
                        raise
                    sleep(5 if attempt_number == 1 else 10)
            else:
                raise AssertionError("unreachable W3 Pilot retry state")
            observed = {
                "novelty": result.novelty.result.value,
                "policy_hit": bool(result.policy.policy_ids),
                "trade": result.expert_trade.trade,
                "direction": (
                    result.expert_trade.direction.value
                    if result.expert_trade.direction is not None
                    else None
                ),
            }
            expected = {
                "novelty": row["expected_novelty"],
                "policy_hit": row["expected_policy_hit"],
                "trade": row["expected_trade"],
                "direction": row["expected_direction"],
            }
            outcomes.append(
                {
                    "case_id": row["case_id"],
                    "mode": row["mode"],
                    "expected": expected,
                    "observed": observed,
                    "semantic_match": observed == expected,
                    "latency_ms": round((perf_counter() - started) * 1000),
                    "attempt_count": w3_case.attempt_count,
                    "retry_errors": retry_errors,
                    "thread_kind": slot.kind.value,
                    "thread_id_present": bool(thread_id),
                    "context_version_pin": pin.model_dump(mode="json"),
                    "result": result.model_dump(mode="json"),
                }
            )
            print(row["case_id"], observed, "match=", observed == expected, flush=True)
    finally:
        runner.close()
    report = {
        "schema_version": "persistent-runtime-w3-pilot.v1",
        "pilot_run_token": pilot_run_token,
        "model": settings.persistent_runtime_v2_w3_model,
        "effort": settings.persistent_runtime_v2_w3_reasoning_effort,
        "case_count": len(outcomes),
        "strict_result_count": len(outcomes),
        "semantic_match_count": sum(item["semantic_match"] for item in outcomes),
        "all_main_thread": all(item["thread_kind"] == "MAIN" for item in outcomes),
        "outcomes": outcomes,
    }
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(target.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
