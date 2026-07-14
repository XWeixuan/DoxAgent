+++
kind = "internal_task_skill"
id = "resolve-monitoring-config"
name = "Resolve Monitoring Config"
version = "2026.07.15"
applicable_agents = ["O2"]
applicable_task_types = ["resolve_monitoring_config"]
workflow_nodes = ["ResolveMonitoringConfig"]
+++
# O2 Resolve Monitoring Config

Revise the current Monitoring Config only to resolve the supplied reviewer findings.

Preserve valid existing coverage. Return a complete revised document, not a patch and not a research note. Keep every `tool_input` API-valid for its source.

## Required schema

```json
{
  "document_id": "string",
  "document_type": "monitoring_config",
  "ticker": "string",
  "created_at": "ISO-8601 string",
  "monitoring_items": [
    {
      "item_id": "stable unique id, e.g. mi_001",
      "tool_input": {
        "ticker": "string",
        "source_id": "string",
        "enabled": true,
        "mode": "merge",
        "reason": "string"
      },
      "reasoning": "string",
      "priority": "string",
      "trigger_condition": "string"
    }
  ]
}
```

Return only `MonitoringConfigDocument`.
