from family_cfo_api.logging import redact_message


def test_redact_message_removes_sensitive_values() -> None:
    message = "token=abc123 password:secret api_key=my-key account=checking"

    assert redact_message(message) == (
        "token=[REDACTED] password:[REDACTED] api_key=[REDACTED] account=checking"
    )

