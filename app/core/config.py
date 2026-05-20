"""
app/core/config.py
Central application configuration loaded from environment variables.
"""
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Application
    app_name: str = "NFSe Bot"
    app_version: str = "1.0.0"
    debug: bool = False
    log_level: str = "INFO"

    # Server
    host: str = "0.0.0.0"
    port: int = 8000

    # Database (SQLite for MVP, swap URL for PostgreSQL migration)
    database_url: str = "sqlite+aiosqlite:///./data/nfse.db"

    # Evolution API (WhatsApp)
    evolution_url: str = ""
    evolution_api_key: str = ""
    evolution_instance: str = "nfse-bot"

    # Groq (OCR / Vision)
    groq_api_key: str = ""
    groq_model: str = "meta-llama/llama-4-scout-17b-16e-instruct"

    # Browser / Playwright
    browser_headless: bool = True
    browser_slow_mo: int = 300
    browser_timeout: int = 30000

    # Security
    webhook_secret: str = ""
    internal_api_key: str = ""

    # Development
    dry_run: bool = False

    # Retry policy
    retry_attempts: int = 3
    retry_delay: float = 2.0

    # File system
    output_dir: str = "./output"
    logs_dir: str = "./logs"
    data_dir: str = "./data"

    @property
    def output_path(self) -> Path:
        return Path(self.output_dir)

    @property
    def logs_path(self) -> Path:
        return Path(self.logs_dir)

    @property
    def data_path(self) -> Path:
        return Path(self.data_dir)

    @property
    def pdf_path(self) -> Path:
        return self.output_path / "pdf"

    @property
    def xml_path(self) -> Path:
        return self.output_path / "xml"

    @property
    def screenshots_path(self) -> Path:
        return self.output_path / "screenshots"

    def ensure_directories(self) -> None:
        for path in (
            self.output_path,
            self.logs_path,
            self.data_path,
            self.pdf_path,
            self.xml_path,
            self.screenshots_path,
        ):
            path.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
