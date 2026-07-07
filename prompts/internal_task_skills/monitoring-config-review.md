+++
kind = "internal_task_skill"
id = "monitoring-config-review"
name = "Monitoring Config Review"
version = "2026.07.07"
applicable_agents = ["C1", "C3"]
applicable_task_types = ["review_monitoring_config"]
workflow_nodes = ["ReviewMonitoringConfig"]
+++
# Monitoring Config Review

Review Monitoring Config as Message Bus coverage.

Do not redesign the config. Check whether the pending config can catch useful runtime messages.

Check:

- key expectation and Known Event update paths have source coverage
- media and social sources are used for the right purpose
- source parameters are concrete and API-ready
- ticker-only sources do not receive forced keywords
- X search terms, usernames, and RSS URLs are not broad or noisy
- each item has clear expectation or event-family linkage
- obvious source gaps are surfaced

Blocking issues:

- missing coverage for a major catalyst family
- unsupported fields inside `tool_input`
- vague search terms that create mostly noise
- fake, guessed, or non-actionable accounts / RSS URLs
- config is narrative-shaped instead of runtime-shaped

Minor optimization suggestions are non-blocking.

Follow the current required review schema. Raise concise objections only for material coverage or API-shape risks.
