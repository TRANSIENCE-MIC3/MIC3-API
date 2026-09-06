from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL


class DatabaseSettings(BaseSettings):
    """PostgreSQL settings shared by the API and migration runner."""

    db_host: str
    db_port: int = 5432
    db_name: str
    db_user: str
    db_password: SecretStr

    @property
    def database_url(self) -> URL:
        """Build a PostgreSQL URL without interpolating credentials."""
        return URL.create(
            drivername="postgresql+psycopg",
            username=self.db_user,
            password=self.db_password.get_secret_value(),
            host=self.db_host,
            port=self.db_port,
            database=self.db_name,
        )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


class Settings(DatabaseSettings):
    """Complete API settings loaded from the environment."""

    app_env: str = "development"
    app_name: str = "mic3-api"
    oidc_issuer_url: str
    oidc_audience: str
    oidc_allowed_algorithms: tuple[str, ...] = ("RS256",)
    oidc_clock_skew_seconds: int = Field(default=30, ge=0, le=300)
    oidc_http_timeout_seconds: float = Field(default=5.0, gt=0, le=30)
    oidc_jwks_cache_seconds: int = Field(default=300, gt=0, le=3600)

    @field_validator("oidc_issuer_url")
    @classmethod
    def validate_oidc_issuer_url(cls, value: str) -> str:
        """Require an absolute issuer while preserving its exact value."""
        normalized = value.strip()
        parsed = urlsplit(normalized)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("OIDC issuer must be an absolute HTTP(S) URL")
        return normalized

    @field_validator("oidc_audience")
    @classmethod
    def validate_oidc_audience(cls, value: str) -> str:
        """Reject an empty resource-server audience."""
        normalized = value.strip()
        if not normalized:
            raise ValueError("OIDC audience must not be empty")
        return normalized

    @field_validator("oidc_allowed_algorithms")
    @classmethod
    def validate_oidc_algorithms(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Allow only asymmetric signing algorithms for bearer tokens."""
        asymmetric_algorithms = {
            "RS256",
            "RS384",
            "RS512",
            "PS256",
            "PS384",
            "PS512",
            "ES256",
            "ES384",
            "ES512",
            "EdDSA",
        }
        if not value or any(
            algorithm not in asymmetric_algorithms for algorithm in value
        ):
            raise ValueError("OIDC algorithms must be supported asymmetric algorithms")
        return value
