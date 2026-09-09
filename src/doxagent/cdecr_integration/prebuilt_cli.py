"""Build, publish, inspect, and reconcile immutable CDECR prebuilt bundles."""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sqlite3
import subprocess
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from cdecr.config import CDECRSettings
from doxagent.cdecr_integration.coordinator import stage_historical_sources
from doxagent.cdecr_integration.historical_loader import (
    BenzingaHistoricalNewsProvider,
    FinnhubHistoricalNewsProvider,
    HistoricalNewsProvider,
)
from doxagent.cdecr_integration.prebuilt import (
    CDECRPrebuiltManifest,
    CDECRPrebuiltStore,
    PrebuiltError,
    RegistryArtifact,
    configuration_fingerprint,
    create_archive,
    export_sqlite,
    message_set_sha256,
    sha256_file,
    validate_bundle,
)
from doxagent.cdecr_integration.registry_resolver import PerTickerRegistryResolver
from doxagent.cdecr_integration.runtime_factory import build_cdecr_workflow_runner
from doxagent.settings import DoxAgentSettings


def _json(value: Any) -> None:
    print(json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2))


async def _build(args: argparse.Namespace) -> dict[str, Any]:
    cutoff = datetime.fromisoformat(args.research_cutoff_at)
    if cutoff.tzinfo is None or cutoff.utcoffset() is None:
        raise ValueError("research cutoff must include timezone")
    output_root = args.output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    work = output_root / f".build-{uuid4().hex}"
    work.mkdir()
    providers: list[HistoricalNewsProvider] = []
    app_settings = DoxAgentSettings()
    if app_settings.finnhub_api_key:
        providers.append(FinnhubHistoricalNewsProvider(app_settings))
    if app_settings.benzinga_api_key:
        providers.append(BenzingaHistoricalNewsProvider(app_settings))
    try:
        binding = PerTickerRegistryResolver(work / "registry").bind(
            market=args.market, ticker=args.ticker
        )
        registry, runner = build_cdecr_workflow_runner(binding)
        message_ids, report = await stage_historical_sources(
            binding=binding,
            registry=registry,
            staging_path=work / "historical_staging.sqlite3",
            providers=providers,
            as_of=cutoff,
        )
        result = runner.run(message_ids, as_of=cutoff)
        if result.status != "FINALIZED" or not result.epoch_id:
            raise PrebuiltError(
                "CDECR_PREBUILT_INVALID", "builder requires a genuine FINALIZED CDECR epoch"
            )
        exported = work / "exported.sqlite3"
        export_sqlite(Path(binding.registry_path), exported)
        registry_digest = sha256_file(exported)
        generated_at = datetime.now(UTC)
        stamp = cutoff.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
        bundle_id = f"cdecr-{binding.market}-{binding.ticker}-{stamp}-{registry_digest[-12:]}"
        bundle = output_root / bundle_id
        if bundle.exists():
            raise FileExistsError(bundle)
        bundle.mkdir()
        shutil.move(exported, bundle / "runtime.sqlite3")
        sorted_ids = sorted(set(result.message_ids))
        manifest = CDECRPrebuiltManifest(
            bundle_id=bundle_id,
            market=binding.market,
            ticker=binding.ticker,
            runtime_scope=binding.runtime_scope,
            research_cutoff_at=cutoff,
            generated_at=generated_at,
            code_revision=_code_revision(),
            compatibility_version=args.compatibility_version,
            configuration_fingerprint=configuration_fingerprint(CDECRSettings()),
            epoch_id=result.epoch_id,
            message_ids=sorted_ids,
            message_set_sha256=message_set_sha256(sorted_ids),
            document_count=result.document_count,
            eligible_document_count=result.eligible_document_count,
            registry=RegistryArtifact(
                size_bytes=(bundle / "runtime.sqlite3").stat().st_size,
                sha256=registry_digest,
            ),
        )
        (bundle / "manifest.json").write_text(
            manifest.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n"
        )
        validate_bundle(
            bundle,
            containing_root=output_root,
            expected_compatibility_version=args.compatibility_version,
            expected_configuration_fingerprint=manifest.configuration_fingerprint,
            integrity_check=True,
        )
        archive = output_root / f"{bundle_id}.tar.zst"
        create_archive(bundle, archive)
        summary = {
            "bundle_id": bundle_id,
            "market": binding.market,
            "ticker": binding.ticker,
            "research_cutoff_at": cutoff.isoformat(),
            "epoch_id": result.epoch_id,
            "registry_sha256": registry_digest,
            "archive": str(archive),
            "historical": report.model_dump(mode="json"),
        }
        (output_root / f"{bundle_id}.build-summary.json").write_text(
            json.dumps(summary, sort_keys=True, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        return summary
    finally:
        for provider in providers:
            close = getattr(provider, "close", None)
            if close is not None:
                close()
        if work.exists():
            shutil.rmtree(work)


def _code_revision() -> str:
    repository = Path(__file__).resolve().parents[3]
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository,
        check=False,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip() if completed.returncode == 0 else "unknown"


def _find_bundle(root: Path, bundle_id: str) -> Path:
    matches = [path for path in root.glob(f"**/{bundle_id}") if path.is_dir()]
    if len(matches) != 1:
        raise PrebuiltError(
            "CDECR_PREBUILT_INVALID", f"expected one bundle named {bundle_id}, found {len(matches)}"
        )
    return matches[0]


def _has_initialization_run(database: Path, operation_id: str) -> bool:
    with closing(sqlite3.connect(database)) as connection:
        row = connection.execute(
            "SELECT 1 FROM initialization_runs WHERE "
            "json_extract(payload,'$.control_operation_id')=? LIMIT 1",
            (operation_id,),
        ).fetchone()
    return row is not None


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    subparsers = root.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build")
    build.add_argument("--market", default="US")
    build.add_argument("--ticker", required=True)
    build.add_argument("--research-cutoff-at", required=True)
    build.add_argument("--output-root", required=True, type=Path)
    build.add_argument("--compatibility-version", default="cdecr-prebuilt-runtime-v1")
    publish = subparsers.add_parser("publish")
    publish.add_argument("--root", type=Path)
    publish.add_argument("--archive", required=True, type=Path)
    inspect = subparsers.add_parser("inspect")
    inspect.add_argument("--root", type=Path)
    inspect.add_argument("--bundle-id", required=True)
    listing = subparsers.add_parser("list")
    listing.add_argument("--root", type=Path)
    listing.add_argument("--ticker")
    reconcile = subparsers.add_parser("reconcile")
    reconcile.add_argument("--root", type=Path)
    reconcile.add_argument("--initialization-db", required=True, type=Path)
    reconcile.add_argument("--grace-hours", type=int, default=1)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    settings = DoxAgentSettings()
    fingerprint = configuration_fingerprint(CDECRSettings())
    if args.command != "build" and args.root is None:
        args.root = Path(settings.cdecr_prebuilt_root)
    try:
        if args.command == "build":
            _json(asyncio.run(_build(args)))
        elif args.command == "publish":
            published = CDECRPrebuiltStore(args.root).publish(
                args.archive,
                compatibility_version=settings.cdecr_prebuilt_compatibility_version,
                fingerprint=fingerprint,
                max_age_hours=settings.cdecr_prebuilt_max_age_hours,
            )
            _json(published.manifest.model_dump(mode="json"))
        elif args.command == "inspect":
            bundle_path = _find_bundle(args.root.resolve(), args.bundle_id)
            validated = validate_bundle(
                bundle_path,
                containing_root=args.root,
                expected_compatibility_version=settings.cdecr_prebuilt_compatibility_version,
                expected_configuration_fingerprint=fingerprint,
                integrity_check=True,
            )
            _json(validated.manifest.model_dump(mode="json"))
        elif args.command == "list":
            _json(
                [
                    item.model_dump(mode="json")
                    for item in CDECRPrebuiltStore(args.root).list_ready(ticker=args.ticker)
                ]
            )
        elif args.command == "reconcile":
            released = CDECRPrebuiltStore(args.root).reconcile_claims(
                has_run=lambda owner: _has_initialization_run(args.initialization_db, owner),
                grace_hours=args.grace_hours,
            )
            _json({"released_claim_owners": released})
        return 0
    except (PrebuiltError, FileExistsError, ValueError, sqlite3.Error) as exc:
        _json({"error": getattr(exc, "code", type(exc).__name__), "detail": str(exc)})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
