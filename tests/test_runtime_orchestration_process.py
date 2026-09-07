import subprocess
import sys
import time
from datetime import UTC, datetime

from doxagent.persistent_runtime_v2.operations import migrate
from doxagent.persistent_runtime_v2.schema import RuntimeCaseStatus
from tests.test_persistent_runtime_v2 import _source
from tests.test_runtime_orchestration_execution import runtime_at


def test_process_killed_between_w1_and_w2_resumes_without_repeating_w1(tmp_path):
    code = r"""
import sys, time
from pathlib import Path
from datetime import datetime, UTC
from tests.test_runtime_orchestration_execution import runtime_at
from tests.test_persistent_runtime_v2 import _source, _AnyResponses
root=Path(sys.argv[1])
runtime,journal=runtime_at(root,[datetime(2026,9,8,12,tzinfo=UTC)])
original=runtime.repository.append_turn
def append(turn):
    result=original(turn)
    if turn.lane == 'W1' and turn.round_name == 'R2':
        (root/'w1.ready').write_text('durable')
    return result
runtime.repository.append_turn=append
class Responses(_AnyResponses):
    def complete(self, request):
        if request.metadata['lane']=='W2':
            time.sleep(120)
        return super().complete(request)
runtime.responses=Responses()
runtime.execute_message(_source())
"""
    child = subprocess.Popen(
        [sys.executable, "-c", code, str(tmp_path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 30
        while not (tmp_path / "w1.ready").exists() and time.monotonic() < deadline:
            if child.poll() is not None:
                raise AssertionError(child.stderr.read().decode(errors="replace"))
            time.sleep(0.05)
        assert (tmp_path / "w1.ready").exists()
        child.kill()
        child.wait(timeout=10)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=10)
        child.stderr.close()
    runtime, journal = runtime_at(tmp_path, [datetime(2026, 9, 8, 12, tzinfo=UTC)])
    try:
        before = runtime.repository.get_case_by_source(_source().source_message_id)
        assert before.status is RuntimeCaseStatus.RUNNING
        w1 = [
            turn.turn_id
            for turn in runtime.repository.list_turns(before.case_id)
            if turn.lane == "W1"
        ]
        resumed = runtime.execute_message(_source())
        assert resumed.status is RuntimeCaseStatus.ADJUDICATED
        assert [
            turn.turn_id
            for turn in runtime.repository.list_turns(before.case_id)
            if turn.lane == "W1"
        ] == w1
        runtime.process_pending_effects()
        runtime.process_pending_effects()
        assert len(journal.values("trade_intents")) == 1
    finally:
        runtime.close()


def test_migration_dry_run_does_not_create_or_modify_database(tmp_path):
    path = tmp_path / "runtime.db"
    assert not migrate(path, dry_run=True, backup=None)["exists"]
    assert not path.exists()
    runtime, _ = runtime_at(tmp_path, [datetime(2026, 9, 8, 12, tzinfo=UTC)])
    runtime.close()
    before = path.read_bytes()
    migrate(path, dry_run=True, backup=None)
    assert path.read_bytes() == before
