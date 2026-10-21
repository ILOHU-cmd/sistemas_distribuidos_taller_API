# app/config.py
"""Configuración centralizada y validada mediante pydantic-settings."""

from functools import lru_cache

from pydantic import Field, HttpUrl, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Variables de entorno de la aplicación."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = Field(default="Agregador Resiliente de APIs", validation_alias="APP_NAME")
    environment: str = Field(default="development", validation_alias="ENVIRONMENT")
    unstable_service_url: HttpUrl = Field(
        default="http://localhost:9000", validation_alias="UNSTABLE_SERVICE_URL"
    )
    usgs_feed_url: HttpUrl = Field(
        default="https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_hour.geojson",
        validation_alias="USGS_FEED_URL",
    )
    http_timeout_seconds: float = Field(default=5.0, gt=0, validation_alias="HTTP_TIMEOUT_SECONDS")
    unstable_total_timeout_seconds: float = Field(
        default=20.0, gt=0, validation_alias="UNSTABLE_TOTAL_TIMEOUT_SECONDS"
    )
    max_retries: int = Field(default=3, ge=0, le=10, validation_alias="MAX_RETRIES")
    backoff_base: float = Field(default=2.0, ge=1.0, validation_alias="BACKOFF_BASE")
    cache_ttl_seconds: int = Field(default=60, gt=0, validation_alias="CACHE_TTL_SECONDS")

    @field_validator("unstable_service_url", "usgs_feed_url", mode="before")
    @classmethod
    def strip_trailing_slash(cls, value: object) -> object:
        return str(value).rstrip("/")


@lru_cache
def get_settings() -> Settings:
    """Devuelve una única instancia inmutable de configuración por proceso."""

    return Settings()

