import json

import pytest

from doxagent.codex_worker.schema import WorkerJob
from doxagent.persistent_runtime_v2.w3 import CodexW3AgentRunner
from doxagent.persistent_runtime_v2.w3_contracts import OPERATIONS_PHASE, TRADE_PHASE
from doxagent.persistent_runtime_v2.w3_models import execution_profile, freeze_case_profile
from tests.test_persistent_runtime_w3 import _Context, _Workspace
from tests.test_runtime_policy_direction_usage import fixture_case
from tests.test_w3_v4 import PhaseAgent, decision, enqueue, operations, runtime


def set_related(case):
    case.w2_final = case.w2_final.model_copy(
        update=dict(
            policy_hit=False,
            policy_related=True,
            policy_ids=[],
            hit_condition_ids=[],
        )
    )


class ModelWorker:
    def __init__(self):
        self.requests = []

    async def run(self, request):
        self.requests.append(request)
        value = (
            {"ready": True}
            if request.task_phase == "w3_template"
            else decision().model_dump(mode="json")
            if request.task_phase == TRADE_PHASE
            else operations(watch=False, pin=False).model_dump(mode="json")
        )
        return WorkerJob(
            job_id=f"job-{len(self.requests)}",
            run_id=request.run_id,
            attempt_id=request.attempt_id,
            status="succeeded",
            thread_id=request.thread_id or "mother",
            final_response=json.dumps(value),
        )


@pytest.mark.parametrize(
    "kind,model,effort",
    [
        ("HIT", "gpt-6.1-sol", "medium"),
        ("RELATED", "gpt-6.1-sol", "medium"),
        ("OPEN", "gpt-6-luna", "max"),
    ],
)
def test_all_worker_phases_use_the_frozen_case_profile(tmp_path, kind, model, effort):
    from doxagent.persistent_runtime_v2.journal import RuntimeJournal

    case, _ = fixture_case()
    if kind != "HIT":
        set_related(case)
        if kind == "OPEN":
            case.w2_final = case.w2_final.model_copy(
                update=dict(policy_related=False, matched_condition_ids=[])
            )
    case.frozen_inputs["w3_assets"] = {
        "w3/agent.md": "synthetic",
        "w3/skills/w3.md": "synthetic",
        "w3/template.md": "{payload}",
        "w3/task.md": "{payload}",
    }
    freeze_case_profile(case)
    context = PhaseAgent().prepare_case(case)
    worker = ModelWorker()
    runner = CodexW3AgentRunner(
        worker=worker,
        workspace=_Workspace(),
        context_provider=_Context(),
        prompt_root=tmp_path,
        journal=RuntimeJournal(tmp_path / "r.db"),
        execution_model_override="gpt-5.6-luna",
        timeout_seconds=30,
    )
    try:
        for phase in ("w3_template", TRADE_PHASE, OPERATIONS_PHASE):
            runner.run_phase(
                case=case,
                record=dict(
                    record_id="record",
                    workspace="model-test",
                    thread_id=None if phase == "w3_template" else "child",
                ),
                phase=phase,
                payload={},
                context=context,
            )
        assert [(r.model, r.effort) for r in worker.requests] == [(model, effort)] * 3
        # Later policy reassessment does not switch the second phase's model.
        case.w2_final = case.w2_final.model_copy(update={"policy_hit": kind != "HIT"})
        assert freeze_case_profile(case)["model"] == model
    finally:
        runner.close()


def test_related_case_has_its_own_fork_with_the_same_sol_profile_as_hit(tmp_path):
    service, _, agent, _ = runtime(tmp_path)
    original = agent.run_phase
    forks = []
    original_fork = agent.fork_thread

    def run_phase(**kwargs):
        result, job = original(**kwargs)
        if kwargs["phase"] == "w3_template":
            job.thread_id = "mother-" + kwargs["record"]["record_id"]
        return result, job

    def fork(request):
        forks.append(request)
        return original_fork(request)

    agent.run_phase = run_phase
    agent.fork_thread = fork
    try:
        hit = enqueue(service, "hit")
        service.process_pending_effects()
        service.process_pending_effects()
        related, _ = fixture_case("related")
        set_related(related)
        related = related.model_copy(
            update={
                "w3_contract_version": hit.w3_contract_version,
                "orchestration_revision": hit.orchestration_revision,
                "status": hit.status,
                "route": hit.route,
            }
        )
        related.frozen_inputs["w3_assets"] = hit.frozen_inputs["w3_assets"]
        related.frozen_inputs["policy_usage"] = hit.frozen_inputs["policy_usage"]
        service.repository.save_case(related)
        service._enqueue_route_effects(related, "")
        service.process_pending_effects()
        assert [(f.model, f.model_provider) for f in forks] == [
            ("gpt-6.1-sol", None),
            ("gpt-6.1-sol", None),
        ]
        assert (
            service.repository.get_case("hit").frozen_inputs["w3_execution_profile"]["effort"]
            == "medium"
        )
        assert (
            service.repository.get_case("related").frozen_inputs["w3_execution_profile"]["effort"]
            == "medium"
        )
    finally:
        service.close()


def test_unmigrated_case_preserves_its_old_settings():
    case, _ = fixture_case()
    case.frozen_inputs["models"] = {"w3_model": "gpt-6-luna", "w3_effort": "max"}
    assert execution_profile(case) == dict(model="gpt-6-luna", effort="max", provider=None)
