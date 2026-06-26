"""Resume one Blackboard initialization run and print a compact JSON result."""

from __future__ import annotations

import argparse
import json

from doxagent.settings import DoxAgentSettings
from doxagent.workflows import BlackboardInitializationWorkflow


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_id")
    args = parser.parse_args()

    workflow = BlackboardInitializationWorkflow(
        execution_mode="agent_runner",
        settings=DoxAgentSettings(),
    )
    result = workflow.resume_latest(args.run_id)
    print(
        json.dumps(
            {
                "status": result.status.value,
                "run_id": result.checkpoint.run_id,
                "next_node": result.checkpoint.next_node.value
                if result.checkpoint.next_node
                else None,
                "completed_nodes": [node.value for node in result.checkpoint.completed_nodes],
                "error": result.error,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
