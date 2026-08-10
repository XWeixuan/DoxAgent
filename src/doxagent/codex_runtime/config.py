"""Validated configuration assembly for the isolated Codex runtime."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, TypeAdapter, model_validator

from doxagent.settings import DoxAgentSettings


class CodexRuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    worker_base_url: HttpUrl
    worker_bearer_token: str | None
    capability_secret: str | None
    workspace_root: Path
    storage_mode: Literal["memory", "sqlite", "hybrid", "postgres"]
    sqlite_path: Path
    remote_runtime_storage_enabled: bool
    hybrid_local_mirror_enabled: bool = True
    database_url: str | None
    published_storage_url: HttpUrl | None = None
    published_storage_secret_key: str | None = None
    published_storage_bucket: str = "codex-published-documents"
    model: str
    model_provider: str | None
    reasoning_effort: Literal["low", "medium", "high", "xhigh", "max"]
    node_timeout_seconds: int = Field(ge=30)
    node_max_attempts: int = Field(ge=1, le=5)
    max_subagents: int = Field(ge=0, le=2)

    @model_validator(mode="after")
    def validate_enabled_secrets(self) -> CodexRuntimeConfig:
        if self.enabled:
            if not self.worker_bearer_token or len(self.worker_bearer_token) < 24:
                raise ValueError(
                    "enabled Codex worker requires a bearer token of at least 24 characters"
                )
            if not self.capability_secret or len(self.capability_secret.encode("utf-8")) < 32:
                raise ValueError(
                    "enabled Codex worker requires a capability secret of at least 32 bytes"
                )
        if self.storage_mode in {"hybrid", "postgres"}:
            if not self.remote_runtime_storage_enabled:
                raise ValueError(
                    f"{self.storage_mode} Codex runtime storage requires explicit "
                    "DOXAGENT_CODEX_REMOTE_RUNTIME_STORAGE_ENABLED=true"
                )
            if not self.database_url:
                raise ValueError(
                    f"{self.storage_mode} Codex runtime storage requires DOXAGENT_DATABASE_URL"
                )
        if (self.published_storage_url is None) != (
            self.published_storage_secret_key is None
        ):
            raise ValueError(
                "published Storage URL and secret key must be configured together"
            )
        return self

    @classmethod
    def from_settings(cls, settings: DoxAgentSettings) -> CodexRuntimeConfig:
        return cls(
            enabled=settings.codex_d1_v2_enabled,
            worker_base_url=TypeAdapter(HttpUrl).validate_python(settings.codex_worker_base_url),
            worker_bearer_token=settings.codex_worker_bearer_token,
            capability_secret=settings.codex_capability_secret,
            workspace_root=Path(settings.codex_workspace_root),
            storage_mode=settings.codex_runtime_storage_mode,
            sqlite_path=Path(settings.codex_runtime_sqlite_path),
            remote_runtime_storage_enabled=settings.codex_remote_runtime_storage_enabled,
            hybrid_local_mirror_enabled=settings.codex_hybrid_local_mirror_enabled,
            database_url=settings.database_url,
            published_storage_url=(
                TypeAdapter(HttpUrl).validate_python(settings.codex_published_storage_url)
                if settings.codex_published_storage_url
                else None
            ),
            published_storage_secret_key=settings.codex_published_storage_secret_key,
            published_storage_bucket=settings.codex_published_storage_bucket,
            model=settings.codex_model,
            model_provider=settings.codex_model_provider,
            reasoning_effort=settings.codex_reasoning_effort,
            node_timeout_seconds=settings.codex_node_timeout_seconds,
            node_max_attempts=settings.codex_node_max_attempts,
            max_subagents=settings.codex_max_subagents,
        )
