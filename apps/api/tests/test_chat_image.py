import base64

import pytest

from family_cfo_ai_orchestrator import RuntimeToolCompletion, ToolCall
from family_cfo_api.api import chat as chat_module

_IMG = base64.b64encode(b"fake-jpeg-bytes").decode()


class _ScriptedRuntime:
    def __init__(self, turns):
        self._turns = turns
        self.seen_messages = []
        self._i = 0

    def complete_with_tools(self, messages, tools, *, temperature=0.2, max_tokens=400):
        self.seen_messages.append(list(messages))
        turn = self._turns[self._i]
        self._i += 1
        return turn

    def close(self):
        pass


def _post(client, token, **extra):
    return client.post(
        "/api/v1/chat/messages",
        headers={"Authorization": f"Bearer {token}"},
        json={"message": "Can I afford this?", **extra},
    )


@pytest.mark.anyio
async def test_image_described_and_numbers_grounded(demo_client, demo_token, monkeypatch) -> None:
    # Describer returns a description containing a price; the model quotes it.
    class _Describer:
        def close(self):
            pass

    monkeypatch.setattr(
        chat_module, "select_vision_describer", lambda e, h, s: (_Describer(), "describer")
    )
    monkeypatch.setattr(
        chat_module,
        "describe_image",
        lambda runtime, url, user_context="": "A price tag showing $999.99 for a smartphone.",
    )
    runtime = _ScriptedRuntime(
        [
            RuntimeToolCompletion(
                tool_calls=[ToolCall(id="c1", name="get_net_worth", arguments={})],
                text="",
                model="stub",
                raw={},
            ),
            RuntimeToolCompletion(
                tool_calls=[],
                # 999.99 comes only from the image description — must be grounded.
                text="The phone costs $999.99; based on your finances you can afford it.",
                model="stub",
                raw={},
            ),
        ]
    )
    monkeypatch.setattr(chat_module, "select_tool_runtime", lambda engine, household_id: runtime)

    resp = await _post(demo_client, demo_token, image_base64=_IMG, image_media_type="image/jpeg")

    assert resp.status_code == 200
    rec = resp.json()["recommendation"]
    assert "999.99" in rec["answer"]  # guardrail accepted the image-grounded number
    # The description was fed into the loop's user message.
    first_turn_user = runtime.seen_messages[0][1]
    assert "Attached photo" in first_turn_user.content
    assert "$999.99" in first_turn_user.content


@pytest.mark.anyio
async def test_image_without_vision_model_warns_gracefully(demo_client, demo_token) -> None:
    # Default settings: no vision main model, no describer.
    resp = await _post(demo_client, demo_token, image_base64=_IMG, image_media_type="image/jpeg")

    assert resp.status_code == 200
    warnings = resp.json()["recommendation"]["warnings"]
    assert any("could not be analyzed" in w for w in warnings)


@pytest.mark.anyio
async def test_oversized_image_rejected(demo_client, demo_engine, demo_token, tmp_path) -> None:
    big = base64.b64encode(b"x" * 2048).decode()
    # demo_settings has the default 10MB cap; build an app with a tiny cap instead.
    from family_cfo_api.config import Settings
    from family_cfo_api.main import create_app
    import httpx

    app = create_app(
        Settings(
            version="0.1.0",
            health_check_database=False,
            backup_encryption_key="jNM8CH53WkD3XZ3P8FluvPFI6BuGGvDIzy6vwiu3jbY=",
            max_upload_bytes=1024,
        ),
        engine=demo_engine,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        login = await client.post(
            "/api/v1/auth/sessions",
            json={"email": "demo@family-cfo.local", "password": "demo-password-123"},
        )
        token = login.json()["access_token"]
        resp = await _post(client, token, image_base64=big, image_media_type="image/jpeg")
    assert resp.status_code == 413


@pytest.mark.anyio
async def test_invalid_base64_rejected(demo_client, demo_token) -> None:
    resp = await _post(
        demo_client, demo_token, image_base64="not-base64!!!", image_media_type="image/jpeg"
    )
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_image_without_media_type_rejected(demo_client, demo_token) -> None:
    resp = await _post(demo_client, demo_token, image_base64=_IMG)
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_photo_response_tags_both_models(demo_engine, monkeypatch) -> None:
    """A photo answer is attributed to BOTH the vision describer and the chat model."""
    import httpx

    from family_cfo_api.config import Settings
    from family_cfo_api.main import create_app
    from family_cfo_ai_orchestrator import RuntimeToolCompletion

    class _Describer:
        def close(self):
            pass

    monkeypatch.setattr(
        chat_module, "select_vision_describer", lambda e, h, s: (_Describer(), "describer")
    )
    monkeypatch.setattr(
        chat_module, "describe_image", lambda runtime, url, user_context="": "A receipt for $42.00."
    )
    runtime = _ScriptedRuntime(
        [RuntimeToolCompletion(tool_calls=[], text="That $42.00 fits your budget.", model="m", raw={})]
    )
    monkeypatch.setattr(chat_module, "select_tool_runtime", lambda engine, household_id: runtime)

    settings = Settings(
        version="0.1.0",
        health_check_database=False,
        backup_encryption_key="jNM8CH53WkD3XZ3P8FluvPFI6BuGGvDIzy6vwiu3jbY=",
        ai_default_enabled=True,
        ai_default_model="Qwen/Qwen2.5-32B-Instruct",
        ai_vision_enabled=True,
        ai_vision_model="Qwen/Qwen2.5-VL-7B-Instruct",
    )
    app = create_app(settings, engine=demo_engine)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        login = await client.post(
            "/api/v1/auth/sessions",
            json={"email": "demo@family-cfo.local", "password": "demo-password-123"},
        )
        token = login.json()["access_token"]
        resp = await client.post(
            "/api/v1/chat/messages",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "message": "How does this affect my savings?",
                "image_base64": _IMG,
                "image_media_type": "image/jpeg",
            },
        )

    rec = resp.json()["recommendation"]
    assert rec["answered_by"] == "Qwen/Qwen2.5-32B-Instruct"
    assert rec["photo_described_by"] == "Qwen/Qwen2.5-VL-7B-Instruct"
    assert any("Qwen2.5-VL-7B" in a for a in rec["assumptions"])


