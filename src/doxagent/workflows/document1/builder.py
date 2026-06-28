# ruff: noqa: F403,F405
"""Behavior-preserving mixin extracted from initialization.py."""

from doxagent.workflows.initialization.shared import *


class Document1BuilderMixin:
    def _build_global_research_with_agent_runner(
        self,
        checkpoint: WorkflowCheckpoint,
        node: WorkflowNode,
    ) -> WorkflowCheckpoint:
        inputs = self._research_inputs_from_checkpoint(checkpoint)
        specs = [
            (
                AgentName.C1_FUNDAMENTAL_RESEARCH,
                "fundamental_report",
                (
                    "Generate a sourced ResearchSection covering recent fundamental "
                    "developments and how longer-cycle fundamentals explain current "
                    "market attention."
                ),
            ),
            (
                AgentName.C2_MACRO_RESEARCH,
                "macro_report",
                (
                    "Generate a sourced ResearchSection covering recent macro changes "
                    "that affect current pricing, using longer-cycle macro context only "
                    "when it explains the current setup."
                ),
            ),
            (
                AgentName.C3_INDUSTRY_RESEARCH,
                "industry_report",
                (
                    "Generate a sourced ResearchSection covering recent industry and "
                    "competitive developments, grounded in structural industry context "
                    "where useful."
                ),
            ),
            (
                AgentName.O4_MARKET_TRACE,
                "market_trace_report",
                (
                    "Generate a sourced ResearchSection covering recent price and flow "
                    "reaction first, with broader chart history used only as baseline "
                    "context."
                ),
            ),
        ]
        results: list[AgentResult] = []
        sections: dict[str, ResearchSection] = {}
        current = checkpoint
        jobs: list[_ParallelAgentJob] = []
        cached_results: dict[int, AgentResult] = {}
        for order, (agent_name, section_key, instruction) in enumerate(specs):
            recovered = self._recover_stale_agent_dispatch(
                current,
                node,
                agent_name,
                section_key,
            )
            if recovered is not current:
                current = recovered
                self.checkpoint_repository.save_checkpoint(current)
            cached = self._cached_global_research_agent_result(current, node, agent_name)
            if cached is not None:
                cached_results[order] = cached
                continue
            current = self._mark_agent_dispatch(
                current,
                node,
                agent_name,
                status="running",
                section_key=section_key,
            )
            jobs.append(
                _ParallelAgentJob(
                    order=order,
                    agent_name=agent_name,
                    task_type=TaskType.GENERATE_GLOBAL_RESEARCH,
                    output_schema="ResearchSection",
                    section_key=section_key,
                    extra_context=self._global_research_agent_context(
                        inputs,
                        section_key=section_key,
                        instruction=instruction,
                    ),
                )
            )
        if jobs:
            self.checkpoint_repository.save_checkpoint(current)

        def cache_global_research_outcome(outcome: _ParallelAgentOutcome) -> None:
            nonlocal current
            section_key = outcome.job.section_key
            if section_key is None:
                return
            if outcome.error is not None:
                current = self._mark_agent_dispatch(
                    current,
                    node,
                    outcome.job.agent_name,
                    status="failed",
                    section_key=section_key,
                    error_message=str(outcome.error),
                )
                self._save_parallel_outcome_checkpoint(current)
                return
            if outcome.result is None:
                return
            current = self._store_global_research_agent_result(
                current,
                node,
                outcome.job.agent_name,
                section_key,
                outcome.result,
            )
            self._save_parallel_outcome_checkpoint(current)

        outcomes_by_order = {
            outcome.job.order: outcome
            for outcome in self._run_agent_jobs_concurrently(
                current,
                node,
                jobs,
                on_outcome=cache_global_research_outcome,
            )
        }
        first_error: Exception | None = None
        for order, (agent_name, section_key, _instruction) in enumerate(specs):
            cached = cached_results.get(order)
            outcome = outcomes_by_order.get(order)
            if cached is not None:
                result = cached
            elif outcome is None:
                result = None
                first_error = first_error or WorkflowContractError(
                    f"{node.value}/{agent_name.value} did not return a parallel outcome."
                )
            elif outcome.error is not None:
                result = None
                current = self._mark_agent_dispatch(
                    current,
                    node,
                    agent_name,
                    status="failed",
                    section_key=section_key,
                    error_message=str(outcome.error),
                )
                self.checkpoint_repository.save_checkpoint(current)
                first_error = first_error or outcome.error
            else:
                result = outcome.result
            if result is None:
                continue

            results.append(result)
            try:
                self._assert_no_document1_proposed_patches(result, node)
                if cached is None:
                    self._write_working_memory(current, result, "global_research_agent_result")
                self._validate_agent_success(result, node, require_patches=False)
                section = self._research_section_from_result(
                    result,
                    "ResearchSection",
                )
                sections[section_key] = self._ensure_global_research_section_content(
                    current,
                    section_key,
                    section,
                    result,
                )
            except WorkflowContractError as exc:
                if cached is None and self._looks_like_schema_failure(exc):
                    self._write_parallel_agent_acceptance_failure(
                        current,
                        node,
                        agent_name,
                        result,
                        event_code="schema_failed",
                        message=str(exc),
                        expected_schema="ResearchSection",
                    )
                current = self._mark_agent_dispatch(
                    current,
                    node,
                    agent_name,
                    status="failed",
                    section_key=section_key,
                    error_message=str(exc),
                )
                self.checkpoint_repository.save_checkpoint(current)
                first_error = first_error or exc
                continue
            if cached is None:
                current = self._store_global_research_agent_result(
                    current,
                    node,
                    agent_name,
                    section_key,
                    result,
                )
                self.checkpoint_repository.save_checkpoint(current)

        if first_error is not None:
            raise first_error

        document = self.global_research_assembler.assemble_from_sections(
            current.ticker,
            fundamental_report=sections["fundamental_report"],
            macro_report=sections["macro_report"],
            industry_report=sections["industry_report"],
            market_trace_report=sections["market_trace_report"],
        )
        patch = self._global_research_patch(document, results)
        self._validate_patch_contract(patch, node)
        self._write_patch_audit_working_memory(
            current,
            patch,
            "global_research_assembly",
            {
                "status": "succeeded",
                "workflow_node": node.value,
                "source_agents": [result.agent_name.value for result in results],
                "rationale": patch.rationale,
            },
        )
        self._submit_patch(
            current.run_id,
            patch,
            f"{node.value} 已由 C1/C2/C3/O4 汇总 GlobalResearchDocument。",
        )
        stable_documents = list(current.stable_document_types)
        if DocumentType.GLOBAL_RESEARCH not in stable_documents:
            stable_documents.append(DocumentType.GLOBAL_RESEARCH)
        return self._mark_completed(
            current,
            node,
            stable_document_types=stable_documents,
            metadata=self._agent_metadata(node, results)
            | {
                "global_research_downstream_context": (
                    self.global_research_assembler.downstream_context(results)
                ),
                "global_research_patch_id": patch.patch_id,
            },
        )

    def _assert_no_document1_proposed_patches(
        self,
        result: AgentResult,
        node: WorkflowNode,
    ) -> None:
        if result.proposed_patches:
            raise WorkflowContractError(
                f"{node.value} forbids proposed_patches: Document1 agents must return "
                "ResearchSection output; GlobalResearchDocument assembly is owned by "
                "the workflow transaction layer."
            )

    def _research_section_from_result(
        self,
        result: AgentResult,
        expected_schema: str,
    ) -> ResearchSection:
        model = self.output_validator.validate(result.payload, expected_schema)
        section = (
            model
            if isinstance(model, ResearchSection)
            else ResearchSection.model_validate(model)
        )
        return section

    def _global_research_patch(
        self,
        document: GlobalResearchDocument,
        results: list[AgentResult],
    ) -> BlackboardPatch:
        evidence_refs = [evidence for result in results for evidence in result.evidence_refs]
        if not evidence_refs:
            raise WorkflowContractError("Global Research module outputs produced no evidence refs.")
        return BlackboardPatch(
            patch_id=new_id("patch"),
            target=BlackboardTarget(
                document_type=DocumentType.GLOBAL_RESEARCH,
                ticker=document.ticker,
                document_id=document.document_id,
                field_path="document",
            ),
            operation=PatchOperation.CREATE,
            before=None,
            after=document.model_dump(mode="json"),
            rationale="由 C1/C2/C3/O4 agent 输出汇总 GlobalResearchDocument。",
            evidence_refs=evidence_refs,
            author_agent=AgentName.C1_FUNDAMENTAL_RESEARCH,
            validation_status=ValidationStatus.VALID,
        )

    def _submit_global_narrative_report(
        self,
        checkpoint: WorkflowCheckpoint,
        node: WorkflowNode,
        result: AgentResult,
    ) -> WorkflowCheckpoint:
        result = self._ensure_o1_narrative_tool_evidence(checkpoint, result, node)
        self._validate_agent_success(result, node, require_patches=False)
        self._validate_o1_narrative_tool_gap(result, node)
        section = self._research_section_from_result(result, "ResearchSection")
        section = self._ensure_global_narrative_section_content(checkpoint, section, result)
        document_id = self._latest_global_research_document_id(checkpoint)
        patch = BlackboardPatch(
            patch_id=new_id("patch"),
            target=BlackboardTarget(
                document_type=DocumentType.GLOBAL_RESEARCH,
                ticker=checkpoint.ticker,
                document_id=document_id,
                field_path="document.market_narrative_report",
            ),
            operation=PatchOperation.UPDATE,
            before=None,
            after=section.model_dump(mode="json"),
            rationale="根据 expectation units 更新 GlobalResearchDocument 的市场叙事。",
            evidence_refs=section.evidence_refs or result.evidence_refs,
            author_agent=AgentName.O1_EXPECTATION_OWNER,
            validation_status=ValidationStatus.VALID,
        )
        result = result.model_copy(
            update={
                "proposed_patches": [patch],
                "evidence_refs": self._dedupe_evidence_refs(
                    [*result.evidence_refs, *patch.evidence_refs]
                ),
            },
            deep=True,
        )
        self._write_working_memory(checkpoint, result, "global_narrative_report")
        self._validate_patch_contract(patch, node)
        self._submit_patch(
            checkpoint.run_id,
            patch,
            "GenerateGlobalNarrativeReport 已更新 GlobalResearchDocument 市场叙事。",
            permissions=self._effective_permissions(
                self.registry.get(AgentName.O1_EXPECTATION_OWNER).runtime.to_permissions(),
                node,
                TaskType.GENERATE_GLOBAL_NARRATIVE_REPORT,
                AgentName.O1_EXPECTATION_OWNER,
            ),
        )
        return self._mark_completed(
            checkpoint,
            node,
            metadata=self._agent_metadata(node, [result])
            | {"global_narrative_patch_id": patch.patch_id},
        )

    def _latest_global_research_document_id(self, checkpoint: WorkflowCheckpoint) -> str:
        document = self._latest_global_research_document_payload(checkpoint)
        document_id = document.get("document_id")
        if not isinstance(document_id, str) or not document_id:
            raise WorkflowDependencyError("Global research document_id is missing.")
        return document_id

    def _latest_global_research_document_payload(
        self,
        checkpoint: WorkflowCheckpoint,
    ) -> dict[str, Any]:
        run = self.blackboard.get_run(checkpoint.run_id)
        bucket = run.belief_state.documents.get(DocumentType.GLOBAL_RESEARCH, {})
        if not bucket:
            raise WorkflowDependencyError("Missing global_research document.")
        latest = next(reversed(bucket.values()))
        if not isinstance(latest, dict):
            raise WorkflowDependencyError("Global research document is malformed.")
        document = latest.get("document")
        if not isinstance(document, dict):
            raise WorkflowDependencyError("Global research document payload is malformed.")
        return document

    def _expectation_names_from_belief_state(
        self,
        checkpoint: WorkflowCheckpoint,
    ) -> list[str]:
        run = self.blackboard.get_run(checkpoint.run_id)
        bucket = run.belief_state.documents.get(DocumentType.EXPECTATION_UNIT, {})
        names: list[str] = []
        for entry in bucket.values():
            if not isinstance(entry, dict):
                continue
            document = entry.get("document")
            if not isinstance(document, dict):
                continue
            name = document.get("expectation_name") or document.get("expectation_id")
            if name:
                names.append(str(name))
        return names
