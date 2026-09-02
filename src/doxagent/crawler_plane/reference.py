"""Bootstrap deterministic working copies for the two reference crawler classes."""

from __future__ import annotations

import shutil
import time
from pathlib import Path

from doxagent.crawler_plane.schema import CrawlerVersionSpec
from doxagent.crawler_plane.service import CrawlerPlaneService

REFERENCE_SPECS = (
    CrawlerVersionSpec(
        crawler_id="company_ir_reference",
        version=1,
        entrypoint="crawler.py:crawl",
        parameter_schema={
            "type": "object",
            "properties": {
                "listing_url": {"type": "string", "minLength": 1},
                "source_name": {"type": "string", "minLength": 1},
            },
            "required": ["listing_url", "source_name"],
            "additionalProperties": False,
        },
        checkpoint_schema_version=1,
    ),
    CrawlerVersionSpec(
        crawler_id="government_policy_reference",
        version=1,
        entrypoint="crawler.py:crawl",
        parameter_schema={
            "type": "object",
            "properties": {
                "listing_url": {"type": "string", "minLength": 1},
                "source_name": {"type": "string", "minLength": 1},
            },
            "required": ["listing_url", "source_name"],
            "additionalProperties": False,
        },
        checkpoint_schema_version=1,
    ),
)


def bootstrap_reference_working_copies(service: CrawlerPlaneService) -> None:
    templates = Path(__file__).resolve().parent / "reference_packages"
    for spec in REFERENCE_SPECS:
        if service.repository.get_package(spec.crawler_id) is not None:
            continue
        try:
            version = service.create_version(spec)
        except FileExistsError:
            for _ in range(40):
                time.sleep(0.05)
                if service.repository.get_package(spec.crawler_id) is not None:
                    break
            else:
                raise RuntimeError(f"reference crawler bootstrap stalled for {spec.crawler_id}")
            continue
        if version.working_path is None:
            raise RuntimeError("reference crawler working path was not created")
        shutil.copytree(templates / spec.crawler_id, version.working_path, dirs_exist_ok=True)


__all__ = ["REFERENCE_SPECS", "bootstrap_reference_working_copies"]
