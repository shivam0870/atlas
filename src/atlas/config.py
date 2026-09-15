from decimal import Decimal
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str = ""
    database_admin_url: str = ""
    app_db_password: str = ""
    redis_url: str = "redis://127.0.0.1:56379/0"
    local_console: bool = False
    paid_providers_enabled: bool = False
    global_paid_spend_limit_usd: Decimal = Decimal(0)
    otel_enabled: bool = False
    otel_endpoint: str = "http://127.0.0.1:54318/v1/traces"
    data_dir: Path = Path(".local")

    @model_validator(mode="after")
    def no_paid_services(self):
        if self.paid_providers_enabled or self.global_paid_spend_limit_usd != 0:
            raise ValueError("This installation only permits local, unbilled inference")
        return self


settings = Settings()
