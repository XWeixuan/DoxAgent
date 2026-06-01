+++
kind = "prompt_block"
block_type = "workflow"
id = "workflow.initialization"
name = "Blackboard Initialization Workflow"
version = "2026.06.01"
workflow_nodes = ["BuildGlobalResearch", "GenerateExpectationUnits", "ReviewExpectationFields", "ResolveObjectionsAndDelegations", "GenerateKnownEvents", "GenerateMonitoringConfig", "GenerateMonitoringPolicy"]
+++
Follow the initialization workflow contract. Drafts enter Working Memory; stable documents enter Belief State only through validated Blackboard patches after objections and delegations are resolved.
