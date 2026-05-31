"""Runtime settings for DoxAgent."""

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class DoxAgentSettings(BaseSettings):
    """Environment-backed settings.

    Defaults stay mock-first and memory-backed so local tests never connect to
    external services unless explicitly configured.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    storage_mode: Literal["memory", "postgres"] = Field(
        default="memory",
        validation_alias="DOXAGENT_STORAGE_MODE",
    )
    database_url: str | None = Field(default=None, validation_alias="DOXAGENT_DATABASE_URL")

    tool_http_timeout_seconds: float = Field(
        default=15.0,
        validation_alias="DOXAGENT_TOOL_HTTP_TIMEOUT_SECONDS",
    )

    doxatlas_tool_base_url: str | None = Field(
        default=None,
        validation_alias="DOXATLAS_TOOL_BASE_URL",
    )
    doxatlas_tool_server_token: str | None = Field(
        default=None,
        validation_alias="DOXATLAS_TOOL_SERVER_TOKEN",
    )
    doxatlas_cache_ttl_seconds: int = Field(
        default=300,
        validation_alias="DOXATLAS_CACHE_TTL_SECONDS",
    )

    fred_api_key: str | None = Field(default=None, validation_alias="FRED_API_KEY")
    fred_base_url: str = Field(
        default="https://api.stlouisfed.org", validation_alias="FRED_BASE_URL"
    )
    bls_api_key: str | None = Field(default=None, validation_alias="BLS_API_KEY")
    bls_base_url: str = Field(default="https://api.bls.gov", validation_alias="BLS_BASE_URL")
    bea_api_key: str | None = Field(default=None, validation_alias="BEA_API_KEY")
    bea_base_url: str = Field(default="https://apps.bea.gov", validation_alias="BEA_BASE_URL")
    macro_cache_ttl_seconds: int = Field(default=86_400, validation_alias="MACRO_CACHE_TTL_SECONDS")

    fed_fomc_calendar_url: str = Field(
        default="https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
        validation_alias="FED_FOMC_CALENDAR_URL",
    )
    fed_cache_ttl_seconds: int = Field(default=86_400, validation_alias="FED_CACHE_TTL_SECONDS")

    alpha_vantage_api_key: str | None = Field(
        default=None,
        validation_alias="ALPHA_VANTAGE_API_KEY",
    )
    alpha_vantage_base_url: str = Field(
        default="https://www.alphavantage.co/query",
        validation_alias="ALPHA_VANTAGE_BASE_URL",
    )
    alpha_cache_ttl_seconds: int = Field(default=86_400, validation_alias="ALPHA_CACHE_TTL_SECONDS")

    fmp_api_key: str | None = Field(default=None, validation_alias="FMP_API_KEY")
    fmp_base_url: str = Field(
        default="https://financialmodelingprep.com", validation_alias="FMP_BASE_URL"
    )
    fmp_cache_ttl_seconds: int = Field(default=21_600, validation_alias="FMP_CACHE_TTL_SECONDS")

    finnhub_api_key: str | None = Field(default=None, validation_alias="FINNHUB_API_KEY")
    finnhub_base_url: str = Field(
        default="https://finnhub.io/api/v1", validation_alias="FINNHUB_BASE_URL"
    )
    finnhub_ws_url: str = Field(default="wss://ws.finnhub.io", validation_alias="FINNHUB_WS_URL")
    finnhub_cache_ttl_seconds: int = Field(
        default=86_400, validation_alias="FINNHUB_CACHE_TTL_SECONDS"
    )
    finnhub_max_stream_seconds: int = Field(
        default=10,
        validation_alias="FINNHUB_MAX_STREAM_SECONDS",
    )
    finnhub_max_stream_events: int = Field(
        default=100,
        validation_alias="FINNHUB_MAX_STREAM_EVENTS",
    )

    tavily_api_key: str | None = Field(default=None, validation_alias="TAVILY_API_KEY")
    tavily_base_url: str = Field(
        default="https://api.tavily.com", validation_alias="TAVILY_BASE_URL"
    )
    tavily_cache_ttl_seconds: int = Field(
        default=21_600, validation_alias="TAVILY_CACHE_TTL_SECONDS"
    )

    polymarket_gamma_base_url: str = Field(
        default="https://gamma-api.polymarket.com",
        validation_alias="POLYMARKET_GAMMA_BASE_URL",
    )
    polymarket_cache_ttl_seconds: int = Field(
        default=300,
        validation_alias="POLYMARKET_CACHE_TTL_SECONDS",
    )

    sec_data_base_url: str = Field(
        default="https://data.sec.gov", validation_alias="SEC_DATA_BASE_URL"
    )
    sec_user_agent: str = Field(
        default="DoxAgent/0.1 contact@example.com", validation_alias="SEC_USER_AGENT"
    )
    sec_cache_ttl_seconds: int = Field(default=86_400, validation_alias="SEC_CACHE_TTL_SECONDS")

    def require_database_url(self) -> str:
        if not self.database_url:
            raise ValueError("DOXAGENT_DATABASE_URL is required when storage mode is postgres.")
        return self.database_url
