"""Expose closed response schemas for implemented methods and audit route completeness."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

from .dto import SCHEMA

ROUTES = json.loads(Path(__file__).with_name("route_contract.json").read_text(encoding="utf-8"))


def query_parameters(endpoint):
    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(endpoint)))
    names = set()
    for call in ast.walk(tree):
        if not isinstance(call, ast.Call):
            continue
        name = getattr(call.func, "id", getattr(call.func, "attr", None))
        if name != "query":
            continue
        for argument in call.args:
            if isinstance(argument, ast.Set):
                names.update(
                    item.value
                    for item in argument.elts
                    if isinstance(item, ast.Constant) and isinstance(item.value, str)
                )
    return [
        {
            "name": name,
            "in": "query",
            "required": False,
            "schema": {"type": "integer", "minimum": 1, "maximum": 100}
            if name == "limit"
            else {"type": "string"},
        }
        for name in sorted(names)
    ]


def coverage(app: FastAPI) -> dict[str, Any]:
    actual = {
        (method, route.path.removeprefix("/api/doxagent/v2"))
        for route in app.routes
        for method in getattr(route, "methods", ())
        if route.path.startswith("/api/doxagent/v2")
    }
    expected = {(r["method"], r["path"]) for r in ROUTES}
    return {
        "expected": len(expected),
        "implemented": len(actual & expected),
        "missing": [f"{m} {p}" for m, p in sorted(expected - actual)],
        "unexpected": [f"{m} {p}" for m, p in sorted(actual - expected)],
    }


def install(app: FastAPI) -> None:
    def generate() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema
        value = get_openapi(title=app.title, version=app.version, routes=app.routes)
        schemas = json.loads(
            json.dumps(SCHEMA["$defs"]).replace("#/$defs/", "#/components/schemas/")
        )
        value.setdefault("components", {}).setdefault("schemas", {}).update(schemas)
        value["components"]["securitySchemes"] = {
            "SupabaseBearer": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}
        }
        for route in ROUTES:
            path = "/api/doxagent/v2" + route["path"]
            operation = value["paths"].get(path, {}).get(route["method"].lower())
            if operation is None:
                continue
            handler = next(
                r
                for r in app.routes
                if r.path == path and route["method"] in getattr(r, "methods", ())
            )
            operation.setdefault("parameters", []).extend(query_parameters(handler.endpoint))
            operation["x-contract-parameters"] = route["parameters"]
            if route["method"] in {"POST", "PATCH", "DELETE"}:
                operation["parameters"].extend(
                    [
                        {
                            "name": "Idempotency-Key",
                            "in": "header",
                            "required": True,
                            "schema": {"type": "string", "minLength": 8, "maxLength": 128},
                        },
                        {
                            "name": "If-Match",
                            "in": "header",
                            "required": route["path"] != "/tickers",
                            "schema": {"type": "string"},
                        },
                    ]
                )
            operation["security"] = (
                [] if route["path"] == "/auth/config" else [{"SupabaseBearer": []}]
            )
            schema = route["response_schema"]
            if schema and not route["path"].endswith("/download"):
                response = {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["data", "meta"],
                    "properties": {
                        "data": {"$ref": "#/components/schemas/" + schema},
                        "meta": {"$ref": "#/components/schemas/Meta"},
                    },
                }
                if route["method"] == "GET" and schema not in {
                    "AuthConfig", "Principal", "Capabilities", "ReadContext", "Operation"
                }:
                    response["properties"]["data"] = {
                        "type": "object", "additionalProperties": False,
                        "required": ["state", "data", "reason", "coverage"],
                        "properties": {
                            "state": {"enum": ["AVAILABLE", "EMPTY", "PARTIAL", "NOT_PRODUCED", "UNAVAILABLE", "FORBIDDEN", "ERROR"]},
                            "data": {"anyOf": [{"$ref": "#/components/schemas/" + schema}, {"type": "null"}]},
                            "reason": {"anyOf": [{"$ref": "#/components/schemas/Reason"}, {"type": "null"}]},
                            "coverage": {"$ref": "#/components/schemas/Coverage"},
                        },
                    }
                status = "202" if schema == "Operation" and route["method"] != "GET" else "200"
                if route["method"] == "POST" and route["path"].endswith("/bindings"):
                    status = "201"
                if status != "200":
                    operation["responses"].pop("200", None)
                operation["responses"][status] = {
                    "description": "V2 response",
                    "content": {"application/json": {"schema": response}},
                }
            for status in (
                "400",
                "401",
                "403",
                "404",
                "409",
                "410",
                "412",
                "413",
                "422",
                "428",
                "503",
            ):
                operation["responses"][status] = {
                    "description": "V2 error",
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/ErrorResponse"}
                        }
                    },
                }
            body = {
                "POST /tickers": "StartTickerRequest",
                "PATCH /tickers/{ticker}/bindings/{binding_id}": "BindingPatch",
                "POST /tickers/{ticker}/bindings": "BindSourceRequest",
            }.get(route["method"] + " " + route["path"])
            if body:
                operation["requestBody"] = {
                    "required": True,
                    "content": {
                        "application/json": {"schema": {"$ref": "#/components/schemas/" + body}}
                    },
                }
        value["x-v2-route-coverage"] = coverage(app)
        app.openapi_schema = value
        return value

    app.openapi = generate
