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
            "This is a read-only harness capability and never changes source data."
        ),
        "input_fields": ["ref", "include_parent", "include_children"],
        "business_purpose": "Reload exact indexed or index-only source material by ref.",
        "observation_policy": "inline",
    }


def memory_action_schema() -> JsonDict:
    return {
        "synthesis_update": ["ADD：…", "REVISE S2：…", "DROP S1"],
        "research_update": [
            "OPEN：…",
            "REVISE Q2：…",
            "RESOLVE Q1",
            "MERGE Q2 Q4：…",
            "DEFER Q3：…",
        ],
        "retain_observations": [
            {"ref": "obs_tc1::/results/0", "note": "…", "reason": "…"}
        ],
    }


def maintenance_action_schema() -> JsonDict:
    return {
        "compaction_reasoning_summary": "公开的上下文维护摘要",
        "synthesis_update": ["REVISE S2：…", "DROP S4"],
        "research_update": ["MERGE Q2 Q5：…", "DEFER Q4：…"],
        "retained_observation_update": [
            {
                "ref": "obs_tc1::/results/0",
                "action": "KEEP_LOADED|INDEX_ONLY|DROP",
                "reason": "…",
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
