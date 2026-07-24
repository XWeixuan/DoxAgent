"""Strict schema, reference, collision, and size validation for CDECR v2."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from common import DEFAULT_OUTPUT_DIR, DEFAULT_WORK_DIR, read_json, runtime_normalize, write_json


SCHEMAS: dict[str, dict[str, set[str]]] = {
    "companies": {"required": {"id", "name", "aliases"}, "optional": {"ticker"}},
    "institutions": {"required": {"id", "name", "aliases"}, "optional": set()},
    "persons": {"required": {"id", "name", "aliases"}, "optional": {"org_id"}},
    "instruments": {"required": {"id", "name", "aliases"}, "optional": {"ticker", "issuer_id"}},
    "places": {"required": {"id", "name", "aliases"}, "optional": set()},
    "named_objects": {"required": {"id", "name", "kind", "aliases"}, "optional": {"owner_id"}},
    "concepts": {"required": {"id", "name", "kind", "aliases"}, "optional": set()},
    "metrics": {"required": {"id", "name", "aliases"}, "optional": set()},
    "fiscal_periods": {"required": {"id", "company_id", "start", "end", "aliases"}, "optional": set()},
    "units": {"required": {"id", "name", "multiplier", "kind", "aliases"}, "optional": set()},
    "artifacts": {"required": {"id", "name", "kind", "aliases"}, "optional": {"owner_id", "date", "period_id"}},
    "attributes": {"required": {"key", "aliases", "target", "use"}, "optional": set()},
}

ENUMS = {
    ("named_objects", "kind"): {"FACILITY", "PRODUCT", "PROJECT", "ASSET", "TECHNOLOGY", "PROGRAM"},
    ("concepts", "kind"): {"PREDICATE", "ACCOUNTING_BASIS", "COMPARISON_BASIS", "GUIDANCE_ACTION", "ANALYST_ACTION", "LIFECYCLE_STAGE", "RATING"},
    ("units", "kind"): {"CURRENCY", "SCALE", "RATIO", "UNIT"},
    ("artifacts", "kind"): {"SEC_FILING", "EARNINGS_RELEASE", "PRESS_RELEASE", "ANALYST_REPORT", "AGREEMENT", "REPORT"},
    ("attributes", "target"): {"COMPANY", "INSTITUTION", "PERSON", "INSTRUMENT", "PLACE", "NAMED_OBJECT", "CONCEPT", "METRIC", "ARTIFACT", "QUANTITY", "LITERAL"},
    ("attributes", "use"): {"HARD", "SOFT", "CLAIM"},
}


def _date(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.strptime(str(value), "%Y-%m-%d")
    except ValueError:
        return None


def _catalog_hash(output: Path, present: list[str]) -> str:
    digest = hashlib.sha256()
    for name in sorted(present):
        path = output / f"{name}.json"
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def validate(output: Path, *, allow_missing: bool = False) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    data: dict[str, list[dict[str, Any]]] = {}
    stats: dict[str, Any] = {}
    for catalog, schema in SCHEMAS.items():
        path = output / f"{catalog}.json"
        if not path.exists():
            if not allow_missing:
                errors.append(f"missing file: {path.name}")
            continue
        try:
            payload = read_json(path)
        except Exception as exc:
            errors.append(f"{path.name}: invalid JSON: {exc}")
            continue
        if not isinstance(payload, list):
            errors.append(f"{path.name}: top level must be an array")
            continue
        data[catalog] = payload
        id_field = "key" if catalog == "attributes" else "id"
        ids: set[str] = set()
        normalized_names: Counter[str] = Counter()
        alias_owner: dict[str, set[str]] = defaultdict(set)
        empty_aliases = 0
        for index, item in enumerate(payload):
            label = f"{catalog}[{index}]"
            if not isinstance(item, dict):
                errors.append(f"{label}: entry must be an object")
                continue
            fields = set(item)
            missing = schema["required"] - fields
            extra = fields - schema["required"] - schema["optional"]
            if missing:
                errors.append(f"{label}: missing fields {sorted(missing)}")
            if extra:
                errors.append(f"{label}: forbidden fields {sorted(extra)}")
            entity_id = item.get(id_field)
            if not isinstance(entity_id, str) or not entity_id:
                errors.append(f"{label}: {id_field} must be non-empty string")
            elif entity_id in ids:
                errors.append(f"{label}: duplicate {id_field} {entity_id}")
            else:
                ids.add(entity_id)
            aliases = item.get("aliases")
            if not isinstance(aliases, list) or any(not isinstance(value, str) or not value.strip() for value in aliases):
                errors.append(f"{label}: aliases must be a list of non-empty strings")
                aliases = []
            if not aliases:
                empty_aliases += 1
            seen_aliases: set[str] = set()
            primary = str(item.get("name") or item.get("key") or "")
            for alias in [primary, *aliases]:
                normalized = runtime_normalize(alias)
                if not normalized:
                    continue
                if normalized in seen_aliases:
                    errors.append(f"{label}: duplicate runtime-equivalent alias {alias!r}")
                seen_aliases.add(normalized)
                if entity_id:
                    alias_owner[normalized].add(str(entity_id))
            if primary:
                normalized_names[runtime_normalize(primary)] += 1
            for (enum_catalog, field), allowed in ENUMS.items():
                if catalog == enum_catalog and item.get(field) not in allowed:
                    errors.append(f"{label}: invalid {field} {item.get(field)!r}")
            if catalog == "units" and not isinstance(item.get("multiplier"), (int, float)):
                errors.append(f"{label}: multiplier must be numeric")
            if catalog == "fiscal_periods":
                start, end = _date(item.get("start")), _date(item.get("end"))
                if not start or not end or end < start:
                    errors.append(f"{label}: invalid period dates")
            if catalog == "artifacts" and item.get("date") is not None and not _date(item.get("date")):
                errors.append(f"{label}: invalid artifact date")
        collisions = {key: owners for key, owners in alias_owner.items() if len(owners) > 1}
        stats[catalog] = {
            "records": len(payload),
            "bytes": path.stat().st_size,
            "mib": round(path.stat().st_size / 1024 / 1024, 3),
            "empty_alias_records": empty_aliases,
            "normalized_alias_collisions": len(collisions),
            "duplicate_normalized_names": sum(count - 1 for count in normalized_names.values() if count > 1),
        }
        if collisions:
            warnings.append(f"{catalog}: {len(collisions)} normalized aliases resolve to multiple IDs")

    def id_set(catalog: str) -> set[str]:
        return {item["id"] for item in data.get(catalog, []) if isinstance(item, dict) and isinstance(item.get("id"), str)}

    companies, institutions = id_set("companies"), id_set("institutions")
    organizations = companies | institutions
    fiscal_periods = id_set("fiscal_periods")
    for item in data.get("persons", []):
        if item.get("org_id") is not None and item["org_id"] not in organizations:
            errors.append(f"persons {item.get('id')}: unknown org_id {item['org_id']}")
    for item in data.get("instruments", []):
        if item.get("issuer_id") is not None and item["issuer_id"] not in companies:
            errors.append(f"instruments {item.get('id')}: unknown issuer_id {item['issuer_id']}")
    for item in data.get("named_objects", []):
        if item.get("owner_id") is not None and item["owner_id"] not in organizations:
            errors.append(f"named_objects {item.get('id')}: unknown owner_id {item['owner_id']}")
    for item in data.get("fiscal_periods", []):
        if item.get("company_id") not in companies:
            errors.append(f"fiscal_periods {item.get('id')}: unknown company_id {item.get('company_id')}")
    for item in data.get("artifacts", []):
        if item.get("owner_id") is not None and item["owner_id"] not in organizations:
            errors.append(f"artifacts {item.get('id')}: unknown owner_id {item['owner_id']}")
        if item.get("period_id") is not None and item["period_id"] not in fiscal_periods:
            errors.append(f"artifacts {item.get('id')}: unknown period_id {item['period_id']}")

    present = sorted(data)
    result = {
        "valid": not errors,
        "catalog_hash": _catalog_hash(output, present),
        "catalogs_present": present,
        "stats": stats,
        "errors": errors,
        "warnings": warnings,
    }
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR)
    parser.add_argument("--allow-missing", action="store_true")
    args = parser.parse_args()
    result = validate(args.output_dir, allow_missing=args.allow_missing)
    report_path = args.work_dir / "reports" / "catalog_validation.json"
    write_json(report_path, result)
    print(json.dumps({"valid": result["valid"], "hash": result["catalog_hash"], "errors": len(result["errors"]), "warnings": len(result["warnings"])}))
    if not result["valid"]:
        for error in result["errors"][:50]:
            print(f"ERROR {error}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
