from __future__ import annotations

from family_cfo_ai_orchestrator import VLLMAdapter
from sqlalchemy.engine import Engine

from family_cfo_api import repository
from family_cfo_api.explanation import DeterministicExplanationAdapter, ExplanationAdapter
from family_cfo_api.llm_explanation import LlmExplanationAdapter

# Providers with a shipped RuntimeAdapter. Other configured providers fall
# back to the deterministic stub until their adapters exist (M4 non-goals).
SUPPORTED_LLM_PROVIDERS = {"vllm"}


def select_explanation_adapter(
    engine: Engine, household_id: str
) -> tuple[ExplanationAdapter, VLLMAdapter | None]:
    """Pick the deterministic stub or a real runtime-backed adapter, per household AI config.

    Shared by the purchase advisor (M3/M4) and report generation (M8) so both
    features select an explanation adapter the same way. Returns the runtime
    client too, if one was created, so the caller can close it when done.
    """
    config = repository.get_ai_runtime_config(engine, household_id)
    if config is None or not config.enabled or config.provider not in SUPPORTED_LLM_PROVIDERS:
        return DeterministicExplanationAdapter(), None

    runtime_client = VLLMAdapter(config.base_url, config.model)
    return LlmExplanationAdapter(runtime_client, model_version=config.model), runtime_client
