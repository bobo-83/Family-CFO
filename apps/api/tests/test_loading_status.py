"""M50: classify the vLLM log tail into a real loading status."""

import httpx
import pytest

from family_cfo_api.api import ai_runtime as ai_runtime_module
from family_cfo_api.api.ai_runtime import classify_vllm_logs

# The actual crash from 2026-07-10 that showed as "loading" for 10+ minutes.
_KV_CACHE_CRASH = """
vllm-1  | (EngineCore pid=98)     raise ValueError(
vllm-1  | (EngineCore pid=98) ValueError: To serve at least one request with the model's max seq len (128000), (39.06 GiB KV cache is needed, which is larger than the available KV cache memory (12.64 GiB).
"""


def test_crash_lines_classify_as_error_with_the_message() -> None:
    result = classify_vllm_logs(_KV_CACHE_CRASH)
    assert result is not None
    phase, detail = result
    assert phase == "error"
    assert "KV cache" in detail
    assert "vllm-1" not in detail  # compose prefix stripped


def test_download_progress_is_reported() -> None:
    text = "vllm-1  | model-00003-of-00011.safetensors:  45%|####      | 2.1G/4.6G"
    phase, detail = classify_vllm_logs(text)
    assert phase == "downloading"
    assert "model-00003-of-00011.safetensors" in detail
    assert "45%" in detail


def test_shard_loading_progress_is_reported() -> None:
    text = (
        "vllm-1  | Loading safetensors checkpoint shards:  20% Completed\n"
        "vllm-1  | Loading safetensors checkpoint shards:  60% Completed"
    )
    phase, detail = classify_vllm_logs(text)
    assert phase == "loading"
    assert "60%" in detail  # the LAST reported percentage wins


def test_warming_up_and_fallback_phases() -> None:
    assert classify_vllm_logs("Capturing CUDA graph shapes")[0] == "warming_up"
    assert classify_vllm_logs("some unrelated startup chatter")[0] == "starting"
    assert classify_vllm_logs("") is None


@pytest.mark.anyio
async def test_status_includes_loading_detail_when_not_ready(
    demo_client, demo_token, monkeypatch
) -> None:
    def fake_get(url, params=None, timeout=None):
        if "/logs" in url:
            assert params == {"service": "vllm", "tail": 40}
            return httpx.Response(
                200, json={"lines": _KV_CACHE_CRASH}, request=httpx.Request("GET", url)
            )
        # The vLLM probe fails -> not ready.
        raise httpx.ConnectError("down")

    monkeypatch.setattr(ai_runtime_module.httpx, "get", fake_get)
    monkeypatch.setenv("FAMILY_CFO_MODEL_MANAGER_URL", "http://model-manager:8000")
    resp = await demo_client.get(
        "/api/v1/ai/runtime/status", headers={"Authorization": f"Bearer {demo_token}"}
    )
    body = resp.json()
    if body["enabled"] and body["provider"] == "vllm":
        assert body["ready"] is False
        assert body["loading_phase"] == "error"
        assert "KV cache" in body["loading_detail"]
