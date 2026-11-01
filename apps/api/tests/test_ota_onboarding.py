"""Release-generated onboarding must agree with account-only sign-in."""

from pathlib import Path


def test_ota_install_page_preserves_sessions_and_uses_account_sign_in() -> None:
    script = (Path(__file__).resolve().parents[3] / "scripts/deploy-ios-ota.sh").read_text()
    page = script.split('cat > "$EXPORT_DIR/index.html" <<HTML\n', 1)[1].split("\nHTML\n", 1)[0]
    guidance = page.split('<div id="installed">', 1)[1].split("</div>", 1)[0]
    assert "sign in with your own account" in guidance
    assert "Existing signed-in devices stay signed in" in guidance
    assert "generate a pairing code" not in guidance
    assert "scan it once" not in guidance
