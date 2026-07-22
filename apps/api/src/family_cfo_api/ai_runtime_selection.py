from __future__ import annotations

from dataclasses import dataclass

from family_cfo_ai_orchestrator import VLLMAdapter
from sqlalchemy.engine import Engine

from family_cfo_api import repository
from family_cfo_api.config import Settings, get_settings
from family_cfo_api.explanation import DeterministicExplanationAdapter, ExplanationAdapter
from family_cfo_api.llm_explanation import LlmExplanationAdapter

# Providers with a shipped RuntimeAdapter. Other configured providers fall
# back to the deterministic stub until their adapters exist (M4 non-goals).
SUPPORTED_LLM_PROVIDERS = {"vllm"}


@dataclass(frozen=True, slots=True)
class EffectiveAiConfig:
    """The AI runtime a household effectively uses: its own row, or the deployment default."""

    provider: str
    base_url: str
    model: str
    enabled: bool

    @property
    def is_usable(self) -> bool:
        # A runtime we can actually build an adapter for: enabled, a provider we
        # ship, and a concrete model name to send in the request.
        return self.enabled and self.provider in SUPPORTED_LLM_PROVIDERS and bool(self.model)


def resolve_ai_config(
    engine: Engine, household_id: str, settings: Settings | None = None
) -> EffectiveAiConfig:
    """The household's own runtime config if set, else the deployment default (settings/env).

    The Docker stack enables AI by default via ``FAMILY_CFO_AI_*``; a household
    that has saved its own ``ai_runtime_configs`` row (e.g. to disable AI or
    point elsewhere) always overrides that default.
    """
    settings = settings or get_settings()
    record = repository.get_ai_runtime_config(engine, household_id)
    if record is not None:
        return EffectiveAiConfig(
            provider=record.provider,
            base_url=record.base_url,
            model=record.model,
            enabled=record.enabled,
        )
    return EffectiveAiConfig(
        provider=settings.ai_default_provider,
        base_url=settings.ai_default_base_url,
        model=settings.ai_default_model,
        enabled=settings.ai_default_enabled,
    )


def select_explanation_adapter(
    engine: Engine, household_id: str, settings: Settings | None = None
) -> tuple[ExplanationAdapter, VLLMAdapter | None]:
    """Pick the deterministic stub or a real runtime-backed adapter, per household AI config.

    Shared by the purchase advisor (M3/M4) and report generation (M8) so both
    features select an explanation adapter the same way. Returns the runtime
    client too, if one was created, so the caller can close it when done.
    """
    config = resolve_ai_config(engine, household_id, settings)
    if not config.is_usable:
        return DeterministicExplanationAdapter(), None

    runtime_client = VLLMAdapter(config.base_url, config.model)
    return LlmExplanationAdapter(runtime_client, model_version=config.model), runtime_client


def select_vision_describer(
    engine: Engine, household_id: str, settings: Settings | None = None
) -> tuple[VLLMAdapter | None, str]:
    """The runtime that should describe an attached photo (ADR 0011).

    Returns (adapter, source) where source is "main" (vision-capable main
    model), "describer" (dedicated vision model), or "none". The caller owns
    closing the adapter.
    """
    settings = settings or get_settings()
    config = resolve_ai_config(engine, household_id, settings)
    if config.is_usable and settings.ai_supports_vision:
        return VLLMAdapter(config.base_url, config.model), "main"
    if settings.ai_vision_enabled and settings.ai_vision_model:
        return VLLMAdapter(settings.ai_vision_base_url, settings.ai_vision_model), "describer"
    return None, "none"


def select_tool_runtime(
    engine: Engine, household_id: str, settings: Settings | None = None
) -> VLLMAdapter | None:
    """Return a tool-calling runtime for the household, or None to fall back deterministically.

    Used by the agentic chat advisor (M16). A runtime exists only when the
    household's effective config is usable (enabled + supported provider +
    a model); otherwise the caller answers from the deterministic snapshot. The
    caller owns closing the returned client.
    """
    config = resolve_ai_config(engine, household_id, settings)
    if not config.is_usable:
        return None
    # A reasoning model thinking + answering within _ANSWER_MAX_TOKENS can run
    # ~50s on the box GPU; the 30s adapter default would abort mid-generation.
    return VLLMAdapter(config.base_url, config.model, timeout_seconds=90.0)
