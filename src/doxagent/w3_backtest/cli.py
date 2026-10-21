"""Local-only W3 evaluation commands. No production scheduler integration."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path("eval/w3_backtest")


def dataset_path(value: str) -> Path:
    path = Path(value)
    return (
        path.resolve()
        if path.exists() or path.is_absolute()
        else (ROOT / "datasets" / value).resolve()
    )


def run_path(value: str) -> Path:
    path = Path(value)
    return (
        path.resolve() if path.exists() or path.is_absolute() else (ROOT / "runs" / value).resolve()
    )


def build_dataset(dataset: Path) -> dict:
    from .dataset import input_fingerprint, load_cases, read_json, write_json
    from .gold import import_gold

    cases = load_cases(dataset)
    stats = dict(cases=len(cases), web_available=0, prices_available=0, incomplete=[])
    for case in cases:
        key = case["case_key"]
        folder = dataset / "inputs" / key / "evidence"
        folder.mkdir(parents=True, exist_ok=True)
        web = folder / "web_search.jsonl"
        price = folder / "price_10m.json"
        available_web = web.exists() and bool(web.read_text(encoding="utf-8").strip())
        available_price = price.exists() and read_json(price).get("availability") == "AVAILABLE"
        review_path = dataset / "preparation" / "web_review" / f"{key}.json"
        review = read_json(review_path) if review_path.exists() else {}
        historical = read_json(dataset / "inputs" / key / "historical_state.json")
        stats["web_available"] += available_web
        stats["prices_available"] += available_price
        if not (available_web and available_price and case.get("original_case")):
            stats["incomplete"].append(
                dict(
                    case_key=key,
                    web=available_web,
                    prices=available_price,
                    upstream=bool(case.get("original_case")),
                )
            )
        write_json(
            folder / "index.json",
            dict(
                schema_version="w3-backtest.evidence.v1",
                cutoff_at=case["evidence_cutoff_at"],
                web_search=dict(
                    path="web_search.jsonl",
                    availability="AVAILABLE" if available_web else "UNAVAILABLE",
                ),
                prices=dict(
                    ten_minute_path="price_10m.json",
                    daily_path="price_daily.json",
                    availability="AVAILABLE" if available_price else "UNAVAILABLE",
                ),
                web_gaps=review.get("gaps", []),
                historical_state_limitations=historical.get("limitations", []),
                instruction=(
                    "Precollected historical evidence. Read files; do not call Web or IBKR."
                ),
            ),
        )
    if not (dataset / "gold.jsonl").exists():
        stats["gold"] = import_gold(dataset)
    else:
        from .report import read_jsonl

        gold = read_jsonl(dataset / "gold.jsonl")
        reviewed = sum(bool(g.get("gold_source") or g.get("expected_action")) for g in gold)
        stats["gold"] = dict(
            cases=len(gold),
            reviewed=reviewed,
            scorable=sum(bool(g.get("expected_action")) for g in gold),
            unreviewed=len(gold) - reviewed,
        )
    manifest = read_json(dataset / "manifest.json")
    manifest.update(
        preparation_status="READY" if not stats["incomplete"] else "READY_WITH_GAPS",
        preparation_summary=stats,
        input_fingerprint=input_fingerprint(dataset),
    )
    write_json(dataset / "manifest.json", manifest)
    return manifest


def execute_run(
    dataset: Path, root: Path, *, keys=None, concurrency=4, prompt_root=None,
    timeout_seconds=1800, model=None, effort=None
) -> dict:
    from .dataset import input_fingerprint, load_cases, read_json, write_json
    from .report import render_report
    from .runner import run_case

    if effort is not None and model is None:
        raise ValueError("--effort requires --model")
    model_profile = dict(model=model, effort=effort or "medium") if model else None
    if not 1 <= concurrency <= 16:
        raise ValueError("concurrency must be between 1 and 16")
    cases = load_cases(dataset)
    available = {c["case_key"] for c in cases}
    if keys and set(keys) - available:
        raise ValueError(f"unknown case keys: {sorted(set(keys) - available)}")
    selected = [c for c in cases if not keys or c["case_key"] in keys]
    prompts = Path(prompt_root or "prompts/persistent_runtime_v2/w3").resolve()
    prompt_files = ["AGENTS.md", "skills/skill.md"]
    if (prompts / "contract.json").exists():
        # The schema marker is part of the frozen prompt pack, not a new capability.
        prompt_files.append("contract.json")
    prompt_hashes = {
        name: hashlib.sha256((prompts / name).read_bytes()).hexdigest()
        for name in prompt_files
    }
    signature = input_fingerprint(dataset)
    try:
        revision = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
        dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], text=True).strip())
    except (OSError, subprocess.CalledProcessError):
        revision, dirty = None, None
    code_hash = hashlib.sha256()
    for folder in (
        Path(__file__).parent,
        Path("src/doxagent/persistent_runtime_v2"),
        Path("src/doxagent/codex_worker"),
    ):
        for file in sorted(folder.glob("*.py")):
            if folder == Path(__file__).parent and file.name not in {"runner.py", "legacy.py"}:
                # Preparation and report changes do not change frozen model
                # execution; prepared input bytes have their own fingerprint.
                continue
            code_hash.update(file.name.encode())
            code_hash.update(file.read_bytes())
    identity = dict(
        dataset_path=str(dataset),
        dataset_fingerprint=signature,
        prompt_hashes=prompt_hashes,
        code_fingerprint=code_hash.hexdigest(),
        case_keys=[c["case_key"] for c in selected],
        model_profile=model_profile,
    )
    marker = root / "run_manifest.json"
    if marker.exists():
        previous = read_json(marker)
        if any(previous.get(k) != v for k, v in identity.items()):
            raise ValueError("Dataset inputs, prompts or code changed; use a new --run-id")
    else:
        write_json(
            marker,
            dict(
                **identity,
                run_id=root.name,
                started_at=datetime.now(UTC).isoformat(),
                git_revision=revision,
                git_dirty=dirty,
                concurrency=concurrency,
                prompt_root=str(prompts),
                timeout_seconds=timeout_seconds,
                environment=dict(
                    web_search="disabled",
                    data_mcp_enabled=False,
                    ibkr_api=False,
                    subagents=False,
                    os_sandbox="full-access" if os.name == "nt" else "read-only",
                    filesystem_contract=(
                        "case-local read-only task; Windows executable file tools enabled"
                    ),
                ),
            ),
        )
    frozen_prompts = root / "prompt_snapshot"
    for name, expected in prompt_hashes.items():
        content = (prompts / name).read_bytes()
        if hashlib.sha256(content).hexdigest() != expected:
            raise ValueError("Prompts changed while starting the run; start again")
        target = frozen_prompts / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_bytes(content)
        elif hashlib.sha256(target.read_bytes()).hexdigest() != expected:
            raise ValueError("Frozen run prompts were modified; use a new --run-id")
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        work = {
            pool.submit(
                run_case,
                dataset,
                c["case_key"],
                root,
                prompt_root=frozen_prompts,
                timeout_seconds=timeout_seconds,
                model=model,
                effort=effort,
            ): c
            for c in selected
            if not (
                (root / "cases" / c["case_key"] / "result.json").exists()
                and read_json(root / "cases" / c["case_key"] / "result.json").get("status")
                == "SUCCEEDED"
            )
        }
        for future in as_completed(work):
            case = work[future]
            try:
                result = future.result()
            except Exception as exc:
                result = dict(
                    case_key=case["case_key"],
                    ticker=case["ticker"],
                    status="INPUT_ERROR",
                    error=dict(type=type(exc).__name__, message=str(exc)),
                    decision=None,
                    operations=None,
                    cutoff_at=case["evidence_cutoff_at"],
                    started_at=datetime.now(UTC).isoformat(),
                    finished_at=datetime.now(UTC).isoformat(),
                )
                write_json(root / "cases" / case["case_key"] / "result.json", result)
            print(
                json.dumps(
                    dict(
                        case_key=result["case_key"],
                        status=result["status"],
                        error=result.get("error"),
                    ),
                    ensure_ascii=False,
                ),
                flush=True,
            )
    summary = render_report(root, dataset)
    manifest = read_json(marker)
    manifest.update(finished_at=datetime.now(UTC).isoformat(), summary=summary)
    write_json(marker, manifest)
    return summary


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Isolated local historical W3 replay")
    commands = root.add_subparsers(dest="command", required=True)
    for name in (
        "prepare-cases",
        "prepare-nasa",
        "prepare-search",
        "prepare-prices",
        "build",
        "run",
    ):
        command = commands.add_parser(name)
        command.add_argument("--dataset", default="w3_history_20261014")
        command.add_argument("--case", action="append", dest="case_keys")
        if name in {"prepare-cases", "prepare-prices"}:
            command.add_argument("--ssh-host", default="doxagent-sg")
        if name == "prepare-search":
            command.add_argument("--workers", type=int, default=4)
        if name == "run":
            command.add_argument("--run-id", required=True)
            command.add_argument("--concurrency", type=int, default=4)
            command.add_argument("--prompt-root")
            command.add_argument("--timeout-seconds", type=int, default=1800)
            command.add_argument("--model")
            command.add_argument("--effort", choices=("low", "medium", "high", "max"))
    for name in ("resume", "report"):
        command = commands.add_parser(name)
        command.add_argument("--run-id", required=True)
    compare = commands.add_parser("compare")
    compare.add_argument("--before", required=True)
    compare.add_argument("--after", required=True)
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    dataset = dataset_path(args.dataset) if hasattr(args, "dataset") else None
    if args.command == "prepare-cases":
        from .dataset import acquire_remote, materialize_export

        acquire_remote(dataset, args.ssh_host)
        result = materialize_export(dataset)
    elif args.command == "prepare-nasa":
        from .nasa import prepare_nasa

        result = prepare_nasa(dataset)
    elif args.command == "prepare-search":
        from .collect import prepare_search

        result = asyncio.run(prepare_search(dataset, args.case_keys, args.workers))
    elif args.command == "prepare-prices":
        from .prices import prepare_prices

        result = prepare_prices(dataset, host=args.ssh_host, case_keys=args.case_keys)
    elif args.command == "build":
        manifest = build_dataset(dataset)
        result = {
            k: manifest[k]
            for k in (
                "dataset_id",
                "preparation_status",
                "preparation_summary",
                "input_fingerprint",
            )
        }
    elif args.command == "run":
        result = execute_run(
            dataset,
            run_path(args.run_id),
            keys=args.case_keys,
            concurrency=args.concurrency,
            prompt_root=args.prompt_root,
            timeout_seconds=args.timeout_seconds,
            model=args.model,
            effort=args.effort,
        )
    elif args.command == "resume":
        root = run_path(args.run_id)
        manifest = json.loads((root / "run_manifest.json").read_text(encoding="utf-8"))
        result = execute_run(
            Path(manifest["dataset_path"]),
            root,
            keys=manifest["case_keys"],
            concurrency=manifest["concurrency"],
            prompt_root=manifest["prompt_root"],
            timeout_seconds=manifest["timeout_seconds"],
            model=(manifest.get("model_profile") or {}).get("model"),
            effort=(manifest.get("model_profile") or {}).get("effort"),
        )
    elif args.command == "report":
        from .report import render_report

        result = render_report(run_path(args.run_id))
    else:
        from .report import compare_runs

        result = compare_runs(run_path(args.before), run_path(args.after))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
