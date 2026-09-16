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


def test_ineligible_high_priority_waiter_does_not_starve_active_batch(monkeypatch):
    g, _ = guardian(monkeypatch)
    parent = g.handle(
        "v2-initialization",
        {
            "command": "acquire",
            "kind": "initialization",
            "identity": "parent",
            "batch": "initialization:RKLB",
        },
    )
    assert parent["ok"]
    blocked = g.handle(
        "codex-worker",
        {
            "command": "acquire",
            "kind": "codex_maintenance",
            "identity": "runtime-other",
            "batch": "maintenance:MU",
        },
    )
    assert blocked["reason"] == "HEAVY_BATCH_CONFLICT"
    same_batch = g.handle(
        "codex-worker",
        {
            "command": "acquire",
            "kind": "codex_initialization",
            "identity": "rklb-c4",
            "batch": "initialization:RKLB",
        },
    )
    assert same_batch["ok"]


def test_quota_recovery_cannot_shrink_busy_or_expand_under_pressure(monkeypatch):
    g, m = guardian(monkeypatch)
    g.low_since["v2-projector"] = 0
    assert g.peak("v2-projector")
    assert "v2-projector" not in g.low_since
    g.resize("v2-projector", 384)
    g.containers["v2-scheduler"].update(limit=2048 * m.MIB, current=1100 * m.MIB)
    assert not g.resize("v2-scheduler", 1024)
    assert g.containers["v2-scheduler"]["limit"] == 2048 * m.MIB
    g.metrics["swapping"] = True
    assert not g.peak("v2-projector")
    assert not g.fits(128 * m.MIB)
    assert not g.handle(
        "v2-api", {"command": "acquire", "kind": "maintenance", "identity": "spoof"}
    )["ok"]


def test_basic_projection_earmark_survives_resident_maintenance(monkeypatch):
    g, m = guardian(monkeypatch)
    g.metrics.update(app_current=3331 * m.MIB, available=2657 * m.MIB)
    g.work = {
        "daily": {
            "service": "v2-scheduler",
            "identity": "daily",
            "bytes": 512 * m.MIB,
            "heavy": True,
            "batch": "maintenance:MU",
        },
        "o2": {
            "service": "codex-worker",
            "identity": "o2",
            "bytes": 1024 * m.MIB,
            "heavy": True,
            "batch": "maintenance:MU",
        },
    }
    g.waiting[("codex-worker", "waiting")] = (1, time.monotonic() + 10)
    assert not g.fits(128 * m.MIB)
    result = g.handle(
        "v2-projector",
        {
            "command": "acquire",
            "kind": "projection",
            "identity": "batch1",
        },
    )
    assert result["ok"]
    assert g.containers["v2-projector"]["limit"] == 384 * m.MIB
    assert "v2-projector" not in g.leases
    assert not g.handle(
        "v2-projector",
        {
            "command": "acquire",
            "kind": "projection",
            "identity": "batch2",
        },
    )["ok"]
    g.handle("v2-projector", {"command": "release", "token": result["token"]})
    g.metrics["available"] = 1100 * m.MIB
    assert not g.handle(
        "v2-projector",
        {
            "command": "acquire",
            "kind": "projection",
            "identity": "unsafe",
        },
    )["ok"]
    g.metrics.update(available=2657 * m.MIB, pressure=1)
    assert not g.fits(0, basic_projection=True)


def test_other_work_cannot_spend_projection_earmark(monkeypatch):
    g, m = guardian(monkeypatch)
    g.metrics.update(app_current=5000 * m.MIB, available=5000 * m.MIB)
    assert not g.handle(
        "codex-worker",
        {
            "command": "acquire",
            "kind": "codex_runtime",
            "identity": "slot",
        },
    )["ok"]


def test_projector_peak_and_projection_earmark_are_not_double_reserved(monkeypatch):
    g, m = guardian(monkeypatch)
    g.metrics.update(app_current=3310 * m.MIB, available=4000 * m.MIB)
    g.work = {
        "parent": {
            "service": "v2-initialization",
            "identity": "parent",
            "bytes": 512 * m.MIB,
            "heavy": True,
            "batch": "initialization:RKLB",
        }
    }
    g.containers["v2-projector"].update(limit=640 * m.MIB, current=145 * m.MIB)

    # 3310 resident + 512 parent + max(128 projection reserve, 256 peak
    # borrowing) + 1024 Codex work = 5102 MiB. The old additive accounting
    # produced 5230 MiB and falsely blocked an otherwise safe admission.
    result = g.handle(
        "codex-worker",
        {
            "command": "acquire",
            "kind": "codex_initialization",
            "identity": "c3",
            "batch": "initialization:RKLB",
        },
    )
    assert result["ok"]


def test_observed_service_growth_reduces_only_its_remaining_reservation(monkeypatch):
    g, m = guardian(monkeypatch)
    g.metrics.update(app_current=4200 * m.MIB, working_current=3000 * m.MIB,
                     inactive_file=1200 * m.MIB, available=3500 * m.MIB)
    g.containers["v2-initialization"] = {
        "limit": 640 * m.MIB,
        "current": 300 * m.MIB,
    }
    g.work = {
        "parent": {
            "service": "v2-initialization",
            "identity": "parent",
            "bytes": 512 * m.MIB,
            "baseline_current": 200 * m.MIB,
            "heavy": True,
            "batch": "initialization:RKLB",
        }
    }
    budget = g.budget(1024 * m.MIB)
    assert budget["raw_current_mib"] == 4200
    assert budget["working_current_mib"] == 3000
    assert budget["outstanding_by_service_mib"]["v2-initialization"] == 412
    assert budget["projected_mib"] == 4564
    assert budget["ok"]


def test_peak_limit_is_not_usage_and_only_active_unused_headroom_is_reserved(monkeypatch):
    g, m = guardian(monkeypatch)
    g.containers["v2-projector"].update(limit=640 * m.MIB, current=500 * m.MIB)
    assert g.budget(0)["borrowed_mib"] == 0
    g.leases["v2-projector"] = time.monotonic() + 60
    # 140 MiB remains below the peak; 128 MiB overlaps the fixed projection reserve.
    assert g.budget(0)["borrowed_mib"] == 12


def test_application_slice_keeps_one_gib_host_margin():
    root = Path(__file__).resolve().parents[1]
    text = (root / "deploy/doxagent-app.slice").read_text()
    assert "MemoryHigh=6144M" in text
    assert "MemoryMax=6656M" in text
    assert "MemorySwapMax=512M" in text
