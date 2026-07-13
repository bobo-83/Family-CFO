"""M87a: /voice/tts proxies the on-box Kokoro TTS service (ADR 0018)."""

import httpx
import pytest


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.anyio
async def test_tts_requires_authentication(demo_client) -> None:
    response = await demo_client.post("/api/v1/voice/tts", json={"text": "hello"})
    assert response.status_code == 401


@pytest.mark.anyio
async def test_tts_returns_503_when_no_service_configured(demo_client, demo_token) -> None:
    # The test settings leave tts_url empty by default.
    response = await demo_client.post(
        "/api/v1/voice/tts", headers=_headers(demo_token), json={"text": "hello"}
    )
    assert response.status_code == 503


def _use_tts_settings(app, **overrides) -> None:
    from family_cfo_api.config import Settings
    from family_cfo_api.deps import get_app_settings

    app.dependency_overrides[get_app_settings] = lambda: Settings(**overrides)


@pytest.mark.anyio
async def test_tts_streams_audio_from_the_upstream(
    demo_app, demo_client, demo_token, monkeypatch
) -> None:
    from family_cfo_api.api import voice as voice_module

    captured: dict[str, object] = {}

    class _FakeResponse:
        def raise_for_status(self) -> None:
            pass

        async def aiter_bytes(self):
            yield b"ID3-audio-"
            yield b"bytes"

        async def aclose(self) -> None:
            captured["closed"] = True

    class _FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def build_request(self, method, url, *, json):
            captured["url"] = url
            captured["body"] = json
            return object()

        async def send(self, request, *, stream):
            captured["streamed"] = stream
            return _FakeResponse()

        async def aclose(self) -> None:
            pass

    monkeypatch.setattr(voice_module.httpx, "AsyncClient", _FakeClient)
    _use_tts_settings(demo_app, tts_url="http://tts:8880", tts_voice="af_heart")

    response = await demo_client.post(
        "/api/v1/voice/tts",
        headers=_headers(demo_token),
        json={"text": "Your net worth is up this month."},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("audio/mpeg")
    assert response.content == b"ID3-audio-bytes"
    assert captured["url"] == "http://tts:8880/v1/audio/speech"
    assert captured["body"]["input"] == "Your net worth is up this month."
    assert captured["body"]["voice"] == "af_heart"
    assert captured["body"]["response_format"] == "mp3"


@pytest.mark.anyio
async def test_tts_returns_503_when_upstream_is_down(
    demo_app, demo_client, demo_token, monkeypatch
) -> None:
    from family_cfo_api.api import voice as voice_module

    class _FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def build_request(self, method, url, *, json):
            return object()

        async def send(self, request, *, stream):
            raise httpx.ConnectError("connection refused")

        async def aclose(self) -> None:
            pass

    monkeypatch.setattr(voice_module.httpx, "AsyncClient", _FakeClient)
    _use_tts_settings(demo_app, tts_url="http://tts:8880")

    response = await demo_client.post(
        "/api/v1/voice/tts", headers=_headers(demo_token), json={"text": "hi"}
    )
    assert response.status_code == 503
