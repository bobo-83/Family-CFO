"""M87a (ADR 0018): advisor voice — proxy the on-box Kokoro TTS service.

The chat pipeline stays text-only and grounded; this endpoint only turns an
already-produced answer into speech. Audio is synthesized on the box by an
optional OpenAI-compatible TTS service (Kokoro-82M) and streamed to the
client. When no service is configured the endpoint returns 503 and clients
fall back to the platform's own speech synthesizer.
"""

from __future__ import annotations

import logging
import struct

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response, StreamingResponse

from family_cfo_api import repository
from family_cfo_api.config import Settings
from family_cfo_api.deps import get_app_settings, get_current_session
from family_cfo_api.schemas import ErrorResponse, VoiceRequest

router = APIRouter(tags=["Voice"])
logger = logging.getLogger(__name__)

_TTS_TIMEOUT = httpx.Timeout(60.0, connect=5.0)


def _patch_wav_header(data: bytes) -> bytes:
    """Kokoro *streams* WAV, so it writes placeholder 0xFFFFFFFF sizes in the
    RIFF and `data` chunk headers. Browsers' Web Audio `decodeAudioData` rejects
    that (iOS AVAudioPlayer tolerates it). Rewrite both sizes to the real byte
    counts now that we've buffered the whole file."""
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return data
    buf = bytearray(data)
    struct.pack_into("<I", buf, 4, len(data) - 8)  # RIFF chunk size = total - 8
    # Walk the sub-chunks (fmt, LIST, …) to the `data` chunk and fix its size.
    offset = 12
    while offset + 8 <= len(data):
        chunk_id = data[offset : offset + 4]
        chunk_size = struct.unpack_from("<I", data, offset + 4)[0]
        if chunk_id == b"data":
            struct.pack_into("<I", buf, offset + 4, len(data) - (offset + 8))
            break
        if chunk_size == 0xFFFFFFFF:  # a placeholder before data — can't walk on
            break
        offset += 8 + chunk_size + (chunk_size & 1)  # chunks are word-aligned
    return bytes(buf)


@router.post(
    "/voice/tts",
    operation_id="synthesizeSpeech",
    responses={
        200: {"content": {"audio/mpeg": {}}, "description": "Synthesized speech (MP3)"},
        401: {"description": "Unauthorized", "model": ErrorResponse},
        503: {"description": "No voice service configured", "model": ErrorResponse},
    },
    summary="Synthesize an advisor answer to speech (Kokoro; falls back client-side)",
)
async def synthesize_speech(
    payload: VoiceRequest,
    session: repository.SessionContext = Depends(get_current_session),
    settings: Settings = Depends(get_app_settings),
) -> StreamingResponse:
    if not settings.tts_url:
        raise HTTPException(status_code=503, detail="No voice service is configured")

    upstream = settings.tts_url.rstrip("/") + "/v1/audio/speech"
    body = {
        "model": "kokoro",
        "input": payload.text,
        "voice": payload.voice or settings.tts_voice,
        "response_format": payload.format,
    }
    media_type = "audio/wav" if payload.format == "wav" else "audio/mpeg"

    # Open the upstream stream up front so a down service is a clean 503, not
    # a half-streamed error body reaching the client.
    client = httpx.AsyncClient(timeout=_TTS_TIMEOUT)
    try:
        request = client.build_request("POST", upstream, json=body)
        response = await client.send(request, stream=True)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        await client.aclose()
        logger.warning("TTS service unavailable at %s: %s", upstream, exc)
        raise HTTPException(status_code=503, detail="Voice service unavailable") from exc

    if payload.format == "wav":
        # Buffer the whole WAV and repair its streamed header so browser Web
        # Audio can decode it (the web path); iOS never requests wav.
        try:
            audio = _patch_wav_header(await response.aread())
        finally:
            await response.aclose()
            await client.aclose()
        return Response(content=audio, media_type=media_type)

    async def _relay():
        try:
            async for chunk in response.aiter_bytes():
                yield chunk
        finally:
            await response.aclose()
            await client.aclose()

    return StreamingResponse(_relay(), media_type=media_type)
