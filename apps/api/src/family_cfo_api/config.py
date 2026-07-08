from dataclasses import dataclass
from functools import lru_cache
import os

from family_cfo_api import __version__

DEFAULT_DATABASE_URL = "postgresql+psycopg://family_cfo:family_cfo@localhost:5432/family_cfo"
DEFAULT_IMPORT_STAGING_DIR = "./data/import-staging"


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
        )


@lru_cache
def get_settings() -> Settings:
    return Settings.from_env()

