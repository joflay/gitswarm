from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite:///./gitswarm.db"
    github_token: str = ""
    admin_username: str = "admin"
    admin_password: str = "change-me"
    secret_key: str = "dev-secret-change-me"
    report_output_dir: str = "./reports"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def report_dir(self) -> Path:
        return Path(self.report_output_dir)


@lru_cache
def get_settings() -> Settings:
    return Settings()
