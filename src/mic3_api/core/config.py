from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL


class Settings(BaseSettings):
    """Application settings loaded from the environment."""

    app_env: str = "development"
    app_name: str = "mic3-api"
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
