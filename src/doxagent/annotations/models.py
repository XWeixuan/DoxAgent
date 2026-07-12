"""Contracts for lightweight citation and event-time annotations."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, computed_field


class AnnotationModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CitationAnnotation(AnnotationModel):
    annotation_id: str
    run_id: str
    task_id: str
    result_id: str
    payload_path: str
    text_hash: str
    span_start: int
    span_end: int
    observation_block_id: str
    created_at: datetime


class TimeAnnotation(AnnotationModel):
    annotation_id: str
    run_id: str
    task_id: str
    result_id: str
    payload_path: str
    text_hash: str
    span_start: int
    span_end: int
    occurred_at: str | None = None
    published_at: str | None = None
    created_at: datetime


class ProcessedText(AnnotationModel):
    payload_path: str
    raw_tagged_text: str
    plain_text: str
    citation_annotations: list[CitationAnnotation] = Field(default_factory=list)
    time_annotations: list[TimeAnnotation] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class AnnotationMetrics(AnnotationModel):
    annotatable_text_count: int = 0
    cited_text_count: int = 0
    citation_tag_count: int = 0
    resolved_citation_count: int = 0
    invalid_alias_count: int = 0
    time_tag_count: int = 0
    valid_time_count: int = 0
    invalid_time_count: int = 0
    temporal_candidate_count: int = 0
    time_annotated_text_count: int = 0

    @computed_field
    @property
    def citation_resolution_rate(self) -> float:
        if self.citation_tag_count == 0:
            return 1.0
        return self.resolved_citation_count / self.citation_tag_count

    @computed_field
    @property
    def citation_coverage(self) -> float:
        if self.annotatable_text_count == 0:
            return 1.0
        return self.cited_text_count / self.annotatable_text_count

    @computed_field
    @property
    def time_validity_rate(self) -> float:
        if self.time_tag_count == 0:
            return 1.0
        return self.valid_time_count / self.time_tag_count

    @computed_field
    @property
    def time_annotation_coverage(self) -> float:
        if self.temporal_candidate_count == 0:
            return 1.0
        return self.time_annotated_text_count / self.temporal_candidate_count

    @computed_field
    @property
    def invalid_alias_rate(self) -> float:
        if self.citation_tag_count == 0:
            return 0.0
        return self.invalid_alias_count / self.citation_tag_count

    @computed_field
    @property
    def invalid_time_rate(self) -> float:
        if self.time_tag_count == 0:
            return 0.0
        return self.invalid_time_count / self.time_tag_count

    @property
    def observation_locator_fidelity(self) -> float:
        return self.citation_resolution_rate


class AnnotationBatch(AnnotationModel):
    run_id: str
    task_id: str
    result_id: str
    plain_payload: dict
    processed_texts: list[ProcessedText] = Field(default_factory=list)
    citations: list[CitationAnnotation] = Field(default_factory=list)
    times: list[TimeAnnotation] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    metrics: AnnotationMetrics = Field(default_factory=AnnotationMetrics)
