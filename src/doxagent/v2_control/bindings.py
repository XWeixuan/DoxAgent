"""Bus-owned atomic configuration mutations, including buffered-message settlement."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

from doxagent.api_v2.dto import validate
from doxagent.api_v2.errors import ApiFailure
from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.schema import SourceDefinition, TickerSourceBinding, UpdateActor
from doxagent.message_bus_v2.service import MessageBusV2Service
from doxagent.v2_read.repository import encode, instant


def migrate(db):
    db.execute(
        "CREATE TABLE IF NOT EXISTS v2_binding_commands (scope TEXT,key_hash TEXT,"
        "body_hash TEXT,receipt TEXT,PRIMARY KEY(scope,key_hash))"
    )


def secret(key, spec):
    return bool(
        spec.get("writeOnly")
        or spec.get("x-secret")
        or spec.get("format") == "password"
        or re.search(
            r"password|secret|credential|api.?key|access.?token|authorization|cookie", key, re.I
        )
    )


def embedded_secret(value, schema):
    """Arrays/credential-bearing URLs are atomic protected parameters, never partial edits."""
    if isinstance(value, str) and "://" in value:
        try:
            url = urlsplit(value)
            return bool(url.username or url.password) or any(
                secret(key, {}) for key, _ in parse_qsl(url.query)
            )
        except ValueError:
            return True
    if isinstance(value, dict):
        return any(
            secret(key, schema.get("properties", {}).get(key, {}))
            or embedded_secret(item, schema.get("properties", {}).get(key, {}))
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(embedded_secret(item, schema.get("items", {})) for item in value)
    return False


def public_parameters(value, schema, prefix="/source_parameters"):
    redacted, writable = [], []
    result = {}
    for key, item in value.items():
        path = prefix + "/" + key.replace("~", "~0").replace("/", "~1")
        spec = schema.get("properties", {}).get(key, {})
        if secret(key, spec) or (not isinstance(item, dict) and embedded_secret(item, spec)):
            redacted.append(path)
            continue
        if isinstance(item, dict):
            result[key], hidden, allowed = public_parameters(item, spec, path)
            redacted.extend(hidden)
            writable.extend(allowed)
        else:
            result[key] = item
            if not spec.get("readOnly"):
                writable.append(path)
    for key, spec in schema.get("properties", {}).items():
        if key not in value and not secret(key, spec) and not spec.get("readOnly"):
            writable.append(prefix + "/" + key.replace("~", "~0").replace("/", "~1"))
    return result, redacted, writable


def public_schema(schema):
    result = copy.deepcopy(schema)
    result.pop("default", None)
    result.pop("examples", None)
    if "properties" in result:
        result["properties"] = {
            key: public_schema(spec)
            for key, spec in result["properties"].items()
            if not secret(key, spec)
        }
        if "required" in result:
            result["required"] = [key for key in result["required"] if key in result["properties"]]
    if isinstance(result.get("items"), dict):
        result["items"] = public_schema(result["items"])
    for key in ("allOf", "anyOf", "oneOf", "prefixItems"):
        if isinstance(result.get(key), list):
            result[key] = [public_schema(item) for item in result[key]]
    for key in ("$defs", "definitions", "patternProperties"):
        if isinstance(result.get(key), dict):
            result[key] = {name: public_schema(item) for name, item in result[key].items()}
    return result


def merge_parameters(current, replacement, schema):
    result = {}
    properties = schema.get("properties", {})
    for key in set(current) | set(replacement):
        spec = properties.get(key, {})
        protected = not isinstance(current.get(key), dict) and embedded_secret(
            current.get(key), spec
        )
        if secret(key, spec) or spec.get("readOnly") or protected:
            if key in replacement and replacement[key] != current.get(key):
                raise ApiFailure("PARAMETER_NOT_WRITABLE", 422)
            if key in current:
                result[key] = current[key]
        elif isinstance(current.get(key), dict) or isinstance(replacement.get(key), dict):
            incoming = replacement.get(key, {})
            if not isinstance(incoming, dict):
                raise ApiFailure("VALIDATION_FAILED", 422)
            result[key] = merge_parameters(current.get(key, {}), incoming, spec)
        elif key in replacement:
            if embedded_secret(replacement[key], spec):
                raise ApiFailure("PARAMETER_NOT_WRITABLE", 422)
            result[key] = replacement[key]
    return result


def configuration(source, binding):
    source = (
        source if isinstance(source, SourceDefinition) else SourceDefinition.model_validate(source)
    )
    binding = (
        binding
        if isinstance(binding, TickerSourceBinding)
        else TickerSourceBinding.model_validate(binding)
    )
    parameters, hidden, writable = public_parameters(
        binding.source_parameters, source.parameter_schema
    )
    return validate(
        "BindingConfig",
        {
            "binding_id": binding.binding_id,
            "ticker": binding.ticker,
            "source": {
                "source_id": source.source_id,
                "binding_id": binding.binding_id,
                "name": source.display_name,
                "kind": source.kind.value,
            },
            "binding_version": binding.version,
            "source_version": source.version,
            "control_etag": f'"binding:{binding.version}:source:{source.version}"',
            "effective": {
                "enabled": binding.enabled,
                "source_parameters": parameters,
                "polling": binding.polling.model_dump(mode="json"),
                "streaming": binding.streaming.model_dump(mode="json"),
            },
            "parameter_schema": public_schema(source.parameter_schema),
            "editor": {
                "parameter_form": False,
                "parameter_form_id": None,
                "json": True,
                "polling_interval_form": True,
            },
            "writable_parameter_paths": sorted(writable),
            "redacted_parameter_paths": sorted(hidden),
        },
    )


class TransactionRepository(MessageBusV2Repository):
    """Reuse native Bus behavior while its nested reads/writes share the caller transaction."""

    def __init__(self, path, db):
        self.path, self.db = path, db

    @contextmanager
    def _connect(self):
        yield self.db

    @contextmanager
    def transaction(self):
        yield self.db


class Bindings:
    def __init__(self, path):
        self.path = Path(path).resolve()

    @contextmanager
    def connect(self, write=False):
        db = sqlite3.connect(
            self.path.as_uri() + ("?mode=rw" if write else "?mode=ro"), uri=True, timeout=2
        )
        db.row_factory = sqlite3.Row
        try:
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def get(self, ticker, identity):
        with self.connect() as db:
            repository = TransactionRepository(self.path, db)
            binding = repository.get_binding(identity)
            if not binding or binding.ticker != ticker:
                raise ApiFailure("RESOURCE_NOT_FOUND", 404)
            source = repository.get_source(binding.source_id)
            if source is None:
                raise ApiFailure("RESOURCE_NOT_FOUND", 404)
            return configuration(source, binding)

    def available(self, ticker, identity):
        with self.connect() as db:
            repository = TransactionRepository(self.path, db)
            source = repository.get_source(identity)
            if (
                not source
                or source.kind.value != "api"
                or not source.enabled
                or repository.get_binding(ticker + ":" + identity)
            ):
                raise ApiFailure("RESOURCE_NOT_FOUND", 404)
            parameters, _, _ = public_parameters(source.default_parameters, source.parameter_schema)
            return validate(
                "AvailableSourceDetail",
                {
                    "source_id": source.source_id,
                    "name": source.display_name,
                    "kind": "api",
                    "source_version": source.version,
                    "defaults": {
                        "enabled": True,
                        "source_parameters": parameters,
                        "polling": source.default_polling_config.model_dump(mode="json"),
                        "streaming": source.default_streaming_config.model_dump(mode="json"),
                    },
                    "parameter_schema": public_schema(source.parameter_schema),
                    "parameter_form_id": None,
                },
            )

    def mutate(self, ticker, identity, method, body, actor, key, expected):
        if not 8 <= len(key) <= 128 or not key.isascii():
            raise ApiFailure("IDEMPOTENCY_KEY_REQUIRED", 428)
        scope = encode([actor, ticker, method, identity])
        key_hash, body_hash = (
            hashlib.sha256(key.encode()).hexdigest(),
            hashlib.sha256(encode(body).encode()).hexdigest(),
        )
        with self.connect(write=True) as db:
            prior = db.execute(
                "SELECT body_hash,receipt FROM v2_binding_commands WHERE scope=? AND key_hash=?",
                (scope, key_hash),
            ).fetchone()
            if prior:
                if prior[0] != body_hash:
                    raise ApiFailure("IDEMPOTENCY_CONFLICT", 409)
                return json.loads(prior[1])
            repository = TransactionRepository(self.path, db)
            service = MessageBusV2Service(repository)
            if method == "POST":
                source = repository.get_source(body["source_id"])
                if not source or not source.enabled or source.kind.value != "api":
                    raise ApiFailure("RESOURCE_NOT_FOUND", 404)
                if source.version != body["source_version"]:
                    raise ApiFailure("REVISION_CONFLICT", 412)
                identity = ticker + ":" + source.source_id
                if repository.get_binding(identity):
                    raise ApiFailure("ALREADY_BOUND", 409)
                config = copy.deepcopy(body["configuration"])
                config["source_parameters"] = merge_parameters(
                    source.default_parameters, config["source_parameters"], source.parameter_schema
                )
                binding = service.configure_binding(
                    ticker=ticker,
                    source_id=source.source_id,
                    **config,
                    actor=UpdateActor.USER,
                    reason=f"V2 {actor} {key_hash}",
                )
            else:
                binding = repository.get_binding(identity)
                if not binding or binding.ticker != ticker:
                    raise ApiFailure("RESOURCE_NOT_FOUND", 404)
                source = repository.get_source(binding.source_id)
                if source is None:
                    raise ApiFailure("RESOURCE_NOT_FOUND", 404)
                if not expected:
                    raise ApiFailure("PRECONDITION_REQUIRED", 428)
                if expected != configuration(source, binding)["control_etag"]:
                    raise ApiFailure("REVISION_CONFLICT", 412)
                if method == "DELETE":
                    binding = service.delete_binding(
                        identity, actor=UpdateActor.USER, reason=f"V2 {actor} {key_hash}"
                    )
                else:
                    patch = copy.deepcopy(body)
                    if "source_parameters" in patch:
                        patch["source_parameters"] = merge_parameters(
                            binding.source_parameters,
                            patch["source_parameters"],
                            source.parameter_schema,
                        )
                    if "polling" in patch:
                        patch["polling"] = {
                            **binding.polling.model_dump(mode="json"),
                            **patch["polling"],
                        }
                    if "streaming" in patch:
                        patch["streaming"] = {
                            **binding.streaming.model_dump(mode="json"),
                            **patch["streaming"],
                            "buffer": {
                                **binding.streaming.buffer.model_dump(mode="json"),
                                **patch["streaming"].get("buffer", {}),
                            },
                        }
                    patch["source_version"] = source.version
                    binding = service.update_binding(
                        identity, patch, actor=UpdateActor.USER, reason=f"V2 {actor} {key_hash}"
                    )
            saved = repository.get_binding(identity, include_tombstoned=True)
            receipt = (
                {
                    "binding_id": identity,
                    "binding_version": saved.version,
                    "removed": True,
                    "updated_at": instant(saved.updated_at),
                }
                if method == "DELETE"
                else configuration(source, saved)
            )
            db.execute(
                "INSERT INTO v2_binding_commands VALUES(?,?,?,?)",
                (scope, key_hash, body_hash, encode(receipt)),
            )
            return receipt
