+++
kind = "internal_task_skill"
id = "expectation-detail"
name = "Expectation Detail"
version = "2026.06.01"
applicable_agents = ["O1"]
applicable_task_types = ["generate_expectation_detail"]
workflow_nodes = ["GenerateExpectationDetails"]
+++
# Expectation Detail

You are completing exactly one expectation unit from one existing expectation shell.

The current required output schema is `ExpectationDetailCandidateResult`.

You must return one JSON final_payload shaped as:

```json
{
  "candidate": {
    "document_id": "doc_<id>",
    "document_type": "expectation_unit",
    "ticker": "<ticker>",
    "created_at": "ISO-8601 timestamp",
    "updated_at": null,

    "expectation_id": "exactly the same as expectation_shell.expectation_id",
    "expectation_name": "exactly the same as expectation_shell.expectation_name",
    "direction": "exactly the same as expectation_shell.direction",
    "why_it_matters": "exactly the same as expectation_shell.why_it_matters",
    "market_view": {
      "text": "exactly preserve or faithfully extend expectation_shell.market_view.text",
      "summary": "exactly preserve or faithfully extend expectation_shell.market_view.summary",
      "evidence_refs": [],
      "author_agent": "O1",
      "reviewer_agents": []
    },

    "realized_facts": [
      {
        "event_id": "event_<id>",
        "description": "具体已发生事实：发生了什么、为什么影响该 expectation",
        "evidence_refs": [],
        "price_reaction": {
          "price_change": "具体价格变化；如果没有可靠市场数据，写明证据不足，不要编造数字",
          "price_pattern": "具体走势模式或 unknown_due_to_missing_market_data",
          "interpretation": "该反应说明市场已 price in、partly priced in，或证据不足",
          "evidence_refs": []
        }
      }
    ],

    "realized_facts_summary": "简短总结哪些事实已被市场知道、哪些可能已 price in、哪些仍不确定",

    "key_variables": [
      {
        "variable_id": "variable_<id>",
        "name": "具体变量名",
        "current_status": "当前状态，必须具体，不要泛泛写 commercialization / deployment / demand",
        "certainty": "普通短文本，说明确定性或证据缺口",
        "evidence_refs": []
      }
    ],

    "event_monitoring_direction": {
      "known_event_notice": "已知后续日期/事件；如无固定日期，明确写 no fixed known date",
      "positive_events": [
        "具体、可监测、会强化该 expectation 的事件触发条件"
      ],
      "negative_events": [
        "具体、可监测、会削弱或推翻该 expectation 的事件触发条件"
      ]
    }
  },
  "evidence_refs": [],
  "delegations": [],
  "unknowns": [],
  "rationale": "简短说明如何从 shell、Document1 context、DoxAtlas evidence 和 price context 完成该 candidate"
}
```

## Rules

1. Return exactly one `candidate`.
2. Do not return `BlackboardPatch`, `proposed_patches`, `patches`, `changes`, `path_map`, partial update, or multiple candidates.
3. Preserve `expectation_id`, `expectation_name`, `direction`, `why_it_matters`, and `market_view` from `expectation_shell`.
4. `realized_facts` must not be empty. Each fact must include evidence_refs.
5. `key_variables` must not be empty. Each variable must include evidence_refs.
6. `event_monitoring_direction.positive_events` and `negative_events` must be lists of concrete strings, not objects and not generic placeholders.
7. If market price evidence is unavailable, do not invent price numbers. State the uncertainty inside `price_reaction`.
8. If evidence is weak, write the gap in `unknowns` and `rationale`; do not fill missing evidence with generic confidence.
9. Do not add general company background that is not tied to this expectation.
