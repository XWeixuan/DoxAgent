# 3.7/3.8 O1/A1/A2 Realization

## Summary

This phase realifies the initialization workflow path after Global Research:
O1 builds sourced expectation units and known events, A1 audits expectation
fields against DoxAtlas evidence, and A2 becomes a Tavily-only delegated
retrieval and fact-check agent.

The work keeps the DoxAgent boundary intact: agents return `AgentResult`, tools
go through `ToolRegistry`, stable state changes still require
`BlackboardService.submit_patch()`, and workflow checkpoints remain separate
from Blackboard business state.

## Key Decisions

- A2 keeps the internal `AgentName.A2_FACT_CHECK = "A2"` for compatibility, but
  its role now covers both fact-check and delegated information retrieval.
- A2 is strictly Tavily-only. Its default permissions include only
  `tavily.search` and `tavily.extract`; it does not call SEC, issuer material,
  DoxAtlas, `fact_check.search`, or external mock tools.
- `TaskType.DELEGATED_RETRIEVAL` is the standard route for non-fact-check
  information requests to A2.
- Other agents delegate retrieval gaps through `DelegatedRetrievalRequest` and
  `create_a2_retrieval_delegation(...)`; they do not call A2 internals.
- A1 audits DoxAtlas support and may create blocking objections or A2
  delegations, but it does not write Blackboard state directly.

## Runtime Behavior

- O1 emits `ExpectationConstructionResult` with proposed patches, evidence,
  delegations, unknowns, and rationale.
- A1 emits `DoxAtlasAuditResult` with field findings, evidence, objections,
  delegations, unknowns, and rationale.
- A2 emits `DelegatedRetrievalResult` with answer, verdict, retrieval summary,
  Tavily source refs, confidence, unknowns, query log, and completion flag.
- `ResolveObjectionsAndDelegations` now calls A2 for blocking A2 delegations in
  `agent_runner` mode. If A2 returns sufficient Tavily evidence, the delegation
  is completed; otherwise the workflow remains blocked.
- If unresolved objections remain after A2 retrieval, O1 gets a resolution task
  and can resolve, accept, partially accept, or reject the objection through
  structured payload fields.

## Non-goals

- No O3 trading strategy implementation.
- No O2 monitoring-policy realification beyond existing workflow behavior.
- No Supabase migration changes.
- No default real Tavily, DoxAtlas, LLM, or broker calls in tests.
