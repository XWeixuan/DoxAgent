import importlib.util
import json
import time
from pathlib import Path

from doxagent.persistent_runtime_v2.bounded_inputs import members, records, unfinished
from doxagent.persistent_runtime_v2.journal import RuntimeJournal
from doxagent.persistent_runtime_v2.repository import SQLitePersistentRuntimeV2Repository


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
                identity,
                "std_" + identity,
                "MU",
                day,
                "PROCESSING",
                "RUNNING",
                payload,
                "2026-09-14T07:00:00+00:00",
                "2026-09-14T07:00:00+00:00",
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
        "candidates": [],
        "trades": [],
        "badcases": [],
        "gaps": [],
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
        "inbox:MU:today",
        "MU",
        "CASE",
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


def guardian(monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "resource_guardian", Path(__file__).resolve().parents[1] / "scripts/resource_guardian.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "docker", lambda *args: "")
    g = module.Guardian()
    g.work = {}
    g.leases = {}
    g.metrics = {
        "app_current": 2000 * module.MIB,
        "available": 4000 * module.MIB,
        "at": time.time(),
        "swapping": False,
        "pressure": 0,
    }
    g.containers = {
        s: {"limit": d * module.MIB, "current": 100 * module.MIB}
        for s, (d, _, _) in module.QUOTAS.items()
    }
    return g, module


def test_resource_budget_wait_and_heavy_batch_isolation(monkeypatch):
    g, m = guardian(monkeypatch)
    first = g.handle(
        "v2-scheduler",
        {
            "command": "acquire",
            "kind": "maintenance",
            "identity": "first",
            "batch": "maintenance:MU",
        },
    )
    assert first["ok"]
    second = g.handle(
        "codex-worker",
        {
            "command": "acquire",
            "kind": "codex_maintenance",
            "identity": "o2",
            "batch": "maintenance:MU",
        },
    )
    assert second["ok"]
    assert not g.handle(
        "codex-worker",
        {
            "command": "acquire",
            "kind": "codex_initialization",
            "identity": "init",
            "batch": "init:NVDA",
        },
    )["ok"]
    assert not g.handle(
        "codex-worker", {"command": "acquire", "kind": "codex_runtime", "identity": "second-slot"}
    )["ok"]
    assert g.containers["v2-scheduler"]["limit"] == 2048 * m.MIB


def test_quota_recovery_cannot_shrink_busy_or_expand_under_pressure(monkeypatch):
    g, m = guardian(monkeypatch)
    g.containers["v2-scheduler"].update(limit=2048 * m.MIB, current=1100 * m.MIB)
    assert not g.resize("v2-scheduler", 1024)
    assert g.containers["v2-scheduler"]["limit"] == 2048 * m.MIB
    g.metrics["swapping"] = True
    assert not g.peak("v2-projector")
    assert not g.fits(128 * m.MIB)
    assert not g.handle(
        "v2-api", {"command": "acquire", "kind": "maintenance", "identity": "spoof"}
    )["ok"]
