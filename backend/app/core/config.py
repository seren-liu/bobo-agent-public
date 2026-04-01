from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Bobo Agent API"
    env: str = "dev"

    database_url: str = ""
    jwt_secret: str = "change_me"
    jwt_algorithm: str = "HS256"
    jwt_expire_hours: int = 24
    jwt_refresh_expire_hours: int = 24 * 30

    cors_prod_origin: str = "https://bobo.yourdomain.com"
    agent_tool_mode: str = ""
    mcp_server_url: str = "http://localhost:8000/mcp/mcp"
    mcp_transport: str = "http"
    mcp_service_token: str = ""
    memory_enabled: bool = True
    memory_summary_trigger_count: int = 20
    memory_recent_message_window: int = 8
    memory_semantic_top_k: int = 4
    memory_profile_refresh_cron: str = ""
    memory_item_default_ttl_days: int = 90
    memory_collection_name: str = "user_memory_vectors"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    if not settings.mcp_service_token:
        settings.mcp_service_token = f"{settings.jwt_secret}:mcp"
    return settings