# --- M84a: PDF attachments in chat ---


def _pdf_base64() -> str:
    from fpdf import FPDF

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=14)
    pdf.cell(text="Statement: total due 123.45")
    return base64.b64encode(bytes(pdf.output())).decode("ascii")


@pytest.mark.anyio
async def test_pdf_attachment_is_rasterized_for_the_describer(
    demo_client, demo_token, monkeypatch
) -> None:
    captured: dict[str, str] = {}

    class _Describer:
        def close(self) -> None:
            pass

    def _fake_describe(describer, image_data_url, user_context=""):
        captured["data_url"] = image_data_url
        return "A statement PDF showing a total due of $123.45."

    monkeypatch.setattr(
        chat_module, "select_vision_describer", lambda e, h, s: (_Describer(), "describer")
    )
    monkeypatch.setattr(chat_module, "describe_image", _fake_describe)

    response = await _post(
        demo_client,
        demo_token,
        image_base64=_pdf_base64(),
        image_media_type="application/pdf",
    )

    assert response.status_code == 200
    # The describer got a rendered page image, never raw PDF bytes.
    assert captured["data_url"].startswith("data:image/png;base64,")


@pytest.mark.anyio
async def test_corrupt_pdf_attachment_is_rejected(demo_client, demo_token) -> None:
    response = await _post(
        demo_client,
        demo_token,
        image_base64=base64.b64encode(b"not a pdf").decode(),
        image_media_type="application/pdf",
    )

    assert response.status_code == 422


# --- M85: data-file attachments in chat ---


@pytest.mark.anyio
async def test_data_file_preview_reaches_the_model_and_grounds_its_numbers(
    demo_client, demo_token, monkeypatch
) -> None:
    runtime = _ScriptedRuntime(
        [
            RuntimeToolCompletion(
                tool_calls=[],
                # 151.69 comes only from the attached file's summary — grounded.
                text="Your three deposits total $151.69 this week.",
                model="stub",
                raw={},
            ),
        ]
    )
    monkeypatch.setattr(chat_module, "select_tool_runtime", lambda engine, household_id: runtime)

    csv_bytes = (
        b"Date,Merchant,Amount\n"
        b"2026-01-05,Whole Foods,84.20\n"
        b"2026-01-06,Shell,52.00\n"
        b"2026-01-07,Netflix,15.49\n"
    )
    response = await _post(
        demo_client,
        demo_token,
        data_file_base64=base64.b64encode(csv_bytes).decode(),
        data_file_name="spending.csv",
    )

    assert response.status_code == 200
    body = response.json()
    # The figure from the file survived the guardrail (grounded context).
    assert "151.69" in body["recommendation"]["answer"]
    # The bounded preview was injected into the user turn.
    user_msg = runtime.seen_messages[0][-1]
    assert "Attached data file summary" in user_msg.content
    assert "spending.csv" in user_msg.content


@pytest.mark.anyio
async def test_corrupt_data_file_base64_rejected(demo_client, demo_token) -> None:
    response = await _post(
        demo_client, demo_token, data_file_base64="not base64!!", data_file_name="x.csv"
    )

    assert response.status_code == 422
