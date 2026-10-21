"""Compile the generated cost contract once; retain canonical validation on failure."""

from functools import lru_cache
from urllib.parse import unquote

import fastjsonschema
from jsonschema import FormatChecker


@lru_cache(maxsize=1)
def compiled_cost_validator():
    from .dto import SCHEMA

    definitions, formats = {}, set()
    # These newer keywords cannot be delegated to a draft-07 compiler. Future
    # contract additions continue through the canonical 2020-12 validator.
    unsupported = {
        "unevaluatedProperties",
        "unevaluatedItems",
        "prefixItems",
        "dependentSchemas",
        "dependentRequired",
        "$dynamicRef",
        "$dynamicAnchor",
        "$recursiveRef",
        "$recursiveAnchor",
        "minContains",
        "maxContains",
        "contentSchema",
        "contentEncoding",
        "contentMediaType",
        "$schema",
        "$id",
        "$anchor",
    }

    def walk(value):
        if isinstance(value, dict):
            if unsupported.intersection(value):
                raise ValueError("newer schema keyword")
            if "$ref" in value:
                if set(value) - {"$ref", "description", "title", "$comment"}:
                    raise ValueError("reference siblings require newer semantics")
                ref = value["$ref"]
                if not ref.startswith("#/$defs/"):
                    raise ValueError("nonlocal schema reference")
                add(unquote(ref[8:]))
            if "format" in value:
                formats.add(value["format"])
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    def add(name):
        if name not in definitions:
            definitions[name] = SCHEMA["$defs"][name]
            walk(definitions[name])

    try:
        add("CostReport")
        checker = FormatChecker()
        return fastjsonschema.compile(
            {
                "$schema": "http://json-schema.org/draft-07/schema#",
                "$ref": "#/$defs/CostReport",
                "$defs": definitions,
            },
            use_default=False,
            formats={
                name: lambda value, name=name: checker.conforms(value, name) for name in formats
            },
        )
    except (ValueError, fastjsonschema.JsonSchemaDefinitionException):
        return None


def accepts(value):
    validator = compiled_cost_validator()
    if validator is not None:
        try:
            validator(value)
            return True
        except fastjsonschema.JsonSchemaException:
            pass  # Canonical validation supplies the same semantics and safe errors.
    return False
