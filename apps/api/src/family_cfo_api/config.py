from dataclasses import dataclass
from functools import lru_cache
import os

from family_cfo_api import __version__

DEFAULT_DATABASE_URL = "postgresql+psycopg://family_cfo:family_cfo@localhost:5432/family_cfo"
DEFAULT_IMPORT_STAGING_DIR = "./data/import-staging"
DEFAULT_BACKUP_DIR = "./data/backups"
DEFAULT_BACKUP_RETENTION_COUNT = 7


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default

    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    app_name: str = "Family CFO API"
    version: str = __version__
    environment: str = "development"
    log_level: str = "INFO"
    database_url: str = DEFAULT_DATABASE_URL
    health_check_database: bool = False
    import_staging_dir: str = DEFAULT_IMPORT_STAGING_DIR
    backup_dir: str = DEFAULT_BACKUP_DIR
    backup_retention_count: int = DEFAULT_BACKUP_RETENTION_COUNT
    backup_encryption_key: str | None = None
    session_ttl_hours: int = 12
    # Default AI runtime for households that have not configured their own. The
    # deployed Docker stack sets these so the agentic advisor is on out of the
    # box (the stack ships a vLLM service); the code default stays off so tests
    # and non-Docker runs never reach for a runtime that isn't there. A
    # household's own ai_runtime_configs row always overrides these.
    ai_default_enabled: bool = False
    ai_default_provider: str = "vllm"
    ai_default_base_url: str = "http://vllm:8000"
    ai_default_model: str = ""

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            app_name=os.getenv("FAMILY_CFO_API_NAME", cls.app_name),
            version=os.getenv("FAMILY_CFO_API_VERSION", cls.version),
            environment=os.getenv("FAMILY_CFO_ENV", cls.environment),
            log_level=os.getenv("FAMILY_CFO_LOG_LEVEL", cls.log_level),
            database_url=os.getenv("FAMILY_CFO_DATABASE_URL", cls.database_url),
            health_check_database=_env_bool(
                "FAMILY_CFO_HEALTH_CHECK_DATABASE",
                cls.health_check_database,
            ),
            import_staging_dir=os.getenv("FAMILY_CFO_IMPORT_STAGING_DIR", cls.import_staging_dir),
            backup_dir=os.getenv("FAMILY_CFO_BACKUP_DIR", cls.backup_dir),
            backup_retention_count=int(
                os.getenv("FAMILY_CFO_BACKUP_RETENTION_COUNT", str(cls.backup_retention_count))
            ),
            backup_encryption_key=os.getenv(
                "FAMILY_CFO_BACKUP_ENCRYPTION_KEY", cls.backup_encryption_key
            ),
            session_ttl_hours=int(
                os.getenv("FAMILY_CFO_SESSION_TTL_HOURS", str(cls.session_ttl_hours))
            ),
            ai_default_enabled=_env_bool("FAMILY_CFO_AI_ENABLED", cls.ai_default_enabled),
            ai_default_provider=os.getenv("FAMILY_CFO_AI_PROVIDER", cls.ai_default_provider),
            ai_default_base_url=os.getenv("FAMILY_CFO_AI_BASE_URL", cls.ai_default_base_url),
            ai_default_model=os.getenv("FAMILY_CFO_AI_MODEL", cls.ai_default_model),
        )


@lru_cache
def get_settings() -> Settings:
    return Settings.from_env()
