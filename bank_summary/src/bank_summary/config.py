from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Add-on options, surfaced as env vars by run.sh (or read directly from .env on WSL)."""

    model_config = SettingsConfigDict(env_prefix="BANK_SUMMARY_", env_file=".env", extra="ignore")

    # Optional at the Settings level: report/accounts/etc. don't touch Enable Banking at all.
    # Commands that do (connect/exchange/sync) validate these are set before building a client.
    application_id: str | None = None
    private_key_path: Path | None = None
    redirect_url: str = "http://localhost:8000/callback"
    account_uid: str | None = None  # restrict sync/report/sensors to a single linked account
    aspsp_name: str | None = None
    aspsp_country: str = "DK"
    environment: str = "sandbox"  # "sandbox" or "production"
    currency: str = "DKK"

    data_dir: Path = Path("data")
    db_path: Path | None = None
    reports_dir: Path | None = None
    rules_path: Path | None = None

    sync_interval_hours: int = 24
    consent_expiring_soon_days: int = 14

    supervisor_token: str | None = Field(default=None, alias="SUPERVISOR_TOKEN")
    ha_api_base: str = "http://supervisor/core/api"

    # Enable Banking has a single API host; sandbox vs. production is a property of which
    # ASPSP you connect to (`sandbox: true` in the /aspsps listing), not a different base URL.
    base_url: str = "https://api.enablebanking.com"

    def resolved_db_path(self) -> Path:
        return self.db_path or (self.data_dir / "bank.db")

    def resolved_reports_dir(self) -> Path:
        return self.reports_dir or (self.data_dir / "reports")

    def resolved_rules_path(self) -> Path:
        return self.rules_path or (self.data_dir / "rules.yaml")
