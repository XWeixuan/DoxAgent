"""Factory for creating Microsoft Agent Framework agents from DoxAgent configs."""

from typing import Any

from agent_framework import Agent

from doxagent.agents.config import AgentDefinition
from doxagent.agents.runtime.chat_client import ModelGatewayChatClient


class MafAgentFactory:
    def create(
        self,
        definition: AgentDefinition,
        chat_client: ModelGatewayChatClient,
        tools: list[Any] | None = None,
    ) -> Agent:
        return Agent(
            chat_client,
            instructions=definition.runtime.role_instruction,
            id=definition.agent_name.value,
            name=definition.agent_name.value,
            description=f"DoxAgent {definition.role.value} agent.",
            tools=tools,
            additional_properties={
                "doxagent_agent_name": definition.agent_name.value,
                "doxagent_role": definition.role.value,
                "output_schema": definition.runtime.output_schema,
                "default_skill_ids": list(definition.runtime.default_skill_ids),
                "allowed_tools": list(definition.runtime.allowed_tools),
            },
        )
