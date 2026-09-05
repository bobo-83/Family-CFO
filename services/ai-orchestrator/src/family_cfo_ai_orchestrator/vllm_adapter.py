from __future__ import annotations

import json

import httpx

from family_cfo_ai_orchestrator.runtime import (
    ExecutionDeadline,
    ExecutionDeadlineExceeded,
    RuntimeCompletion,
    RuntimeMessage,
    RuntimeToolCompletion,
    RuntimeUnavailableError,
    ToolCall,
    ToolSpec,
)

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
        self._timeout_seconds = timeout_seconds
        self._client = client or httpx.Client(timeout=timeout_seconds)
        self._owns_client = client is None

    def complete(
        self,
        messages: list[RuntimeMessage],
        *,
        temperature: float = 0.2,
        max_tokens: int = 400,
        thinking: bool = True,
        deadline: ExecutionDeadline | None = None,
    ) -> RuntimeCompletion:
        payload = {
            "model": self._model,
            "messages": [self._message_payload(message) for message in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if not thinking:
            # Hybrid reasoning models (Qwen3 family) spend max_tokens on a
            # thinking phase before the answer; structured-extraction callers
            # need the whole budget for the answer. Templates without an
            # enable_thinking variable ignore the extra kwarg.
            payload["chat_template_kwargs"] = {"enable_thinking": False}

        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            request_timeout, deadline_limited = self._request_timeout(deadline)
            try:
                response = self._client.post(
                    f"{self._base_url}/v1/chat/completions",
                    json=payload,
                    timeout=request_timeout,
                )
                response.raise_for_status()
                data = response.json()
                # Reasoning-parsed models return content: null when the whole
                # budget went to thinking -- callers get "", never None.
                text = data["choices"][0]["message"]["content"] or ""
                model = data.get("model", self._model)
                return RuntimeCompletion(text=text, model=model, raw=data)
            except httpx.TimeoutException as exc:
                # A timeout at the outer deadline is terminal for the whole
                # turn; an ordinary per-call timeout retains #126's fallback.
                if deadline_limited:
                    raise ExecutionDeadlineExceeded("advisor turn deadline exceeded") from exc
                raise RuntimeUnavailableError(
                    f"vLLM runtime timed out after {self._timeout_seconds:.0f}s"
                ) from exc
            except (httpx.HTTPError, KeyError, IndexError) as exc:
                last_error = exc

        raise RuntimeUnavailableError(
            f"vLLM runtime unavailable after {self._max_retries + 1} attempts"
        ) from last_error

    def _message_payload(self, message: RuntimeMessage) -> dict:
        item: dict = {"role": message.role, "content": message.content}
        if message.image_data_url:
            # OpenAI multimodal content parts (vLLM-compatible) for vision models.
            item["content"] = [
                {"type": "text", "text": message.content},
                {"type": "image_url", "image_url": {"url": message.image_data_url}},
            ]
        if message.tool_calls:
            item["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
                }
                for call in message.tool_calls
            ]
        if message.tool_call_id is not None:
            item["tool_call_id"] = message.tool_call_id
        return item

    def complete_with_tools(
        self,
        messages: list[RuntimeMessage],
        tools: list[ToolSpec],
        *,
        temperature: float = 0.2,
        max_tokens: int = 400,
        deadline: ExecutionDeadline | None = None,
    ) -> RuntimeToolCompletion:
        payload = {
            "model": self._model,
            "messages": [self._message_payload(message) for message in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.parameters,
                    },
                }
                for tool in tools
            ],
        }

        last_error: Exception | None = None
        for _ in range(self._max_retries + 1):
            request_timeout, deadline_limited = self._request_timeout(deadline)
            try:
                response = self._client.post(
                    f"{self._base_url}/v1/chat/completions",
                    json=payload,
                    timeout=request_timeout,
                )
                response.raise_for_status()
                data = response.json()
                message = data["choices"][0]["message"]
                model = data.get("model", self._model)
                raw_calls = message.get("tool_calls") or []
                tool_calls = [
                    ToolCall(
                        id=call.get("id", ""),
                        name=call["function"]["name"],
                        arguments=_parse_arguments(call["function"].get("arguments", "{}")),
                    )
                    for call in raw_calls
                ]
                return RuntimeToolCompletion(
                    tool_calls=tool_calls,
                    text=message.get("content") or "",
                    model=model,
                    raw=data,
                )
            except httpx.TimeoutException as exc:
                if deadline_limited:
                    raise ExecutionDeadlineExceeded("advisor turn deadline exceeded") from exc
                raise RuntimeUnavailableError(
                    f"vLLM runtime timed out after {self._timeout_seconds:.0f}s"
                ) from exc
            except (httpx.HTTPError, KeyError, IndexError) as exc:
                last_error = exc

        raise RuntimeUnavailableError(
            f"vLLM runtime unavailable after {self._max_retries + 1} attempts"
        ) from last_error

    def _request_timeout(self, deadline: ExecutionDeadline | None) -> tuple[float, bool]:
        if deadline is None:
            return self._timeout_seconds, False
        remaining = deadline.remaining_seconds()
        if remaining <= 0:
            raise ExecutionDeadlineExceeded("advisor turn deadline exceeded")
        return min(self._timeout_seconds, remaining), remaining < self._timeout_seconds

    def close(self) -> None:
        if self._owns_client:
            self._client.close()


def _parse_arguments(raw: str | dict) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}
