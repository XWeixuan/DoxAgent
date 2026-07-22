"""Environment-only configuration for the standalone CDECR module."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, SecretStr
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
    model_m1: str = Field(default="text-embedding-v4", alias="CDECR_MODEL_M1")
    model_m2: str = Field(default="deepseek-v4-flash", alias="CDECR_MODEL_M2")
    model_m3: str = Field(default="qwen3.7-plus", alias="CDECR_MODEL_M3")
    model_m4: str = Field(default="qwen3.7-max", alias="CDECR_MODEL_M4")
    embedding_dimensions: int = Field(default=1024, alias="CDECR_EMBEDDING_DIMENSIONS")
    http_timeout_seconds: float = Field(default=30.0, alias="CDECR_HTTP_TIMEOUT_SECONDS")
    model_timeout_seconds: float = Field(default=600.0, alias="CDECR_MODEL_TIMEOUT_SECONDS")

    def require_supabase(self) -> tuple[str, str]:
        if not self.supabase_url or self.supabase_publishable_key is None:
            raise ValueError("CDECR_SUPABASE_URL and CDECR_SUPABASE_PUBLISHABLE_KEY are required")
        return self.supabase_url.rstrip("/"), self.supabase_publishable_key.get_secret_value()

    def require_dashscope(self) -> str:
        if self.dashscope_api_key is None:
            raise ValueError("DASHSCOPE_API_KEY is required")
        return self.dashscope_api_key.get_secret_value()

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
