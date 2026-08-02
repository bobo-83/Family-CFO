"""Median felt-latency per model (evidence for model choice)."""

import pytest

from family_cfo_api import repository


def _rec(engine, hh, model, ms):
    repository.create_recommendation(
        engine, household_id=hh, scenario_id=None, answer="a", assumptions=[],
        impacts=[], tradeoffs=[], alternatives=[], confidence=0.8,
        calculation_refs=[], warnings=[], explanation_source="agentic_tool_calling",
        model_version=model, answer_ms=ms,
    )


@pytest.mark.anyio
async def test_status_reports_median_answer_time_per_model(
    demo_client, demo_token, demo_engine
) -> None:
    headers = {"Authorization": f"Bearer {demo_token}"}
    hh = (await demo_client.get("/api/v1/auth/session", headers=headers)).json()["household_id"]
    for ms in (30_000, 45_000, 60_000):
        _rec(demo_engine, hh, "unsloth/Qwen3.6-35B-A3B-NVFP4", ms)
    for ms in (12_000, 18_000):
        _rec(demo_engine, hh, "nvidia/MiniMax-M2.7-NVFP4", ms)
    # An untimed deterministic answer must not pollute the stats.
    repository.create_recommendation(
        demo_engine, household_id=hh, scenario_id=None, answer="d", assumptions=[],
        impacts=[], tradeoffs=[], alternatives=[], confidence=0.8,
        calculation_refs=[], warnings=[], explanation_source="deterministic_stub",
    )

    status = (await demo_client.get("/api/v1/ai/runtime/status", headers=headers)).json()
    stats = {s["model"]: s for s in status["answer_stats"]}
    assert stats["unsloth/Qwen3.6-35B-A3B-NVFP4"]["median_ms"] == 45_000
    assert stats["unsloth/Qwen3.6-35B-A3B-NVFP4"]["samples"] == 3
    assert stats["nvidia/MiniMax-M2.7-NVFP4"]["median_ms"] == 15_000
    assert stats["nvidia/MiniMax-M2.7-NVFP4"]["samples"] == 2
