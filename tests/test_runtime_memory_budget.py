import importlib.util
import json
import sys
from pathlib import Path

from doxagent.persistent_runtime_v2.bounded_inputs import members, records, unfinished
from doxagent.persistent_runtime_v2.journal import RuntimeJournal
from doxagent.persistent_runtime_v2.repository import SQLitePersistentRuntimeV2Repository
from doxagent.resource_safety import SafetyLevel, SafetyStateReader


def setup_scope(tmp_path):
    journal = RuntimeJournal(tmp_path / "runtime.db")
    repo = SQLitePersistentRuntimeV2Repository(journal.path)
    task = {
        "id": "daily:MU:2026-09-14",
        "ticker": "MU",
        "inputs": {"scope": "DAILY", "day": "2026-09-14", "cutoff": "2026-09-15T06:00:00+00:00"},
    }
    return repo, journal, task


def case(repo, identity, *, day="2026-09-14", sweep=None):
    payload = json.dumps(
        {"sweep_id": sweep, "source": {"$doxagent_content_v1": "missing", "bytes": 900000}}
    )
    with repo._connect() as db:
        db.execute(
            "insert into runtime_v2_cases values(?,?,?,?,?,?,?,?,?)",
            (
                identity, "std_" + identity, "MU", day, "PROCESSING", "RUNNING", payload,
                "2026-09-14T07:00:00+00:00", "2026-09-14T07:00:00+00:00",
            ),
        )


def test_wait_and_membership_do_not_hydrate_bodies(tmp_path, monkeypatch):
    repo, journal, task = setup_scope(tmp_path)
    for n in range(220):
        case(repo, str(n), day="2026-09-12")
    case(repo, "target")

    def forbidden(*args):
        raise AssertionError("article body hydration is forbidden")

    monkeypatch.setattr(repo.content, "text", forbidden)
    assert not unfinished(repo, journal, task)
    selected = members(repo, journal, task)
    assert [c["case_id"] for c in selected] == ["target"]
    case(repo, "late")
    assert members(repo, journal, task) == selected
    assert records(repo, selected, ticker="MU") == {
        "candidates": [], "trades": [], "badcases": [], "gaps": []
    }


def test_pending_other_day_sweep_and_invalid_admission_are_not_global_gates(tmp_path):
    repo, journal, task = setup_scope(tmp_path)
    for name, at, sweep in [
        ("old", "2026-09-13T09:00:00+00:00", None),
        ("sweep", "2026-09-14T09:00:00+00:00", "weekend"),
    ]:
        journal.put_task("inbox:MU:" + name, "MU", "CASE", {"admitted_at": at, "sweep_id": sweep})
    assert not unfinished(repo, journal, task)
    journal.put_task(
        "inbox:MU:today", "MU", "CASE",
        {"admitted_at": "2026-09-14T09:00:00+00:00", "sweep_id": None},
    )
    assert unfinished(repo, journal, task)
    journal.set("invalid_admissions", "today", {"excluded": True})
    assert not unfinished(repo, journal, task)


def test_explicit_compensation_and_sweep_scope(tmp_path):
    repo, journal, task = setup_scope(tmp_path)
    case(repo, "old", day="2026-09-12", sweep="weekend")
    case(repo, "today")
    with repo._connect() as db:
        db.execute(
            "insert into runtime_v2_effects values(?,?,?,?,?,?,?,?,?)",
            ("effect", "old", "NOOP", "key", "PENDING", 0, "2026-09-14", "{}", "2026-09-14"),
        )
    assert not unfinished(repo, journal, task)
    task["inputs"]["case_ids"] = ["old"]
    assert unfinished(repo, journal, task)
    assert [c["case_id"] for c in members(repo, journal, task)] == ["old"]
    task["inputs"]["case_ids"] = []
    assert not unfinished(repo, journal, task)
    sweep = {**task, "id": "sweep-maint", "inputs": {"scope": "SWEEP", "sweep_id": "weekend"}}
    assert unfinished(repo, journal, sweep)


def test_missing_safety_controller_fails_open():
    snapshot = SafetyStateReader(Path("missing-safety-state.json")).read()
    assert snapshot.level is SafetyLevel.NORMAL
    assert snapshot.stale


def _safety_module():
    path = Path(__file__).resolve().parents[1] / "scripts/resource_safety_controller.py"
    spec = importlib.util.spec_from_file_location("resource_safety_controller", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_safety_controller_uses_actual_pressure_and_hysteresis(tmp_path):
    module = _safety_module()
    controller = module.SafetyController(tmp_path, tmp_path / "safety.json")
    healthy = module.Sample(
        observed_at=1, memory_current=10 * module.GIB, memory_max=15 * module.GIB,
        memory_swap_current=0, host_available=4 * module.GIB,
        psi_some_avg10=0, psi_full_avg10=0, swap_io_bytes_per_minute=0,
        high_events=0, oom_events=0,
    )
    assert controller.evaluate(healthy, now=0)[0] == "NORMAL"
    high_without_pressure = module.Sample(
        observed_at=2, memory_current=int(14.5 * module.GIB),
        memory_max=15 * module.GIB, memory_swap_current=0,
        host_available=2 * module.GIB, psi_some_avg10=0, psi_full_avg10=0,
        swap_io_bytes_per_minute=0, high_events=0, oom_events=0,
    )
    assert controller.evaluate(high_without_pressure, now=1)[0] == "NORMAL"
    pressure = module.Sample(
        observed_at=3, memory_current=int(14.2 * module.GIB),
        memory_max=15 * module.GIB, memory_swap_current=0,
        host_available=900 * module.MIB, psi_some_avg10=6, psi_full_avg10=3,
        swap_io_bytes_per_minute=0, high_events=1, oom_events=0,
    )
    assert controller.evaluate(pressure, now=2)[0] == "NORMAL"
    assert controller.evaluate(pressure, now=17)[0] == "PRESSURE"
    assert controller.evaluate(healthy, now=20)[0] == "PRESSURE"
    assert controller.evaluate(healthy, now=80)[0] == "NORMAL"
    critical = module.Sample(
        observed_at=4, memory_current=int(14.8 * module.GIB),
        memory_max=15 * module.GIB, memory_swap_current=0,
        host_available=2 * module.GIB, psi_some_avg10=0, psi_full_avg10=0,
        swap_io_bytes_per_minute=0, high_events=1, oom_events=0,
    )
    assert controller.evaluate(critical, now=81)[0] == "NORMAL"
    assert controller.evaluate(critical, now=86)[0] == "CRITICAL"


def test_application_slice_uses_approved_4c16g_boundary():
    root = Path(__file__).resolve().parents[1]
    text = (root / "deploy/doxagent-app.slice").read_text()
    assert "MemoryHigh=14G" in text
    assert "MemoryMax=15G" in text
    assert "MemorySwapMax=1G" in text
