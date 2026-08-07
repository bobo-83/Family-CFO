"""#10 phase 4: the prose the API itself emits, in the household's language."""

import pytest

from family_cfo_api import localization


def test_accept_language_header_reduces_to_a_supported_locale() -> None:
    assert localization.normalize("vi-VN,vi;q=0.9,en;q=0.8") == "vi"
    assert localization.normalize("lt") == "lt"
    assert localization.normalize("en-US") == "en"
    # Unsupported or absent falls back to English rather than erroring.
    assert localization.normalize("fr-FR") == "en"
    assert localization.normalize(None) == "en"
    assert localization.normalize("") == "en"


def test_untranslated_message_degrades_to_english_not_a_key() -> None:
    """A missing entry must still read as a usable sentence."""
    assert localization.translate("Some brand new error", "vi") == "Some brand new error"


def test_known_messages_translate() -> None:
    assert localization.translate("Account not found", "vi") == "Không tìm thấy tài khoản"
    assert localization.translate("Account not found", "lt") == "Sąskaita nerasta"
    assert localization.translate("Account not found", "en") == "Account not found"


@pytest.mark.anyio
async def test_error_detail_is_localized_at_the_response_boundary(
    demo_client, demo_token
) -> None:
    """Raise sites keep writing English; the reader gets their language."""
    headers = {"Authorization": f"Bearer {demo_token}"}
    missing = "/api/v1/savings/contributions/does-not-exist"

    english = await demo_client.delete(missing, headers=headers)
    assert english.status_code == 404
    assert english.json()["error"]["message"] == "Contribution not found"

    vietnamese = await demo_client.delete(
        missing, headers={**headers, "Accept-Language": "vi"}
    )
    assert vietnamese.status_code == 404
    assert vietnamese.json()["error"]["message"] == "Không tìm thấy khoản đóng góp"


def test_export_readme_follows_the_household_language(demo_engine) -> None:
    """#10: the export is read long after the request, so it uses the
    household's own setting — and file NAMES stay English because they are the
    actual entries in the zip."""
    from family_cfo_api import household_export

    vi = household_export.readme_for("vi")
    assert "xuất dữ liệu" in vi
    assert "transactions.csv" in vi  # the real filename, untranslated
    lt = household_export.readme_for("lt")
    assert "duomenų eksportas" in lt
    assert household_export.readme_for(None) == household_export._README
