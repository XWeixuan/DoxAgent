import os
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def enabled() -> bool:
    return os.getenv("DOXAGENT_SOURCE_MAINTENANCE_ENABLED", "false").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


class MaintenanceSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DOXAGENT_SOURCE_MAINTENANCE_", extra="ignore")
    enabled: bool = False
    mode: str = Field(default="shadow", pattern="^(shadow|diagnose|apply)$")
    root: Path = Path("data/source-maintenance")
    sources: list[str] = Field(default_factory=list)
    sites: list[str] = Field(default_factory=list)
    scan_seconds: float = Field(default=30, ge=1)
    max_rounds: int = Field(default=3, ge=1)
    round_seconds: int = Field(default=1200, ge=1)
    daily_rounds: int = Field(default=6, ge=1)
    daily_wall_seconds: int = Field(default=7200, ge=1)
    resource_daily_deploys: int = Field(default=3, ge=1)
    model: str = "gpt-6.1-sol"
    effort: str = "medium"
    agent_image: str = "doxagent-source-maintenance-agent:local"
    verify_image: str = "doxagent-source-maintenance-agent:local"
    source_repository: str | None = None
    manifest_path: Path | None = None
    policy_path: Path | None = None
    auth_file: Path | None = None
    worker_network: str = "doxagent-source-maintenance-isolated"
    worker_proxy: str | None = None
    worker_host_root: Path | None = None
    site_access_url: str = "http://v2-site-access:8011"
    site_token_file: Path | None = None
    evidence_token_file: Path | None = None
    evidence_port: int = Field(default=8024, ge=1, le=65535)
    docker_executable: str = "docker"
    compose_files: list[str] = Field(default_factory=list)
    compose_env_file: str | None = None
    compose_project_directory: Path | None = None

    @property
    def database(self) -> Path:
        return self.root / "maintenance.sqlite3"
