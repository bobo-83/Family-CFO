from sqlalchemy.engine import Engine

from family_cfo_api import fixtures, repository
from family_cfo_api.ai_runtime_selection import (
    resolve_ai_config,
    select_explanation_adapter,
    select_tool_runtime,
)
from family_cfo_api.config import Settings
from family_cfo_api.explanation import DeterministicExplanationAdapter

_HH = fixtures.DEMO_HOUSEHOLD_ID
_ENABLED = Settings(ai_default_enabled=True, ai_default_model="Qwen/Qwen2.5-32B-Instruct")


def test_default_disabled_settings_yield_no_runtime(demo_engine: Engine) -> None:
    # Bare/non-Docker defaults keep AI off, so nothing reaches for a runtime.
    assert select_tool_runtime(demo_engine, _HH, Settings()) is None
    adapter, client = select_explanation_adapter(demo_engine, _HH, Settings())
    assert isinstance(adapter, DeterministicExplanationAdapter)
    assert client is None


def test_enabled_settings_default_used_when_household_has_no_row(demo_engine: Engine) -> None:
    config = resolve_ai_config(demo_engine, _HH, _ENABLED)
    assert config.enabled and config.is_usable
    assert config.model == "Qwen/Qwen2.5-32B-Instruct"

    runtime = select_tool_runtime(demo_engine, _HH, _ENABLED)
    assert runtime is not None
    runtime.close()


def test_household_row_overrides_enabled_settings_default(demo_engine: Engine) -> None:
    # A household that saved its own disabled config wins over the deployment default.
    repository.upsert_ai_runtime_config(
        demo_engine,
        household_id=_HH,
        provider="vllm",
        base_url="http://vllm:8000",
        model="some-model",
        enabled=False,
    )
    assert select_tool_runtime(demo_engine, _HH, _ENABLED) is None


def test_enabled_settings_but_empty_model_is_not_usable(demo_engine: Engine) -> None:
    # Enabled with no model name is a misconfiguration; fall back, don't crash.
    settings = Settings(ai_default_enabled=True, ai_default_model="")
    assert select_tool_runtime(demo_engine, _HH, settings) is None
