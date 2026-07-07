"""Runtime settings for DoxAgent."""

import os
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

    dashscope_api_key: str | None = Field(default=None, validation_alias="DASHSCOPE_API_KEY")
    dashscope_fallback_api_key: str | None = Field(
        default=None,
        validation_alias="DASHSCOPE_FALLBACK_API_KEY",
    )
    dashscope_fallback_api_keys_csv: str | None = Field(
        default=None,
        validation_alias="DASHSCOPE_FALLBACK_API_KEYS",
    )
    dashscope_base_url: str = Field(
        default="https://dashscope.aliyuncs.com/api/v2/apps/protocols/compatible-mode/v1",
        validation_alias="DASHSCOPE_BASE_URL",
    )
    dashscope_chat_base_url: str = Field(
        default="https://dashscope.aliyuncs.com/compatible-mode/v1",
        validation_alias="DASHSCOPE_CHAT_BASE_URL",
    )
    dashscope_model: str = Field(default="qwen3.7-plus", validation_alias="DASHSCOPE_MODEL")
    dashscope_enable_thinking: bool = Field(
        default=True,
        validation_alias="DASHSCOPE_ENABLE_THINKING",
    )
    dashscope_thinking_budget: int | None = Field(
        default=None,
        ge=1,
        validation_alias="DASHSCOPE_THINKING_BUDGET",
    )

    langsmith_tracing: bool = Field(default=False, validation_alias="LANGSMITH_TRACING")
    langsmith_endpoint: str | None = Field(default=None, validation_alias="LANGSMITH_ENDPOINT")
    langsmith_api_key: str | None = Field(default=None, validation_alias="LANGSMITH_API_KEY")
    langsmith_project: str | None = Field(default=None, validation_alias="LANGSMITH_PROJECT")

    tool_http_timeout_seconds: float = Field(
        default=15.0,
        validation_alias="DOXAGENT_TOOL_HTTP_TIMEOUT_SECONDS",
    )
    model_request_timeout_seconds: float = Field(
        default=300.0,
        gt=0,
        validation_alias="DOXAGENT_MODEL_REQUEST_TIMEOUT_SECONDS",
    )
    model_usage_storage_mode: Literal["memory", "sqlite"] = Field(
        default="sqlite",
        validation_alias="DOXAGENT_MODEL_USAGE_STORAGE_MODE",
    )
    model_usage_sqlite_path: str = Field(
        default=".tmp/model_usage.sqlite3",
        validation_alias="DOXAGENT_MODEL_USAGE_SQLITE_PATH",
    )
    model_pricing_config_path: str | None = Field(
        default=None,
        validation_alias="DOXAGENT_MODEL_PRICING_CONFIG_PATH",
    )
    model_pricing_discount_rate: float = Field(
        default=0.45,
        ge=0,
        validation_alias="DOXAGENT_MODEL_PRICING_DISCOUNT_RATE",
    )
    model_pricing_cny_usd_rate: float = Field(
        default=6.8,
        gt=0,
        validation_alias="DOXAGENT_MODEL_PRICING_CNY_USD_RATE",
    )
    react_tool_call_timeout_seconds: float = Field(
        default=180.0,
        gt=0,
        validation_alias="DOXAGENT_REACT_TOOL_CALL_TIMEOUT_SECONDS",
    )
    workflow_agent_stale_after_seconds: int = Field(
        default=1_800,
        ge=1,
        validation_alias="DOXAGENT_WORKFLOW_AGENT_STALE_AFTER_SECONDS",
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

    anysearch_api_key: str | None = Field(default=None, validation_alias="ANYSEARCH_API_KEY")
    anysearch_base_url: str = Field(
        default="https://api.anysearch.com", validation_alias="ANYSEARCH_BASE_URL"
    )
    anysearch_cache_ttl_seconds: int = Field(
        default=21_600, validation_alias="ANYSEARCH_CACHE_TTL_SECONDS"
    )

    monitoring_storage_mode: Literal["memory", "sqlite"] = Field(
        default="sqlite",
        validation_alias="DOXAGENT_MONITORING_STORAGE_MODE",
    )
    monitoring_sqlite_path: str = Field(
        default=".tmp/monitoring_message_bus.sqlite3",
        validation_alias="DOXAGENT_MONITORING_SQLITE_PATH",
    )
    monitoring_auto_media_enrichment_enabled: bool = Field(
        default=True,
        validation_alias="DOXAGENT_MONITORING_AUTO_MEDIA_ENRICHMENT_ENABLED",
    )
    monitoring_auto_media_enrichment_limit: int = Field(
        default=5,
        validation_alias="DOXAGENT_MONITORING_AUTO_MEDIA_ENRICHMENT_LIMIT",
    )
    monitoring_auto_media_enrichment_concurrency: int = Field(
        default=2,
        validation_alias="DOXAGENT_MONITORING_AUTO_MEDIA_ENRICHMENT_CONCURRENCY",
    )
    benzinga_api_key: str | None = Field(default=None, validation_alias="BENZINGA_API_KEY")
    benzinga_news_base_url: str = Field(
        default="https://api.benzinga.com",
        validation_alias="BENZINGA_NEWS_BASE_URL",
    )
    stocktwits_rapidapi_key: str | None = Field(
        default=None,
        validation_alias="STOCKTWITS_RAPIDAPI_KEY",
    )
    stocktwits_rapidapi_fallback_key: str | None = Field(
        default=None,
        validation_alias="STOCKTWITS_RAPIDAPI_FALLBACK_KEY",
    )
    stocktwits_rapidapi_base_url: str = Field(
        default="https://stocktwits-sentiment-message-analytics-api.p.rapidapi.com",
        validation_alias="STOCKTWITS_RAPIDAPI_BASE_URL",
    )
    stocktwits_rapidapi_host: str | None = Field(
        default=None,
        validation_alias="STOCKTWITS_RAPIDAPI_HOST",
    )
    stocktwits_public_base_url: str = Field(
        default="https://api.stocktwits.com/api/2",
        validation_alias="STOCKTWITS_PUBLIC_BASE_URL",
    )
    stocktwits_public_path_template: str = Field(
        default="/streams/symbol/{symbol}.json",
        validation_alias="STOCKTWITS_PUBLIC_PATH_TEMPLATE",
    )
    stocktwits_user_agent: str = Field(
        default=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        ),
        validation_alias="STOCKTWITS_USER_AGENT",
    )
    stocktwits_accept_language: str = Field(
        default="en-US,en;q=0.9",
        validation_alias="STOCKTWITS_ACCEPT_LANGUAGE",
    )
    stocktwits_storage_mode: Literal["memory", "sqlite", "postgres"] = Field(
        default="sqlite",
        validation_alias="DOXAGENT_STOCKTWITS_STORAGE_MODE",
    )
    stocktwits_sqlite_path: str = Field(
        default=".tmp/stocktwits_polling.sqlite3",
        validation_alias="DOXAGENT_STOCKTWITS_SQLITE_PATH",
    )
    stocktwits_allow_postgres: bool = Field(
        default=False,
        validation_alias="DOXAGENT_STOCKTWITS_ALLOW_POSTGRES",
    )
    stocktwits_default_symbols: str = Field(
        default="AAPL,MSFT,NVDA,TSLA,AMZN,META,GOOGL,AMD,PLTR,MU",
        validation_alias="DOXAGENT_STOCKTWITS_SYMBOLS",
    )
    stocktwits_target_cadence_seconds: int = Field(
        default=300,
        ge=30,
        validation_alias="DOXAGENT_STOCKTWITS_TARGET_CADENCE_SECONDS",
    )
    stocktwits_hot_cadence_seconds: int = Field(
        default=90,
        ge=30,
        validation_alias="DOXAGENT_STOCKTWITS_HOT_CADENCE_SECONDS",
    )
    stocktwits_scheduler_tick_seconds: int = Field(
        default=30,
        ge=1,
        validation_alias="DOXAGENT_STOCKTWITS_SCHEDULER_TICK_SECONDS",
    )
    stocktwits_page_size: int = Field(
        default=30,
        ge=1,
        validation_alias="DOXAGENT_STOCKTWITS_PAGE_SIZE",
    )
    stocktwits_max_pages_per_crawl: int = Field(
        default=10,
        ge=1,
        validation_alias="DOXAGENT_STOCKTWITS_MAX_PAGES_PER_CRAWL",
    )
    stocktwits_hot_message_threshold: int = Field(
        default=80,
        ge=1,
        validation_alias="DOXAGENT_STOCKTWITS_HOT_MESSAGE_THRESHOLD",
    )
    stocktwits_hot_cooldown_successes: int = Field(
        default=3,
        ge=1,
        validation_alias="DOXAGENT_STOCKTWITS_HOT_COOLDOWN_SUCCESSES",
    )
    stocktwits_min_request_interval_seconds: float = Field(
        default=1.0,
        ge=0,
        validation_alias="DOXAGENT_STOCKTWITS_MIN_REQUEST_INTERVAL_SECONDS",
    )
    stocktwits_request_timeout_seconds: float = Field(
        default=15.0,
        gt=0,
        validation_alias="DOXAGENT_STOCKTWITS_REQUEST_TIMEOUT_SECONDS",
    )
    stocktwits_max_retries: int = Field(
        default=3,
        ge=1,
        validation_alias="DOXAGENT_STOCKTWITS_MAX_RETRIES",
    )
    stocktwits_retry_base_delay_seconds: float = Field(
        default=1.0,
        ge=0,
        validation_alias="DOXAGENT_STOCKTWITS_RETRY_BASE_DELAY_SECONDS",
    )
    tikhub_api_key: str | None = Field(default=None, validation_alias="TIKHUB_API_KEY")
    tikhub_base_url: str = Field(
        default="https://api.tikhub.io",
        validation_alias="TIKHUB_BASE_URL",
    )
    monitoring_rss_user_agent: str = Field(
        default="DoxAgent/0.1 monitoring-message-bus",
        validation_alias="DOXAGENT_MONITORING_RSS_USER_AGENT",
    )
    monitoring_viewer_refresh_seconds: int = Field(
        default=5,
        ge=1,
        validation_alias="DOXAGENT_MONITORING_VIEWER_REFRESH_SECONDS",
    )
    monitoring_poller_sleep_seconds: int = Field(
        default=15,
        ge=1,
        validation_alias="DOXAGENT_MONITORING_POLLER_SLEEP_SECONDS",
    )
    monitoring_remote_ssh_alias: str = Field(
        default="doxagent-hk",
        validation_alias="DOXAGENT_MONITORING_REMOTE_SSH_ALIAS",
    )
    monitoring_remote_path: str = Field(
        default="/root/doxagent",
        validation_alias="DOXAGENT_MONITORING_REMOTE_PATH",
    )
    monitoring_remote_timeout_seconds: int = Field(
        default=45,
        ge=1,
        validation_alias="DOXAGENT_MONITORING_REMOTE_TIMEOUT_SECONDS",
    )
    persistent_runtime_storage_mode: Literal["memory", "sqlite"] = Field(
        default="sqlite",
        validation_alias="DOXAGENT_PERSISTENT_RUNTIME_STORAGE_MODE",
    )
    persistent_runtime_sqlite_path: str = Field(
        default=".tmp/persistent_runtime_execution.sqlite3",
        validation_alias="DOXAGENT_PERSISTENT_RUNTIME_SQLITE_PATH",
    )
    runtime_scheduler_storage_mode: Literal["memory", "sqlite"] = Field(
        default="sqlite",
        validation_alias="DOXAGENT_RUNTIME_SCHEDULER_STORAGE_MODE",
    )
    runtime_scheduler_sqlite_path: str = Field(
        default=".tmp/runtime_scheduler.sqlite3",
        validation_alias="DOXAGENT_RUNTIME_SCHEDULER_SQLITE_PATH",
    )
    runtime_scheduler_loop_sleep_seconds: int = Field(
        default=15,
        ge=1,
        validation_alias="DOXAGENT_RUNTIME_SCHEDULER_LOOP_SLEEP_SECONDS",
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
    sec_min_request_interval_seconds: float = Field(
        default=0.12,
        validation_alias="SEC_MIN_REQUEST_INTERVAL_SECONDS",
    )
    fred_min_request_interval_seconds: float = Field(
        default=0.55,
        validation_alias="FRED_MIN_REQUEST_INTERVAL_SECONDS",
    )
    twelvedata_api_key: str | None = Field(default=None, validation_alias="TWELVEDATA_API_KEY")
    twelvedata_base_url: str = Field(
        default="https://api.twelvedata.com",
        validation_alias="TWELVEDATA_BASE_URL",
    )
    twelvedata_cache_ttl_seconds: int = Field(
        default=86_400,
        validation_alias="TWELVEDATA_CACHE_TTL_SECONDS",
    )

    def require_database_url(self) -> str:
        if not self.database_url:
            raise ValueError("DOXAGENT_DATABASE_URL is required when storage mode is postgres.")
        return self.database_url

    def require_dashscope_api_key(self) -> str:
        if not self.dashscope_api_key:
            raise ValueError("DASHSCOPE_API_KEY is required for agent_runner execution.")
        return self.dashscope_api_key

    def dashscope_fallback_api_keys(self) -> list[str]:
        keys: list[str] = []
        seen = {self.dashscope_api_key} if self.dashscope_api_key else set()
        raw_values = [self.dashscope_fallback_api_key, self.dashscope_fallback_api_keys_csv]
        for raw_value in raw_values:
            if not raw_value:
                continue
            for item in raw_value.replace(";", ",").split(","):
                key = item.strip()
                if not key or key in seen:
                    continue
                seen.add(key)
                keys.append(key)
        return keys

    @property
    def langsmith_enabled(self) -> bool:
        return bool(self.langsmith_tracing and self.langsmith_api_key)

    def apply_langsmith_environment(self) -> None:
        """Expose .env-backed LangSmith settings to the SDK wrappers."""

        os.environ["LANGSMITH_TRACING"] = "true" if self.langsmith_tracing else "false"
        optional_values = {
            "LANGSMITH_ENDPOINT": self.langsmith_endpoint,
            "LANGSMITH_API_KEY": self.langsmith_api_key,
            "LANGSMITH_PROJECT": self.langsmith_project,
        }
        for key, value in optional_values.items():
            if value:
                os.environ[key] = value
