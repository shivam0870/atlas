from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str = ""
    database_admin_url: str = ""
    app_db_password: str = ""
    worker_db_password: str = ""
    worker_database_url: str = ""
    identity_database_url: str = ""
    identity_db_password: str = ""
    mfa_encryption_key: str = ""
    app_url: str = "http://127.0.0.1:8100"
    smtp_host: str = "127.0.0.1"
    smtp_port: int = 51025
    smtp_from: str = "Atlas <atlas@localhost>"
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_starttls: bool = False
    session_idle_seconds: int = 86400
    session_absolute_seconds: int = 2592000
    max_file_bytes: int = 20_000_000
    max_document_pages: int = 200
    redis_url: str = "redis://127.0.0.1:56379/0"
    cache_redis_url: str = "redis://127.0.0.1:56380/0"
    semantic_cache_threshold: float | None = None
    fallback_url: str = "http://127.0.0.1:11436"
    fallback_enabled: bool = False
    local_console: bool = False
    paid_providers_enabled: bool = False
    global_paid_spend_limit_usd: Decimal = Decimal(0)
    otel_enabled: bool = False
    service_name: str = "atlas-api"
    otel_endpoint: str = "http://127.0.0.1:54318/v1/traces"
    data_dir: Path = Path(".local")
    ollama_url: str = "http://127.0.0.1:11435"
    generation_model: str = "qwen3:4b-instruct-2507-q4_K_M"
    embedding_path: str = ".models/bge"
    reranker_path: str = ".models/reranker"
    chunk_size: int = 1000
    chunk_overlap: int = 120
    relevance_floor: float = 0.48
    rrf_constant: float = 60
    vector_weight: float = 1
    lexical_weight: float = 1
    max_upload_bytes: int = 2_000_000
    max_pending_jobs: int = 100
    model_timeout: float = 120
    generation_slots: int = 1
    generation_queue_size: int = 200
    generation_queue_timeout: float = 30
    ingestion_stream_size: int = 100
    upload_scanner_host: str = "127.0.0.1"
    upload_scanner_port: int = 53310
    upload_scanner_timeout: float = 15
    upload_scanner_max_age_days: int = 7
    upload_scan_required: bool = True

    @model_validator(mode="after")
    def no_paid_services(self):
        if self.paid_providers_enabled or self.global_paid_spend_limit_usd != 0:
            raise ValueError("This installation only permits local, unbilled inference")
        if not 1 <= self.generation_slots <= 32 or not 1 <= self.generation_queue_size <= 10000:
            raise ValueError("Generation capacity must be bounded and positive")
        if not 1 <= self.generation_queue_timeout <= 120:
            raise ValueError("Generation queue timeout must be between 1 and 120 seconds")
        origin = urlsplit(self.app_url)
        if origin.scheme not in {"http", "https"} or not origin.hostname:
            raise ValueError("APP_URL must be a complete HTTP(S) origin")
        if origin.scheme != "https" and origin.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("Non-local installations require HTTPS for secure account cookies")
        if not self.upload_scan_required and origin.hostname not in {
            "localhost",
            "127.0.0.1",
            "::1",
        }:
            raise ValueError("Malware scanning cannot be disabled for a non-local installation")
        if origin.path not in {"", "/"} or origin.query or origin.fragment:
            raise ValueError("APP_URL must be an origin without a path, query or fragment")
        return self


settings = Settings()
