from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


def to_psycopg_conninfo(database_url: str) -> str:
    if database_url.startswith("postgresql+psycopg://"):
        return database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    return database_url


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
    agent_system_prompt_version: str = "bobo-agent-system.v1"
    agent_memory_context_version: str = "bobo-agent-memory-context.v1"
    mcp_server_url: str = "http://localhost:8000/mcp/mcp"
    mcp_transport: str = "http"
    mcp_service_token: str = ""
    metrics_access_token: str = ""
    redis_url: str = "redis://localhost:6379/0"
    menu_search_result_cache_ttl_seconds: float = 90.0
    menu_search_query_cache_ttl_seconds: float = 600.0
    menu_search_hot_query_threshold: int = 3
    memory_enabled: bool = True
    memory_summary_trigger_count: int = 20
    memory_recent_message_window: int = 8
    memory_semantic_top_k: int = 4
    memory_profile_refresh_cron: str = ""
    memory_item_default_ttl_days: int = 90
    memory_collection_name: str = "user_memory_vectors"
    memory_worker_mode: str = ""
    memory_worker_poll_interval_seconds: float = 2.0
    memory_worker_batch_size: int = 10
    memory_worker_max_backoff_seconds: float = 15.0
    memory_worker_max_attempts: int = 3
    memory_worker_retry_base_seconds: float = 10.0
    memory_worker_retry_max_seconds: float = 300.0
    agent_tool_timeout_seconds: float = 6.0
    agent_tool_write_timeout_seconds: float = 10.0
    llm_request_timeout_seconds: float = 20.0
    embedding_request_timeout_seconds: float = 15.0
    qdrant_request_timeout_seconds: float = 8.0
    dependency_circuit_failure_threshold: int = 3
    dependency_circuit_recovery_seconds: float = 30.0


def is_production_env(env: str) -> bool:
    return env.lower() in {"prod", "production"}


def resolve_memory_worker_mode(settings: Settings) -> str:
    explicit = (settings.memory_worker_mode or "").strip().lower()
    if explicit in {"embedded", "external", "disabled"}:
        return explicit
    return "embedded" if not is_production_env(settings.env) else "external"


def validate_security_settings(settings: Settings) -> None:
    if not is_production_env(settings.env):
        return

    if not settings.jwt_secret or settings.jwt_secret == "change_me":
        raise RuntimeError("JWT_SECRET must be explicitly configured with a strong secret in production")
    if not settings.mcp_service_token or settings.mcp_service_token == f"{settings.jwt_secret}:mcp":
        raise RuntimeError("MCP_SERVICE_TOKEN must be explicitly configured and must not be derived from JWT_SECRET in production")
    if not settings.metrics_access_token:
        raise RuntimeError("METRICS_ACCESS_TOKEN must be explicitly configured in production")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    if not settings.mcp_service_token and not is_production_env(settings.env):
        settings.mcp_service_token = f"{settings.jwt_secret}:mcp"
    validate_security_settings(settings)
    return settings
