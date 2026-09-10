"""Typed invocation receipts; no pickle or executable operator-supplied import paths."""

from __future__ import annotations

import importlib
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel


def encode(value: Any) -> Any:
    if isinstance(value, Enum):
        return ["enum", _name(type(value)), value.value]
    if isinstance(value, BaseModel):
        return ["model", _name(type(value)), value.model_dump(mode="json")]
    if isinstance(value, type) and issubclass(value, BaseModel):
        return ["type", _name(value)]
    if isinstance(value, datetime):
        return ["datetime", value.isoformat()]
    if isinstance(value, Path):
        return ["path", str(value)]
    if isinstance(value, dict):
        return ["dict", [[encode(k), encode(v)] for k, v in value.items()]]
    if isinstance(value, (list, tuple)):
        return ["tuple" if isinstance(value, tuple) else "list", [encode(v) for v in value]]
    if value is None or isinstance(value, (str, int, float, bool)):
        return ["value", value]
    raise TypeError(f"unsupported invocation argument: {type(value).__name__}")


def _name(model: type[Any]) -> str:
    name = model.__module__ + ":" + model.__name__
    if not model.__module__.startswith("doxagent."):
        raise ValueError("only repository-owned invocation types are supported")
    return name


def decode(value: Any) -> Any:
    kind, *parts = value
    if kind == "value":
        return parts[0]
    if kind == "dict":
        return {decode(k): decode(v) for k, v in parts[0]}
    if kind in {"tuple", "list"}:
        items = [decode(v) for v in parts[0]]
        return tuple(items) if kind == "tuple" else items
    if kind == "datetime":
        return datetime.fromisoformat(parts[0])
    if kind == "path":
        return Path(parts[0])
    module, name = parts[0].split(":", 1)
    if not module.startswith("doxagent.") or not name.isidentifier():
        raise ValueError("invalid invocation type")
    model = getattr(importlib.import_module(module), name)
    if kind in {"model", "type"} and isinstance(model, type) and issubclass(model, BaseModel):
        from doxagent.codex_runtime.recovery import ingest_model

        return model if kind == "type" else ingest_model(model, parts[1])
    if kind == "enum" and isinstance(model, type) and issubclass(model, Enum):
        return model(parts[1])
    raise ValueError("invalid invocation type or tag")


async def freeze_invocation(child: Any, kind: str, arguments: dict[str, Any]) -> None:
    if child.node.receipt.get("invocation"):
        return
    owner = arguments["self"]
    workspace = getattr(owner, "workspace", None) or getattr(owner, "_workspace", None)
    snapshot = getattr(workspace, "snapshot", None)
    if snapshot is None:
        return  # In-memory/test adapters have no deployed worker workspace.
    run_id = arguments.get("workspace_run_id") or arguments.get("run_id")
    if run_id is None:
        run_id = arguments["request"].run_id
    invocation = encode({k: v for k, v in arguments.items() if k != "self"})
    await snapshot(run_id, child.node.execution_id)
    child.checkpoint(
        invocation=invocation,
        snapshot={
            "run_id": run_id,
            "snapshot_id": child.node.execution_id,
            "kind": kind,
        },
    )
