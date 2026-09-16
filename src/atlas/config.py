from decimal import Decimal
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str = ""
    database_admin_url: str = ""
    app_db_password: str = ""
    worker_db_password: str = ""
    worker_database_url: str = ""
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

    @model_validator(mode="after")
    def no_paid_services(self):
        if self.paid_providers_enabled or self.global_paid_spend_limit_usd != 0:
            raise ValueError("This installation only permits local, unbilled inference")
        return self


settings = Settings()
