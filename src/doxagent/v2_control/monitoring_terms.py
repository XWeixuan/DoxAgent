"""Ticker-owned monitoring terms, read from and written to the native Bus database."""

from __future__ import annotations

import hashlib
import json

from pydantic import ValidationError

from doxagent.api_v2.errors import ApiFailure
from doxagent.message_bus_v2.monitoring_terms import (
    MonitoringTermsService,
    TickerMonitoringTerms,
    validate_terms,
)
from doxagent.message_bus_v2.schema import AcquisitionMode
from doxagent.message_bus_v2.search_plan import build_query_plan
from doxagent.v2_read.repository import encode

from .bindings import Bindings, TransactionRepository, migrate


def etag(ticker: str, revision: int) -> str:
    return f'"monitoring-terms:{ticker}:{revision}"'


def issues(exc: Exception) -> list[dict[str, str]]:
    if isinstance(exc, ValidationError):
        return [
            {
                "path": "/" + "/".join(str(part) for part in error["loc"]),
                "code": error["type"],
                "message": error["msg"],
            }
            for error in exc.errors()
        ]
    message = str(exc)
    path, _, detail = message.partition(": ") if message.startswith("/") else ("/", "", message)
    return [{"path": path, "code": "INVALID_TERMS", "message": detail}]


class MonitoringTermsControl(Bindings):
    def __init__(self, path):
        super().__init__(path)
        with self.connect(write=True) as db:
            migrate(db)

    def _snapshot(self, db, ticker: str, configuration_override=None):
        repo = TransactionRepository(self.path, db)
        source_map = {source.source_id: source for source in repo.list_sources()}
        requirements: dict[str, list[str]] = {"en": []}
        for source in source_map.values():
            if (
                source.acquisition_mode
                in {AcquisitionMode.BY_SEARCH, AcquisitionMode.BY_DISTRIBUTION}
                and source.content_language
            ):
                requirements.setdefault(source.content_language, []).append(source.source_id)
        head = db.execute(
            "SELECT current_revision,updated_at FROM ticker_monitoring_terms WHERE ticker=?",
            (ticker,),
        ).fetchone()
        revision = int(head[0]) if head else 0
        row = (
            db.execute(
                "SELECT config_json FROM ticker_monitoring_term_revisions "
                "WHERE ticker=? AND revision=?",
                (ticker, revision),
            ).fetchone()
            if head
            else None
        )
        saved = json.loads(row[0]) if row else None
        config = (
            configuration_override
            if configuration_override is not None
            else (
                {key: saved[key] for key in ("l1_concepts", "l2", "definition")} if saved else None
            )
        )
        preview_revision = revision + 1 if configuration_override is not None else revision
        terms = None
        if config:
            try:
                terms = TickerMonitoringTerms.model_validate(
                    {"ticker": ticker, "expected_revision": preview_revision - 1, **config}
                )
            except ValidationError:
                pass  # A historical invalid definition remains readable and editable.
        consumers = []
        for binding in repo.list_bindings(ticker=ticker):
            source = source_map.get(binding.source_id)
            if not source or source.acquisition_mode not in {
                AcquisitionMode.BY_SEARCH,
                AcquisitionMode.BY_DISTRIBUTION,
            }:
                continue
            previews = []
            problem = None
            if source.acquisition_mode is AcquisitionMode.BY_SEARCH and terms:
                try:
                    previews = [
                        q.model_dump(mode="json")
                        for q in build_query_plan(source, terms, preview_revision).queries
                    ]
                except ValueError as exc:
                    problem = str(exc)
            consumers.append(
                {
                    "source_id": source.source_id,
                    "name": source.display_name,
                    "binding_id": binding.binding_id,
                    "acquisition_mode": source.acquisition_mode.value,
                    "content_language": source.content_language,
                    "source_enabled": source.enabled,
                    "binding_enabled": binding.enabled,
                    "search_policy_mode": source.search_policy.mode
                    if source.search_policy
                    else None,
                    "jev_source_enabled": source.distribution_policy.jev_enabled
                    if source.distribution_policy
                    else None,
                    "terms_mode": "UNIFIED"
                    if terms
                    else "LEGACY"
                    if source.acquisition_mode is AcquisitionMode.BY_SEARCH
                    else "UNCONFIGURED",
                    "queries": previews,
                    "preview_issue": problem,
                }
            )
        return {
            "ticker": ticker,
            "revision": revision,
            "control_etag": etag(ticker, revision),
            "updated_at": head[1] if head else None,
            "configuration": config,
            "required_languages": sorted(requirements),
            "language_requirements": [
                {"language": lang, "source_ids": sorted(ids)}
                for lang, ids in sorted(requirements.items())
            ],
            "consumers": consumers,
        }

    def get_terms(self, ticker: str):
        with self.connect() as db:
            return self._snapshot(db, ticker)

    def validate_config(self, ticker: str, configuration):
        with self.connect() as db:
            snapshot = self._snapshot(db, ticker, configuration)
        try:
            terms = TickerMonitoringTerms.model_validate(
                {"ticker": ticker, "expected_revision": snapshot["revision"], **configuration}
            )
            validate_terms(terms, set(snapshot["required_languages"]))
            found = []
        except (ValidationError, ValueError, TypeError) as exc:
            found = issues(exc)
        return {
            "valid": not found,
            "issues": found,
            "search_previews": [
                consumer
                for consumer in snapshot["consumers"]
                if consumer["acquisition_mode"] == "by_search"
            ]
            if not found
            else [],
        }

    def put_terms(self, ticker: str, configuration, actor: str, key: str, expected: str | None):
        if not expected:
            raise ApiFailure("PRECONDITION_REQUIRED", 428)
        if not 8 <= len(key) <= 128 or not key.isascii():
            raise ApiFailure("IDEMPOTENCY_KEY_REQUIRED", 428)
        scope = encode([actor, ticker, "PUT", "monitoring-terms"])
        key_hash = hashlib.sha256(key.encode()).hexdigest()
        body_hash = hashlib.sha256(encode([expected, configuration]).encode()).hexdigest()
        with self.connect(write=True) as db:
            prior = db.execute(
                "SELECT body_hash,receipt FROM v2_monitoring_terms_commands "
                "WHERE scope=? AND key_hash=?",
                (scope, key_hash),
            ).fetchone()
            if prior:
                if prior[0] != body_hash:
                    raise ApiFailure("IDEMPOTENCY_CONFLICT", 409)
                return json.loads(prior[1])
            snapshot = self._snapshot(db, ticker)
            if expected != snapshot["control_etag"]:
                raise ApiFailure("REVISION_CONFLICT", 412)
            try:
                terms = TickerMonitoringTerms.model_validate(
                    {
                        "ticker": ticker,
                        "expected_revision": snapshot["revision"],
                        **configuration,
                    }
                )
                validate_terms(terms, set(snapshot["required_languages"]))
                service = MonitoringTermsService(TransactionRepository(self.path, db))
                service.apply(terms, actor=actor)
            except (ValidationError, ValueError, TypeError) as exc:
                raise ApiFailure("VALIDATION_FAILED", 422, fields=issues(exc)) from exc
            receipt = self._snapshot(db, ticker)
            db.execute(
                "INSERT INTO v2_monitoring_terms_commands VALUES(?,?,?,?)",
                (scope, key_hash, body_hash, encode(receipt)),
            )
            return receipt
