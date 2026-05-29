"""Run the Phase 6 mock ticker vertical slice."""

import argparse
import json
from pathlib import Path
from typing import Any

from doxagent.examples.phase6_exporter import compact_summary, export_phase6_run
from doxagent.workflows import BlackboardInitializationWorkflow

DEFAULT_FIXTURE_PATH = Path("examples/phase6_mock_ticker/fixture.json")


def run_sample(fixture_path: Path = DEFAULT_FIXTURE_PATH) -> dict[str, Any]:
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    ticker = str(fixture["ticker"])
    workflow = BlackboardInitializationWorkflow()
    result = workflow.run(ticker)
    blackboard_run = workflow.blackboard.get_run(result.checkpoint.run_id)
    return export_phase6_run(
        workflow_result=result,
        blackboard_run=blackboard_run,
        fixture=fixture,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fixture",
        type=Path,
        default=DEFAULT_FIXTURE_PATH,
        help="Path to the Phase 6 fixture JSON.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional path for the generated review JSON.",
    )
    args = parser.parse_args(argv)

    exported = run_sample(args.fixture)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(exported, indent=2) + "\n",
            encoding="utf-8",
        )
    else:
        print(json.dumps(compact_summary(exported), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
