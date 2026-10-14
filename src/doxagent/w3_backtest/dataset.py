"""Portable historical scenarios; acquisition is read-only and separate from replay."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

DATASET_ID = "w3_history_20261014"
DEFAULT_ROOT = Path("eval/w3_backtest/datasets") / DATASET_ID


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def focus_selection(export_root="eval/w3_trade_intent_review_20261010/revised_export"):
    selection = []
    for path in sorted(Path(export_root).glob("*_*.json")):
        bundle = read_json(path)
        if "records" not in bundle:
            continue
        for n, case in enumerate(bundle["records"], 1):
            selection.append(
                dict(
                    case_key=f"{bundle['ticker']}-{bundle['part']:02d}-{n:02d}",
                    case_id=case["case_id"],
                    group="reviewed",
                )
            )
    selection.extend(
        [
            dict(
                case_key="EXTRA-INTC-OPENAI",
                case_id="case_1514e9e49e654055ab92d1428ec9afaf",
                group="extra",
            ),
            dict(
                case_key="EXTRA-INTC-TERAFAB",
                case_id="case_9dd6c3ac9c8a4796adae3f93d31d93a1",
                group="extra",
            ),
            dict(
                case_key="EXTRA-RKLB-SYNSPECTIVE",
                case_id="case_cb774ab16f154c5c9535f8453b99ff86",
                group="extra",
            ),
        ]
    )
    return selection


def acquire_remote(root=DEFAULT_ROOT, ssh_host="doxagent-sg"):
    root = Path(root)
    script = Path(__file__).with_name("remote_export.py").read_text(encoding="utf-8")
    for placeholder in ("'__SELECTION_JSON__'", '"__SELECTION_JSON__"'):
        script = script.replace(placeholder, repr(json.dumps(focus_selection())))
    script = script.replace("/var/lib/docker/volumes/doxagent-v2_v2-data/_data", "/data")
    target = root / "preparation" / "remote_export.jsonl"
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("wb") as stream:
        result = subprocess.run(
            [
                "ssh",
                "-o",
                "BatchMode=yes",
                ssh_host,
                "sudo -n docker exec -i doxagent-v2-v2-scheduler-1 python -",
            ],
            input=script.encode(),
            stdout=stream,
            stderr=subprocess.PIPE,
        )
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors="replace"))
    return target


def sanitized_case(original):
    """Allowlist before model preparation; old outcomes never enter case inputs."""
    keys = (
        "case_id",
        "trading_date",
        "source",
        "version_pin",
        "runtime_mode",
        "closed_cycle_id",
        "w1_final",
        "w1_extraction",
        "w2_final",
        "w2_alignment",
        "first_round_tags",
        "created_at",
    )
    safe = {key: original[key] for key in keys if key in original}
    frozen = original.get("frozen_inputs", {})
    safe["frozen_inputs"] = {
        key: frozen[key] for key in ("index", "projection", "provisional") if key in frozen
    }
    return safe


def materialize_export(root=DEFAULT_ROOT, export_path=None):
    root = Path(root)
    export_path = Path(export_path) if export_path else root / "preparation/remote_export.jsonl"
    text = export_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if text and not text.endswith("\n"):
        lines = lines[:-1]  # Allow consuming completed rows while SSH is still streaming.
    rows = [json.loads(line) for line in lines if line.strip()]
    history_cache = root / "preparation/history_intents.jsonl"
    history_records = {}
    if history_cache.exists():
        history_records = {
            row["value"]["case_id"]: row["value"]
            for row in (
                json.loads(line) for line in history_cache.read_text(encoding="utf-8").splitlines()
            )
        }
    selection = next(row for row in rows if row["kind"] == "selection")
    write_json(root / "preparation/selection.json", selection)
    cases = []
    for row in rows:
        if row["kind"] == "execution_window":
            write_json(root / "research/execution_window.json", row)
        elif row["kind"] == "research":
            folder = root / "research" / row["research_key"]
            folder.mkdir(parents=True, exist_ok=True)
            for key, name in (
                ("document1", "document1.md"),
                ("document2", "document2.json"),
                ("reference_view", "reference_view.md"),
                ("known_event_index", "known_event_index.md"),
            ):
                (folder / name).write_text(row[key], encoding="utf-8")
            write_json(folder / "policy_set.original.json", row["policy_set"])
            from .legacy import LegacyReplayAdapter

            write_json(
                folder / "policy_execution_view.json",
                LegacyReplayAdapter.policy_execution_view(row["policy_set"]),
            )
            write_json(folder / "events.json", row["events"])
            write_json(
                folder / "manifest.json",
                {key: row[key] for key in ("research_key", "ticker", "version_pin")},
            )
        elif row["kind"] == "case":
            original = row["case"]
            case_key = row["selection"]["case_key"]
            safe = sanitized_case(original)
            case = dict(
                schema_version="w3-backtest.case.v1",
                case_key=case_key,
                group=row["selection"]["group"],
                original_case_id=original["case_id"],
                ticker=original["ticker"],
                evidence_cutoff_at=row["evidence_cutoff_at"],
                cutoff_basis=row["cutoff_basis"],
                research_key=row["research_key"],
                research_path=f"research/{row['research_key']}",
                original_case=safe,
                **{
                    key: safe.get(key)
                    for key in (
                        "source",
                        "w1_final",
                        "w2_final",
                        "w2_alignment",
                        "version_pin",
                        "runtime_mode",
                        "trading_date",
                    )
                },
            )
            write_json(root / "inputs" / case_key / "case.json", case)
            state = row["historical_state"]
            from .legacy import LegacyReplayAdapter

            history, matches = [], []
            for item in state.get("recent_trade_intents", []):
                value = history_records.get(item["case_id"], item)
                projected, match = LegacyReplayAdapter.historical_intent(value)
                history.append(projected)
                matches.append(match)
            state.update(recent_trade_intents=history, history_matches=matches)
            prior_path = root / "inputs" / case_key / "historical_state.json"
            if "event_details" not in state and prior_path.exists():
                prior = read_json(prior_path)
                if prior.get("event_details"):
                    state["event_details"] = prior["event_details"]
            state["global_state"] = {
                "watches": state.get("watches", []),
                "pins": state.get("pins", []),
            }
            write_json(root / "inputs" / case_key / "historical_state.json", state)
            write_json(
                root / "preparation/original_outcomes" / f"{case_key}.json",
                dict(original_case=original, original_w3=row.get("original_w3")),
            )
            cases.append(case)
    # The NASA synthetic case uses the nearest pre-cutoff RKLB activation, not a future policy set.
    nasa_t0 = "2026-10-06T12:05:00+00:00"
    eligible = [c for c in cases if c["ticker"] == "RKLB" and c["evidence_cutoff_at"] < nasa_t0]
    if not eligible:
        raise ValueError("No pre-cutoff RKLB research for NASA scenario")
    base = max(eligible, key=lambda c: c["evidence_cutoff_at"])
    nasa = dict(
        schema_version="w3-backtest.case.v1",
        case_key="EXTRA-RKLB-NASA",
        group="extra",
        original_case_id=None,
        ticker="RKLB",
        evidence_cutoff_at=nasa_t0,
        cutoff_basis="synthetic_publication_plus_5_minutes",
        research_key=base["research_key"],
        research_path=base["research_path"],
        version_pin=base["version_pin"],
        runtime_mode="REALTIME",
        trading_date="2026-10-06",
        source=None,
        w1_final=None,
        w2_final=None,
        w2_alignment=None,
        original_case=None,
        preparation_status="NEEDS_NASA_W1_W2",
        research_basis_case=base["case_key"],
    )
    nasa_path = root / "inputs/EXTRA-RKLB-NASA/case.json"
    if nasa_path.exists():
        existing = read_json(nasa_path)
        if existing.get("w1_final") and existing.get("w2_final"):
            nasa = existing
    write_json(nasa_path, nasa)
    nasa_state = root / "inputs/EXTRA-RKLB-NASA/historical_state.json"
    if not nasa_state.exists():
        write_json(
            nasa_state, read_json(root / "inputs" / base["case_key"] / "historical_state.json")
        )
    cases.insert(47, nasa)
    (root / "cases.jsonl").write_text(
        "".join(
            json.dumps(
                {
                    k: c.get(k)
                    for k in (
                        "case_key",
                        "group",
                        "ticker",
                        "original_case_id",
                        "evidence_cutoff_at",
                        "research_key",
                    )
                },
                ensure_ascii=False,
            )
            + "\n"
            for c in cases
        ),
        encoding="utf-8",
    )
    manifest = dict(
        schema_version="w3-backtest.dataset.v1",
        dataset_id=DATASET_ID,
        seed=20261014,
        case_count=len(cases),
        cases=[
            dict(case_key=c["case_key"], path=f"inputs/{c['case_key']}/case.json", group=c["group"])
            for c in cases
        ],
        groups={g: sum(c["group"] == g for c in cases) for g in ("reviewed", "extra", "noise")},
        preparation_status="EVIDENCE_PREPARATION",
        time_basis="original W3 start, W3 route entry fallback; NASA synthetic publication+5m",
    )
    write_json(root / "manifest.json", manifest)
    return manifest


def load_cases(root=DEFAULT_ROOT, case_key=None):
    root = Path(root)
    manifest = read_json(root / "manifest.json")
    return [
        read_json(root / item["path"])
        for item in manifest["cases"]
        if not case_key or item["case_key"] == case_key
    ]


def input_fingerprint(root=DEFAULT_ROOT):
    root = Path(root)
    digest = hashlib.sha256()
    for prefix in ("inputs", "research"):
        for path in sorted((root / prefix).rglob("*")):
            if path.is_file():
                digest.update(path.relative_to(root).as_posix().encode())
                digest.update(path.read_bytes())
    return digest.hexdigest()
