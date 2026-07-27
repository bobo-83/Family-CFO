import httpx
import pytest

from family_cfo_ai_orchestrator.runtime import RuntimeMessage, RuntimeUnavailableError
from family_cfo_ai_orchestrator.vllm_adapter import VLLMAdapter

MESSAGES = [RuntimeMessage(role="user", content="hello")]


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_complete_returns_text_from_successful_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        body = request.read()
        assert b'"role":"user"' in body or b'"role": "user"' in body
        return httpx.Response(
            200,
            json={
                "model": "test-model",
                "choices": [{"message": {"content": "This is grounded advice."}}],
            },
        )

    adapter = VLLMAdapter("http://vllm.local:8000", "test-model", client=_client(handler))

    completion = adapter.complete(MESSAGES)

    assert completion.text == "This is grounded advice."
    assert completion.model == "test-model"


def test_complete_retries_then_succeeds() -> None:
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        if attempts["count"] < 2:
            return httpx.Response(500)
        return httpx.Response(
            200,
            json={"model": "test-model", "choices": [{"message": {"content": "ok"}}]},
        )

    adapter = VLLMAdapter(
        "http://vllm.local:8000", "test-model", max_retries=2, client=_client(handler)
    )

    completion = adapter.complete(MESSAGES)

    assert completion.text == "ok"
    assert attempts["count"] == 2


def test_complete_raises_runtime_unavailable_after_exhausting_retries() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    adapter = VLLMAdapter(
        "http://vllm.local:8000", "test-model", max_retries=1, client=_client(handler)
    )

    with pytest.raises(RuntimeUnavailableError):
        adapter.complete(MESSAGES)


def test_complete_raises_runtime_unavailable_on_timeout() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out")

    adapter = VLLMAdapter(
        "http://vllm.local:8000", "test-model", max_retries=0, client=_client(handler)
    )

    with pytest.raises(RuntimeUnavailableError):
        adapter.complete(MESSAGES)


def test_complete_raises_runtime_unavailable_on_malformed_response() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": "shape"})

    adapter = VLLMAdapter(
        "http://vllm.local:8000", "test-model", max_retries=0, client=_client(handler)
    )

    with pytest.raises(RuntimeUnavailableError):
        adapter.complete(MESSAGES)


def test_complete_omits_chat_template_kwargs_by_default() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert b"chat_template_kwargs" not in request.read()
        return httpx.Response(
            200,
            json={"model": "test-model", "choices": [{"message": {"content": "ok"}}]},
        )

    adapter = VLLMAdapter("http://vllm.local:8000", "test-model", client=_client(handler))

    assert adapter.complete(MESSAGES).text == "ok"


def test_complete_thinking_false_disables_thinking_in_template() -> None:
    # Hybrid reasoning models must not spend a structured-extraction call's
    # max_tokens budget on a thinking phase (truncated JSON otherwise).
    def handler(request: httpx.Request) -> httpx.Response:
        import json

        body = json.loads(request.read())
        assert body["chat_template_kwargs"] == {"enable_thinking": False}
        return httpx.Response(
            200,
            json={"model": "test-model", "choices": [{"message": {"content": "{}"}}]},
        )

    adapter = VLLMAdapter("http://vllm.local:8000", "test-model", client=_client(handler))

    assert adapter.complete(MESSAGES, thinking=False).text == "{}"


def test_complete_coerces_null_content_to_empty_string() -> None:
    # vLLM's reasoning parser returns content: null when the model spent its
    # whole token budget thinking; callers must get "" (str), never None.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "test-model",
                "choices": [
                    {"message": {"content": None, "reasoning_content": "hmm..."}}
                ],
            },
        )

    adapter = VLLMAdapter("http://vllm.local:8000", "test-model", client=_client(handler))

    completion = adapter.complete(MESSAGES)

    assert completion.text == ""
