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


def _env_csv(name: str) -> tuple[str, ...]:
    value = os.getenv(name)
    if not value:
        return ()
    return tuple(item.strip() for item in value.split(",") if item.strip())


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
    # Vision routing (ADR 0011). If the main model is vision-capable, it
    # describes attached photos itself; otherwise the dedicated describer below
    # is used; otherwise images get a graceful "not analyzed" warning.
    ai_supports_vision: bool = False
    ai_vision_enabled: bool = False
    ai_vision_base_url: str = "http://vllm-vision:8000"
    ai_vision_model: str = ""
    # SSRF guard: base_urls a household may point its AI runtime at. Empty means
    # "just the deployment default" (ai_default_base_url) — see ADR 0010.
    ai_allowed_base_urls: tuple[str, ...] = ()
    # Brute-force guard on POST /auth/sessions (in-memory, single-instance).
    auth_rate_limit_enabled: bool = True
    auth_rate_limit_max_attempts: int = 5
    auth_rate_limit_window_seconds: int = 300
    auth_rate_limit_lockout_seconds: int = 900
    # Max accepted upload size (bytes) for imports/documents.
    max_upload_bytes: int = 10_000_000
    # One-click model apply (ADR 0013): the model-manager sidecar URL; empty
    # disables the in-app Apply flow (fall back to scripts/swap-model.sh).
    model_manager_url: str = ""
    # Hugging Face Hub base URL for model search (overridable for tests).
    hf_hub_url: str = "https://huggingface.co"
    # Live-data chat tools (ADR 0014): exchange rates on by default (only ISO
    # codes leave the box); web_search requires a self-hosted SearXNG URL.
    live_data_enabled: bool = True
    searxng_url: str = ""
    # M69 (ADR 0017): vector retrieval; empty disables the feature entirely.
    qdrant_url: str = ""
    # Advisor voice (M31): "playful" (default) or "professional". Tone only —
    # grounding rules are identical in both.
    ai_tone: str = "playful"
    # M32: single-tenant by default — POST /households refuses once a household
    # exists. Opt out for deliberate multi-household deployments.
    allow_multiple_households: bool = False
    # M83a: path to the deployment's TLS certificate (PEM). When set, pairing
    # QR payloads carry its SHA-256 fingerprint so the iOS app can pin it.
    tls_cert_path: str = ""

    def allowed_ai_base_urls(self) -> tuple[str, ...]:
        """The effective AI base_url allowlist: configured set, else the default."""
        return self.ai_allowed_base_urls or (self.ai_default_base_url,)

    @property
    def docs_enabled(self) -> bool:
        """Serve Swagger/openapi.json everywhere except production."""
        return self.environment.strip().lower() != "production"

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
            ai_supports_vision=_env_bool("FAMILY_CFO_AI_SUPPORTS_VISION", cls.ai_supports_vision),
            ai_vision_enabled=_env_bool("FAMILY_CFO_AI_VISION_ENABLED", cls.ai_vision_enabled),
            ai_vision_base_url=os.getenv("FAMILY_CFO_AI_VISION_BASE_URL", cls.ai_vision_base_url),
            ai_vision_model=os.getenv("FAMILY_CFO_AI_VISION_MODEL", cls.ai_vision_model),
            ai_allowed_base_urls=_env_csv("FAMILY_CFO_AI_ALLOWED_BASE_URLS"),
            auth_rate_limit_enabled=_env_bool(
                "FAMILY_CFO_AUTH_RATE_LIMIT_ENABLED", cls.auth_rate_limit_enabled
            ),
            auth_rate_limit_max_attempts=int(
                os.getenv("FAMILY_CFO_AUTH_RATE_LIMIT_MAX_ATTEMPTS", str(cls.auth_rate_limit_max_attempts))
            ),
            auth_rate_limit_window_seconds=int(
                os.getenv(
                    "FAMILY_CFO_AUTH_RATE_LIMIT_WINDOW_SECONDS",
                    str(cls.auth_rate_limit_window_seconds),
                )
            ),
            auth_rate_limit_lockout_seconds=int(
                os.getenv(
                    "FAMILY_CFO_AUTH_RATE_LIMIT_LOCKOUT_SECONDS",
                    str(cls.auth_rate_limit_lockout_seconds),
                )
            ),
            max_upload_bytes=int(os.getenv("FAMILY_CFO_MAX_UPLOAD_BYTES", str(cls.max_upload_bytes))),
            model_manager_url=os.getenv("FAMILY_CFO_MODEL_MANAGER_URL", cls.model_manager_url),
            hf_hub_url=os.getenv("FAMILY_CFO_HF_HUB_URL", cls.hf_hub_url),
            live_data_enabled=_env_bool("FAMILY_CFO_LIVE_DATA_ENABLED", cls.live_data_enabled),
            searxng_url=os.getenv("FAMILY_CFO_SEARXNG_URL", cls.searxng_url),
            qdrant_url=os.getenv("FAMILY_CFO_QDRANT_URL", cls.qdrant_url),
            ai_tone=os.getenv("FAMILY_CFO_AI_TONE", cls.ai_tone),
            allow_multiple_households=_env_bool(
                "FAMILY_CFO_ALLOW_MULTIPLE_HOUSEHOLDS", cls.allow_multiple_households
            ),
            tls_cert_path=os.getenv("FAMILY_CFO_TLS_CERT_PATH", cls.tls_cert_path),
        )


@lru_cache
def get_settings() -> Settings:
    return Settings.from_env()
