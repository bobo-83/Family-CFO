from __future__ import annotations

import httpx

from family_cfo_ai_orchestrator.runtime import RuntimeCompletion, RuntimeMessage, RuntimeUnavailableError

DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_MAX_RETRIES = 2


class VLLMAdapter:
    """OpenAI-compatible chat completions adapter targeting a self-hosted vLLM server.

    Retries are immediate (no backoff) since the runtime is expected to be
    co-located on the same private network, not a remote cloud endpoint.
    """

    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_retries: int = DEFAULT_MAX_RETRIES,
        client: httpx.Client | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._max_retries = max_retries
        self._client = client or httpx.Client(timeout=timeout_seconds)
        self._owns_client = client is None

    def complete(
        self,
        messages: list[RuntimeMessage],
        *,
        temperature: float = 0.2,
        max_tokens: int = 400,
    ) -> RuntimeCompletion:
        payload = {
            "model": self._model,
            "messages": [{"role": message.role, "content": message.content} for message in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                response = self._client.post(f"{self._base_url}/v1/chat/completions", json=payload)
                response.raise_for_status()
                data = response.json()
                text = data["choices"][0]["message"]["content"]
                model = data.get("model", self._model)
                return RuntimeCompletion(text=text, model=model, raw=data)
            except (httpx.HTTPError, KeyError, IndexError) as exc:
                last_error = exc

        raise RuntimeUnavailableError(
            f"vLLM runtime unavailable after {self._max_retries + 1} attempts"
        ) from last_error

    def close(self) -> None:
        if self._owns_client:
            self._client.close()
