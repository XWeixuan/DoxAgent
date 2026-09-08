"""Closed wire validation compiled from the checked-in TypeScript contract.

Generated schemas are ordinary JSON Schema 2020-12, usable by Python, OpenAPI and
consumer tests. Native workflow payloads must pass an explicit projector first.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, ClassVar

from jsonschema import Draft202012Validator, FormatChecker
from pydantic import RootModel, model_validator

SCHEMA = json.loads(Path(__file__).with_name("wire_schema.json").read_text(encoding="utf-8"))
VERSION = "2.0.0-draft.2"


@lru_cache(maxsize=1024)
def validator(name: str) -> Draft202012Validator:
    if name not in SCHEMA["$defs"]:
        raise KeyError(name)
    return Draft202012Validator(
        {"$ref": f"#/$defs/{name}", "$defs": SCHEMA["$defs"]},
        format_checker=FormatChecker(),
    )


def validate(name: str, value: Any) -> Any:
    error = next(validator(name).iter_errors(value), None)
    if error:
        # Never include an offending value: it might contain a native secret or path.
        location = "/".join(map(str, error.absolute_path))
        raise ValueError(f"{name}/{location}: {error.validator}")
    return value


class WireModel(RootModel[Any]):
    schema_name: ClassVar[str]

    @model_validator(mode="before")
    @classmethod
    def check_wire(cls, value: Any) -> Any:
        return validate(cls.schema_name, value)

    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema: Any, handler: Any) -> dict[str, Any]:
        return {
            **SCHEMA["$defs"][cls.schema_name],
            "$defs": SCHEMA["$defs"],
            "title": cls.schema_name,
        }


@lru_cache(maxsize=1024)
def model(name: str) -> type[WireModel]:
    validator(name)
    return type(name, (WireModel,), {"schema_name": name, "__module__": __name__})


def available(value: Any) -> dict[str, Any]:
    return {"state": "AVAILABLE", "value": value, "reason": None}


def missing(reason: str = "NOT_RECORDED", state: str = "NOT_RECORDED") -> dict[str, Any]:
    return {"state": state, "value": None, "reason": reason}


def coverage(
    *,
    complete: bool = False,
    count: int | None = None,
    at: str | None = None,
    reasons: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "state": "COMPLETE" if complete else "PARTIAL" if count is not None else "UNKNOWN",
        "reasons": reasons or ([] if complete else ["NOT_RECORDED"]),
        "known_count": count,
        "excluded_count": 0 if complete else None,
        "observed_through": at,
    }
