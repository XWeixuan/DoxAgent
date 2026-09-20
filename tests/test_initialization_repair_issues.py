from __future__ import annotations

from datetime import UTC, datetime

from doxagent.initialization_repair.issues import record_agent_report, render, render_to
from doxagent.initialization_repair.repository import RepairRepository
from doxagent.initialization_repair.schema import RepairAgentReport
from doxagent.ticker_initialization import InitializationRepository, NodeSpec


def test_central_issue_ledger_is_idempotent_and_rebuildable(tmp_path):
    initialization = InitializationRepository(tmp_path / "control.sqlite3")
    run = initialization.submit("MU", datetime.now(UTC), [NodeSpec(key="a", block="D1")])
    lease = initialization.claim("worker")
    assert lease is not None
    initialization.begin(lease, "a", {})
    initialization.fail(lease, "a", "broken")
    failed = initialization.finish(lease, error="broken")
    repairs = RepairRepository(initialization)
    incident = repairs.open_incident(run.initialization_id, expected_state_seq=failed.state_seq)
    repair_round = repairs.start_round(incident.incident_id, failed_state_seq=failed.state_seq)
    report = RepairAgentReport(
        root_cause="proven bug",
        evidence=["test failure"],
        changed_files={"src/example.py": "fix call path"},
        tests=[{"command": "pytest -q", "result": "passed"}],
        downstream_implications=["same helper repaired"],
        remaining_items=[],
    )
    arguments = dict(
        entry_id="entry-1",
        incident_id=incident.incident_id,
        round_id=repair_round.round_id,
        report=report,
        node_ordinals=repair_round.node_ordinals,
        ticker="MU",
        initialization_id=run.initialization_id,
        thread_id="thread",
        turn_id="turn",
        commit="commit",
        image_id="image",
    )
    assert record_agent_report(repairs, **arguments)
    assert not record_agent_report(repairs, **arguments)
    markdown = render(repairs)
    assert markdown.count("proven bug") == 1
    assert "`src/example.py`: fix call path" in markdown
    assert "Remaining items\n\n- None" in markdown
    output = tmp_path / "init-issue.md"
    render_to(repairs, output)
    assert output.read_text(encoding="utf-8") == markdown
