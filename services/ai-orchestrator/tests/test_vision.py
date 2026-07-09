import httpx
import pytest

from family_cfo_ai_orchestrator import (
    RuntimeMessage,
    RuntimeUnavailableError,
    VLLMAdapter,
    describe_image,
)

_DATA_URL = "data:image/jpeg;base64,aGVsbG8="


def _adapter_with(handler) -> VLLMAdapter:
    transport = httpx.MockTransport(handler)
    return VLLMAdapter(
        "http://vllm-vision:8000",
        "Qwen/Qwen2.5-VL-7B-Instruct",
        client=httpx.Client(transport=transport),
        max_retries=0,
    )


def test_message_payload_renders_multimodal_content_parts() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "A receipt for $12.50."}}], "model": "m"},
        )

    adapter = _adapter_with(handler)
    text = describe_image(adapter, _DATA_URL, user_context="can I afford this?")

    assert text == "A receipt for $12.50."
    user_message = captured["messages"][1]
    assert isinstance(user_message["content"], list)
    kinds = [part["type"] for part in user_message["content"]]
    assert kinds == ["text", "image_url"]
    assert user_message["content"][1]["image_url"]["url"] == _DATA_URL
    # The user's question is given as context to focus the description.
    assert "can I afford this?" in user_message["content"][0]["text"]


def test_plain_messages_still_send_string_content() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}], "model": "m"})

    adapter = _adapter_with(handler)
    adapter.complete([RuntimeMessage(role="user", content="hello")])

    assert captured["messages"][0]["content"] == "hello"


def test_describe_image_raises_runtime_unavailable_on_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    adapter = _adapter_with(handler)
    with pytest.raises(RuntimeUnavailableError):
        describe_image(adapter, _DATA_URL)
