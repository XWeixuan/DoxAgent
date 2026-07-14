"""Agent-facing task-memory protocol declarations."""

from __future__ import annotations

from typing import Any

JsonDict = dict[str, Any]

READ_OBSERVATION_TOOL_NAME = "read_observation"


def read_observation_descriptor() -> JsonDict:
    return {
        "name": READ_OBSERVATION_TOOL_NAME,
        "description": (
            "Read an exact Observation Block already created inside this AgentTask. "
            "When alias comes from group_catalog, load the complete catalog group. "
            "This is a read-only harness capability and never changes source data."
        ),
        "input_fields": ["alias", "include_parent", "include_children"],
        "input_schema": {
            "type": "object",
            "properties": {
                "alias": {
                    "type": "string",
                    "description": (
                        "A real O# from group_catalog or block_index. A catalog alias "
                        "loads the complete group; an index alias loads one exact block."
                    ),
                },
                "include_parent": {"type": "boolean", "default": False},
                "include_children": {"type": "boolean", "default": False},
            },
            "required": ["alias"],
            "additionalProperties": False,
        },
        "business_purpose": (
            "Reload a complete catalog group or one exact indexed source block by O# alias."
        ),
        "observation_policy": "inline",
    }


def memory_action_schema() -> JsonDict:
    return {
        "synthesis_update": ["ADD：结论【cite:O1】"],
        "research_update": ["OPEN：待研究问题"],
        "retain_observations": [{"alias": "O1", "note": "材料内容"}],
        "tool_calls": [
            {
                "tool_name": "read_observation",
                "input": {
                    "alias": "O1",
                    "include_parent": False,
                    "include_children": False,
                },
            }
        ],
    }


def maintenance_action_schema() -> JsonDict:
    return {
        "compaction_reasoning_summary": "公开的上下文维护摘要",
        "synthesis_update": ["REVISE S2：…", "DROP S4"],
        "research_update": ["MERGE Q2 Q5：…", "DEFER Q4：…"],
        "retained_observation_update": [
            {
                "alias": "O1",
                "action": "KEEP_LOADED|INDEX_ONLY|DROP",
            }
        ],
        "plan_update": ["…"],
    }


__all__ = [
    "READ_OBSERVATION_TOOL_NAME",
    "maintenance_action_schema",
    "memory_action_schema",
    "read_observation_descriptor",
]
