"""Versioned, ticker-owned search and distribution monitoring definitions."""

from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .repository import MessageBusV2Repository
from .schema import BusModel, canonical_json, sha256_text, utc_now


class L1Concept(BusModel):
    concept_id: str
    expressions: dict[str, str]

    @field_validator("expressions")
    @classmethod
    def _expressions(cls, value: dict[str, str]) -> dict[str, str]:
        if not value or any(
            not item.strip() or "|" in item or re.search(r"\bOR\b", item, re.I)
            for item in value.values()
        ):
            raise ValueError("L1 expressions must be nonempty and cannot contain OR syntax")
        return {language: item.strip() for language, item in value.items()}


class L2Term(BusModel):
    literal: str | None = None
    regex: str | None = None
    field: Literal["all", "title", "summary", "body"] = "all"
    case_sensitive: bool = False
    whole_word: bool = False

    @model_validator(mode="after")
    def _one_pattern(self) -> L2Term:
        if bool(self.literal) == bool(self.regex):
            raise ValueError("exactly one of literal or regex is required")
        return self


class L2Group(BusModel):
    id: str
    any: list[L2Term] = Field(default_factory=list)
    all: list[L2Term] = Field(default_factory=list)
    none: list[L2Term] = Field(default_factory=list)

    @model_validator(mode="after")
    def _positive(self) -> L2Group:
        if not self.any and not self.all:
            raise ValueError("L2 group needs a positive condition")
        return self


class L2Language(BusModel):
    groups: list[L2Group]


class RelevanceDefinition(BusModel):
    relevant: str = Field(min_length=1)
    irrelevant: str = Field(min_length=1)


class TickerMonitoringTerms(BusModel):
    ticker: str
    expected_revision: int = Field(ge=0)
    l1_concepts: list[L1Concept] = Field(min_length=1, max_length=3)
    l2: dict[str, L2Language]
    definition: RelevanceDefinition

    @field_validator("ticker")
    @classmethod
    def _ticker(cls, value: str) -> str:
        return value.strip().upper()

    @model_validator(mode="after")
    def _unique(self) -> TickerMonitoringTerms:
        ids = [item.concept_id for item in self.l1_concepts]
        if len(ids) != len(set(ids)):
            raise ValueError("L1 concept_id must be unique")
        return self

    def validate_languages(self, languages: set[str]) -> None:
        for language in languages | {"en"}:
            if any(language not in concept.expressions for concept in self.l1_concepts):
                raise ValueError(f"missing L1 language: {language}")
            if language not in self.l2 or not self.l2[language].groups:
                raise ValueError(f"missing L2 language: {language}")


class MonitoringTermsService:
    def __init__(self, repository: MessageBusV2Repository) -> None:
        self.repository = repository
        with repository.transaction() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS ticker_monitoring_terms "
                "(ticker TEXT PRIMARY KEY, current_revision INTEGER NOT NULL, "
                "updated_at TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS ticker_monitoring_term_revisions "
                "(ticker TEXT NOT NULL, revision INTEGER NOT NULL, config_json TEXT NOT NULL, "
                "config_hash TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL, "
                "PRIMARY KEY(ticker,revision))"
            )

    def required_languages(self) -> set[str]:
        from .schema import AcquisitionMode

        return {
            source.content_language
            for source in self.repository.list_sources()
            if source.acquisition_mode
            in {AcquisitionMode.BY_SEARCH, AcquisitionMode.BY_DISTRIBUTION}
            and source.content_language
        }

    def apply(self, terms: TickerMonitoringTerms, *, actor: str) -> int:
        if len(canonical_json(terms.model_dump(mode="json"))) > 65536:
            raise ValueError("monitoring terms exceed 64 KiB")
        terms.validate_languages(self.required_languages())
        created_at = utc_now().isoformat()
        payload = canonical_json(terms.model_dump(mode="json"))
        with self.repository.transaction() as db:
            row = db.execute(
                "SELECT current_revision FROM ticker_monitoring_terms WHERE ticker=?",
                (terms.ticker,),
            ).fetchone()
            current = int(row[0]) if row else 0
            if terms.expected_revision != current:
                raise ValueError(f"revision conflict: current={current}")
            revision = current + 1
            db.execute(
                "INSERT INTO ticker_monitoring_term_revisions VALUES(?,?,?,?,?,?)",
                (terms.ticker, revision, payload, sha256_text(payload), actor, created_at),
            )
            db.execute(
                "INSERT INTO ticker_monitoring_terms VALUES(?,?,?) "
                "ON CONFLICT(ticker) DO UPDATE SET "
                "current_revision=excluded.current_revision,updated_at=excluded.updated_at",
                (terms.ticker, revision, created_at),
            )
        return revision

    def get(
        self, ticker: str, revision: int | None = None
    ) -> tuple[int, TickerMonitoringTerms] | None:
        with self.repository._connect() as db:
            if revision is None:
                row = db.execute(
                    "SELECT current_revision FROM ticker_monitoring_terms WHERE ticker=?",
                    (ticker.upper(),),
                ).fetchone()
                if not row:
                    return None
                revision = int(row[0])
            row = db.execute(
                "SELECT config_json FROM ticker_monitoring_term_revisions "
                "WHERE ticker=? AND revision=?",
                (ticker.upper(), revision),
            ).fetchone()
        return (revision, TickerMonitoringTerms.model_validate(json.loads(row[0]))) if row else None

    def history(self, ticker: str) -> list[dict[str, str | int]]:
        with self.repository._connect() as db:
            rows = db.execute(
                "SELECT revision,config_hash,actor,created_at "
                "FROM ticker_monitoring_term_revisions WHERE ticker=? "
                "ORDER BY revision DESC",
                (ticker.upper(),),
            ).fetchall()
        return [dict(row) for row in rows]
