from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, model_validator

from ..schema import BusModel, SourceDefinition, TickerSourceBinding, canonical_json, sha256_text


class IrEndpointSpec(BusModel):
    endpoint_id: str
    owner_ticker: str
    display_name: str
    entry_url: str
    method: Literal["rss", "json", "html", "browser"]
    parser_ref: Literal["rss", "micron", "coherent", "nebius", "ceg", "rocketlab", "snowflake"]
    parser_parameters: dict = Field(default_factory=dict)
    allowed_article_hosts: list[str]
    fallback_urls: list[str] = Field(default_factory=list)
    date_timezone: str = "America/New_York"
    enabled: bool = True
    target_interval_seconds: int = Field(default=30, ge=10)
    revision: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def public_urls(self):
        for url in [self.entry_url, *self.fallback_urls]:
            parsed = urlsplit(url)
            if parsed.scheme != "https" or parsed.hostname not in self.allowed_article_hosts:
                raise ValueError("IR entry requires an approved HTTPS host")
        if not set(self.parser_parameters.get("urllib_hosts", [])).issubset(
            self.allowed_article_hosts
        ):
            raise ValueError("IR transport host must be approved")
        return self

    @property
    def fingerprint(self):
        return sha256_text(canonical_json(self.model_dump(mode="json")))


class IrEndpointState(BusModel):
    binding_id: str
    endpoint_id: str
    config_revision: str = ""
    bootstrap_complete: bool = False
    checkpoint: dict = Field(default_factory=dict)
    etag: str | None = None
    last_modified: str | None = None
    representation_hash: str | None = None
    next_due_at: datetime | None = None
    next_dispatch_at: datetime | None = None
    last_attempt_at: datetime | None = None
    last_success_at: datetime | None = None
    last_change_at: datetime | None = None
    status: Literal["never_polled", "succeeded", "failed", "deferred"] = "never_polled"
    consecutive_failures: int = 0
    last_error_code: str | None = None
    last_error_message: str | None = None
    latency_ms: int = 0
    http_status: int | None = None
    bytes_received: int = 0
    method_used: str | None = None
    updated_at: datetime | None = None


def endpoints(source: SourceDefinition, binding: TickerSourceBinding):
    catalog = {
        value["endpoint_id"]: IrEndpointSpec.model_validate(value)
        for value in source.default_parameters.get("endpoints", [])
    }
    ids = binding.source_parameters.get("endpoint_ids", [])
    if not ids or len(set(ids)) != len(ids):
        raise ValueError("IR binding requires unique endpoint_ids")
    selected = []
    for key in ids:
        spec = catalog.get(key)
        if spec is None or spec.owner_ticker != binding.ticker:
            raise ValueError(f"IR endpoint does not belong to {binding.ticker}: {key}")
        selected.append(spec)
    return selected
