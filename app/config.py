from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "DAH Agent PoC"
    app_env: str = "local"
    database_path: str = "./data/dah_poc.sqlite3"
    openai_api_key: str = ""
    openai_base_url: str = "https://litellm.uaysk.com"
    openai_model: str = "gpt-5.5"
    openai_reasoning_effort: str = "low"
    openai_timeout_seconds: float = 30.0
    public_base_url: str = "http://127.0.0.1:18080"

    temporal_enabled: bool = False
    temporal_address: str = "temporal:7233"
    temporal_namespace: str = "default"
    temporal_task_queue: str = "dah-agent-task-queue"
    temporal_workflow_timeout_seconds: int = 300
    temporal_db_host: str = "172.30.1.49"
    temporal_db_port: int = 5432
    temporal_db_user: str = "temporal"
    temporal_db_password: str = ""
    temporal_db_name: str = "temporal"
    temporal_visibility_db_name: str = "temporal_visibility"

    redis_streams_enabled: bool = False
    redis_url: str = "redis://172.30.1.51:6379/0"
    redis_stream_prefix: str = "dah"
    redis_stream_maxlen: int = 10000

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def openai_configured(self) -> bool:
        return bool(self.openai_api_key.strip())

    @property
    def openai_responses_url(self) -> str:
        base_url = self.openai_base_url.rstrip("/")
        if base_url.endswith("/responses"):
            return base_url
        if base_url.endswith("/v1"):
            return f"{base_url}/responses"
        return f"{base_url}/v1/responses"


@lru_cache
def get_settings() -> Settings:
    return Settings()
