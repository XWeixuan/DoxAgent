"""Environment-only configuration for the standalone CDECR module."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class CDECRSettings(BaseSettings):
    """CDECR settings; deliberately independent of DoxAgent settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
    )

    supabase_url: str | None = Field(default=None, alias="CDECR_SUPABASE_URL")
    supabase_publishable_key: SecretStr | None = Field(
        default=None, alias="CDECR_SUPABASE_PUBLISHABLE_KEY"
    )
    sqlite_path: Path = Field(default=Path(".tmp/cdecr/cdecr.sqlite3"), alias="CDECR_SQLITE_PATH")
    dashscope_api_key: SecretStr | None = Field(default=None, alias="DASHSCOPE_API_KEY")
    dashscope_fallback_api_key: SecretStr | None = Field(
        default=None, alias="DASHSCOPE_FALLBACK_API_KEY"
    )
    dashscope_fallback_api_keys_csv: SecretStr | None = Field(
        default=None, alias="DASHSCOPE_FALLBACK_API_KEYS"
    )
    dashscope_base_url: str = Field(
        default="https://dashscope.aliyuncs.com/compatible-mode/v1",
        alias="CDECR_DASHSCOPE_BASE_URL",
    )
    deepseek_api_key: SecretStr | None = Field(default=None, alias="DEEPSEEK_API_KEY")
    deepseek_base_url: str = Field(
        default="https://api.deepseek.com/beta",
        alias="CDECR_DEEPSEEK_BASE_URL",
    )
    model_m2_provider: Literal["dashscope", "deepseek"] = Field(
        default="dashscope", alias="CDECR_M2_PROVIDER"
    )
    model_m3_provider: Literal["dashscope", "deepseek"] = Field(
        default="dashscope", alias="CDECR_M3_PROVIDER"
    )
    model_m4_provider: Literal["dashscope", "deepseek"] = Field(
        default="dashscope", alias="CDECR_M4_PROVIDER"
    )
    model_m2_reasoning_effort: Literal["none", "low", "high", "max"] = Field(
        default="none", alias="CDECR_M2_REASONING_EFFORT"
    )
    model_m3_reasoning_effort: Literal["none", "low", "high", "max"] = Field(
        default="low", alias="CDECR_M3_REASONING_EFFORT"
    )
    model_m4_reasoning_effort: Literal["none", "low", "high", "max"] = Field(
        default="high", alias="CDECR_M4_REASONING_EFFORT"
    )
    model_m2_strict: bool = Field(default=False, alias="CDECR_M2_STRICT")
    model_m3_strict: bool = Field(default=False, alias="CDECR_M3_STRICT")
    model_m4_strict: bool = Field(default=False, alias="CDECR_M4_STRICT")
    model_m1: str = Field(default="qwen3.7-text-embedding", alias="CDECR_MODEL_M1")
    model_m2: str = Field(default="deepseek-v4-flash-0731", alias="CDECR_MODEL_M2")
    model_m3: str = Field(default="deepseek-v4-flash-0731", alias="CDECR_MODEL_M3")
    model_m4: str = Field(default="deepseek-v4-flash-0731", alias="CDECR_MODEL_M4")
    relevance_filter_mode: Literal["off", "shadow", "enforce"] = Field(
        default="enforce", alias="CDECR_RELEVANCE_FILTER_MODE"
    )
    relevance_target_profiles: dict[str, str] = Field(
        default_factory=dict,
        alias="CDECR_RELEVANCE_TARGET_PROFILES",
        description=(
            "JSON object mapping ticker to 'Company (TICKER); concise business description'."
        ),
    )
    atomic_hard_cannot_link_mode: Literal["enforce", "shadow", "off"] = Field(
        default="enforce",
        alias="CDECR_ATOMIC_HARD_CANNOT_LINK_MODE",
    )
    n9_wire_protocol: Literal["on"] = Field(
        default="on",
        alias="CDECR_N9_WIRE_PROTOCOL",
    )
    field_epoch_planned_batching: bool = Field(
        default=True, alias="CDECR_FIELD_EPOCH_PLANNED_BATCHING"
    )
    grounder_safe_normalization: bool = Field(
        default=True, alias="CDECR_GROUNDER_SAFE_NORMALIZATION"
    )
    grounder_primary_normalization: Literal["off", "shadow"] = Field(
        default="shadow", alias="CDECR_GROUNDER_PRIMARY_NORMALIZATION"
    )
    n9_overlap_batch_packing: bool = Field(
        default=False, alias="CDECR_N9_OVERLAP_BATCH_PACKING"
    )
    parent_compact_wire_dto: bool = Field(
        default=True, alias="CDECR_PARENT_COMPACT_WIRE_DTO"
    )
    grounder_issue_protocol: Literal["legacy", "shadow", "canary", "on"] = Field(
        default="legacy",
        alias="CDECR_GROUNDER_ISSUE_PROTOCOL",
    )
    targeted_repair_protocol: Literal["legacy", "shadow", "canary", "on"] = Field(
        default="legacy",
        alias="CDECR_TARGETED_REPAIR",
    )
    embedding_dimensions: int = Field(default=1024, alias="CDECR_EMBEDDING_DIMENSIONS")
    http_timeout_seconds: float = Field(default=30.0, alias="CDECR_HTTP_TIMEOUT_SECONDS")
    model_timeout_seconds: float = Field(default=600.0, alias="CDECR_MODEL_TIMEOUT_SECONDS")
    scheduler_m1_concurrency: int = Field(
        default=64, ge=1, le=256, alias="CDECR_SCHEDULER_M1_CONCURRENCY"
    )
    scheduler_m2_concurrency: int = Field(
        default=128, ge=1, le=256, alias="CDECR_SCHEDULER_M2_CONCURRENCY"
    )
    scheduler_m3_concurrency: int = Field(
        default=160, ge=1, le=256, alias="CDECR_SCHEDULER_M3_CONCURRENCY"
    )
    scheduler_m4_concurrency: int = Field(
        default=96, ge=1, le=256, alias="CDECR_SCHEDULER_M4_CONCURRENCY"
    )
    structured_request_start_interval_seconds: float = Field(
        default=0.0,
        ge=0.0,
        le=10.0,
        alias="CDECR_STRUCTURED_REQUEST_START_INTERVAL_SECONDS",
    )
    structured_provider_target_concurrency: int = Field(
        default=100,
        ge=1,
        le=500,
        alias="CDECR_STRUCTURED_PROVIDER_TARGET_CONCURRENCY",
    )
    structured_provider_hard_concurrency: int = Field(
        default=160,
        ge=1,
        le=500,
        alias="CDECR_STRUCTURED_PROVIDER_HARD_CONCURRENCY",
    )
    structured_provider_start_rate: float = Field(
        default=50.0,
        ge=1.0,
        le=500.0,
        alias="CDECR_STRUCTURED_PROVIDER_START_RATE",
    )
    structured_provider_initial_burst: int = Field(
        default=80,
        ge=1,
        le=500,
        alias="CDECR_STRUCTURED_PROVIDER_INITIAL_BURST",
    )
    structured_provider_max_retries: int = Field(
        default=2, ge=0, le=3, alias="CDECR_PROVIDER_MAX_RETRIES"
    )
    provider_key_rotation_enabled: bool = Field(
        default=False, alias="CDECR_PROVIDER_KEY_ROTATION_ENABLED"
    )
    provider_auto_quarantine_enabled: bool = Field(
        default=False, alias="CDECR_PROVIDER_AUTO_QUARANTINE_ENABLED"
    )
    provider_first_pause_seconds: float = Field(
        default=8.0, ge=0.0, le=300.0, alias="CDECR_PROVIDER_FIRST_PAUSE_SECONDS"
    )
    provider_second_pause_seconds: float = Field(
        default=20.0, ge=0.0, le=600.0, alias="CDECR_PROVIDER_SECOND_PAUSE_SECONDS"
    )
    provider_half_open_probes: int = Field(
        default=2, ge=1, le=16, alias="CDECR_PROVIDER_HALF_OPEN_PROBES"
    )
    provider_recovery_start_rate: float = Field(
        default=5.0, ge=0.1, le=500.0, alias="CDECR_PROVIDER_RECOVERY_START_RATE"
    )
    provider_recovery_initial_concurrency: int = Field(
        default=8, ge=1, le=160, alias="CDECR_PROVIDER_RECOVERY_INITIAL_CONCURRENCY"
    )
    structured_provider_key_quarantine_seconds: int = Field(
        default=14400, ge=60, le=86400, alias="CDECR_STRUCTURED_PROVIDER_KEY_QUARANTINE_SECONDS"
    )
    document_workers: int = Field(default=120, ge=1, le=256, alias="CDECR_DOCUMENT_WORKERS")
    document_block_concurrency: int = Field(
        default=24, ge=1, le=64, alias="CDECR_DOCUMENT_BLOCK_CONCURRENCY"
    )
    field_active_requests: int = Field(
        default=100, ge=1, le=160, alias="CDECR_FIELD_ACTIVE_REQUESTS"
    )
    n9_active_requests: int = Field(default=72, ge=1, le=160, alias="CDECR_N9_ACTIVE_REQUESTS")
    n9_escalation_active_requests: int = Field(
        default=32, ge=1, le=160, alias="CDECR_N9_ESCALATION_ACTIVE_REQUESTS"
    )
    n9_late_active_requests: int = Field(
        default=24, ge=1, le=160, alias="CDECR_N9_LATE_ACTIVE_REQUESTS"
    )
    parent_induction_active_requests: int = Field(
        default=96, ge=1, le=160, alias="CDECR_PARENT_INDUCTION_ACTIVE_REQUESTS"
    )
    parent_induction_max_documents: int = Field(
        default=4, ge=1, le=16, alias="CDECR_PARENT_INDUCTION_MAX_DOCUMENTS"
    )
    parent_induction_max_slices: int = Field(
        default=48, ge=1, le=96, alias="CDECR_PARENT_INDUCTION_MAX_SLICES"
    )
    parent_context_soft_token_budget: int = Field(
        default=6000, ge=1000, le=32000, alias="CDECR_PARENT_CONTEXT_SOFT_TOKEN_BUDGET"
    )
    package_v3_batch_size: int = Field(
        default=200, ge=1, le=200, alias="CDECR_PACKAGE_V3_BATCH_SIZE"
    )
    package_v3_model: Literal["deepseek-v4-flash-0731"] = Field(
        default="deepseek-v4-flash-0731", alias="CDECR_PACKAGE_V3_MODEL"
    )
    package_v3_reasoning_effort: Literal["none", "low", "high", "max"] = Field(
        default="low", alias="CDECR_PACKAGE_V3_REASONING_EFFORT"
    )
    package_v3_description_reasoning_effort: Literal["none", "low", "high", "max"] = Field(
        default="none", alias="CDECR_PACKAGE_V3_DESCRIPTION_REASONING_EFFORT"
    )
    atomic_cosine_backend: Literal["matrix", "scalar"] = Field(
        default="matrix", alias="CDECR_ATOMIC_COSINE_BACKEND"
    )
    bulk_registry_read_mode: Literal["snapshot", "locked"] = Field(
        default="snapshot", alias="CDECR_BULK_REGISTRY_READ_MODE"
    )
    package_v3_context_token_budget: int = Field(
        default=100000,
        ge=8000,
        le=200000,
        alias="CDECR_PACKAGE_V3_CONTEXT_TOKEN_BUDGET",
    )
    package_v3_description_token_budget: int = Field(
        default=32000,
        ge=4000,
        le=100000,
        alias="CDECR_PACKAGE_V3_DESCRIPTION_TOKEN_BUDGET",
    )
    package_v3_context_reserve_tokens: int = Field(
        default=8000,
        ge=1000,
        le=64000,
        alias="CDECR_PACKAGE_V3_CONTEXT_RESERVE_TOKENS",
    )
    package_v3_description_active_requests: int = Field(
        default=16,
        ge=1,
        le=64,
        alias="CDECR_PACKAGE_V3_DESCRIPTION_ACTIVE_REQUESTS",
    )
    package_v3_registry_scope: str = Field(
        default="cdecr-default", alias="CDECR_PACKAGE_V3_REGISTRY_SCOPE"
    )
    item_repair_active_requests: int = Field(
        default=16, ge=1, le=64, alias="CDECR_ITEM_REPAIR_ACTIVE_REQUESTS"
    )
    dreamer_active_requests: int = Field(
        default=100, ge=1, le=160, alias="CDECR_DREAMER_ACTIVE_REQUESTS"
    )
    dreamer_relevance_active_requests: int = Field(
        default=100,
        ge=1,
        le=160,
        alias="CDECR_DREAMER_RELEVANCE_ACTIVE_REQUESTS",
    )
    grounder_active_requests: int = Field(
        default=100, ge=1, le=160, alias="CDECR_GROUNDER_ACTIVE_REQUESTS"
    )
    judge_active_requests: int = Field(
        default=64, ge=1, le=160, alias="CDECR_JUDGE_ACTIVE_REQUESTS"
    )
    writer_queue_low_watermark: int = Field(
        default=1000, ge=1, le=10000, alias="CDECR_WRITER_QUEUE_LOW_WATERMARK"
    )
    writer_queue_high_watermark: int = Field(
        default=5000, ge=1, le=10000, alias="CDECR_WRITER_QUEUE_HIGH_WATERMARK"
    )
    writer_queue_hard_limit: int = Field(
        default=10000, ge=1, le=100000, alias="CDECR_WRITER_QUEUE_HARD_LIMIT"
    )
    batch_audit_write: bool = Field(default=True, alias="CDECR_BATCH_AUDIT_WRITE")
    stage_read_snapshot: bool = Field(default=True, alias="CDECR_STAGE_READ_SNAPSHOT")
    chunked_stage_apply: bool = Field(default=True, alias="CDECR_CHUNKED_STAGE_APPLY")
    batch_task_ledger: bool = Field(default=True, alias="CDECR_BATCH_TASK_LEDGER")
    embedding_batch_executor: bool = Field(
        default=True, alias="CDECR_EMBEDDING_BATCH_EXECUTOR"
    )
    atomic_late_convergence: bool = Field(default=True, alias="CDECR_ATOMIC_LATE_CONVERGENCE")
    atomic_late_task_cap: int = Field(default=48, ge=0, le=128, alias="CDECR_ATOMIC_LATE_TASK_CAP")

    @model_validator(mode="after")
    def validate_capacity_ordering(self) -> CDECRSettings:
        if self.structured_provider_hard_concurrency < self.structured_provider_target_concurrency:
            raise ValueError("structured provider hard concurrency must be >= target concurrency")
        if not (
            self.writer_queue_low_watermark
            <= self.writer_queue_high_watermark
            <= self.writer_queue_hard_limit
        ):
            raise ValueError("writer queue watermarks must satisfy low <= high <= hard")
        for ticker, profile in self.relevance_target_profiles.items():
            normalized_ticker = ticker.strip().upper()
            if not normalized_ticker or not profile.strip():
                raise ValueError("relevance target profiles must not contain blank keys or values")
            if normalized_ticker.casefold() not in profile.casefold():
                raise ValueError(
                    f"relevance target profile for {normalized_ticker} must include its ticker"
                )
        return self

    def require_supabase(self) -> tuple[str, str]:
        if not self.supabase_url or self.supabase_publishable_key is None:
            raise ValueError("CDECR_SUPABASE_URL and CDECR_SUPABASE_PUBLISHABLE_KEY are required")
        return self.supabase_url.rstrip("/"), self.supabase_publishable_key.get_secret_value()

    def require_dashscope(self) -> str:
        if self.dashscope_api_key is None:
            raise ValueError("DASHSCOPE_API_KEY is required")
        return self.dashscope_api_key.get_secret_value()

    def require_deepseek(self) -> str:
        if self.deepseek_api_key is None:
            raise ValueError("DEEPSEEK_API_KEY is required")
        return self.deepseek_api_key.get_secret_value()

    def dashscope_fallback_api_keys(self) -> tuple[str, ...]:
        """Return ordered, de-duplicated fallback keys without the primary key."""

        primary = (
            self.dashscope_api_key.get_secret_value().strip()
            if self.dashscope_api_key is not None
            else None
        )
        candidates: list[str] = []
        if self.dashscope_fallback_api_key is not None:
            candidates.append(self.dashscope_fallback_api_key.get_secret_value())
        if self.dashscope_fallback_api_keys_csv is not None:
            raw = self.dashscope_fallback_api_keys_csv.get_secret_value()
            candidates.extend(raw.replace(";", ",").split(","))

        result: list[str] = []
        seen = {primary} if primary else set()
        for candidate in candidates:
            key = candidate.strip()
            if key and key not in seen:
                seen.add(key)
                result.append(key)
        return tuple(result)
