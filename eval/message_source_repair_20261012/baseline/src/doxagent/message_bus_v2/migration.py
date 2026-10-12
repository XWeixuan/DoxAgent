"""Explicit, repeatable acquisition-mode migration with an online SQLite backup."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .distribution_repository import DistributionRepository
from .monitoring_terms import MonitoringTermsService
from .repository import MessageBusV2Repository
from .service import MessageBusV2Service

SEARCH_IDS = ("reuters_site_search", "google_news_search_rss")


def preview(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    if not target.exists():
        return {"database": str(target), "exists": False, "source_changes": [], "legacy_terms": []}
    changes: list[dict[str, Any]] = []
    legacy: list[dict[str, Any]] = []
    with sqlite3.connect(f"file:{target.as_posix()}?mode=ro", uri=True) as db:
        if not _has_table(db, "source_definitions"):
            return {
                "database": str(target),
                "exists": True,
                "source_changes": [],
                "legacy_terms": [],
            }
        for source_id, value in db.execute(
            "SELECT source_id,data_json FROM source_definitions WHERE source_id IN (?,?)",
            SEARCH_IDS,
        ):
            source = json.loads(value)
            if source.get("acquisition_mode", "by_ticker") != "by_search":
                changes.append(
                    {
                        "source_id": source_id,
                        "from": source.get("acquisition_mode", "by_ticker"),
                        "to": "by_search",
                    }
                )
        if _has_table(db, "ticker_source_bindings"):
            for ticker, source_id, value in db.execute(
                "SELECT ticker,source_id,data_json FROM ticker_source_bindings "
                "WHERE source_id IN (?,?) ORDER BY ticker,source_id",
                SEARCH_IDS,
            ):
                binding = json.loads(value)
                params = binding.get("source_parameters", {})
                legacy.append(
                    {
                        "ticker": ticker,
                        "source_id": source_id,
                        "company_short_name": params.get("company_short_name"),
                        "search_terms": params.get("search_terms"),
                        "status": "LEGACY_TERMS",
                    }
                )
    return {
        "database": str(target),
        "exists": True,
        "source_changes": changes,
        "legacy_terms": legacy,
    }


def apply(path: str | Path, *, backup_dir: str | Path | None = None) -> dict[str, Any]:
    target = Path(path)
    before = preview(target)
    backup: str | None = None
    if target.exists():
        directory = Path(backup_dir) if backup_dir else target.parent
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        backup_path = directory / f"{target.name}.pre-acquisition-{stamp}.bak"
        if backup_path.exists():
            raise FileExistsError(backup_path)
        with sqlite3.connect(target) as source, sqlite3.connect(backup_path) as destination:
            source.backup(destination)
        backup = str(backup_path)
    repository = MessageBusV2Repository(target)
    MonitoringTermsService(repository)
    DistributionRepository(repository)
    MessageBusV2Service(repository)._migrate_acquisition_modes()
    return {"before": before, "after": preview(target), "backup": backup}


def _has_table(db: sqlite3.Connection, name: str) -> bool:
    return (
        db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
        is not None
    )
