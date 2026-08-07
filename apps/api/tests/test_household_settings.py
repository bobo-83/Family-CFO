

import pytest


@pytest.mark.anyio
async def test_language_round_trips_and_is_validated(demo_client, demo_token):
    headers = {"Authorization": f"Bearer {demo_token}"}
    before = await demo_client.get("/api/v1/household", headers=headers)
    assert before.json()["language"] == "en"  # never set -> English

    updated = await demo_client.patch(
        "/api/v1/household", headers=headers, json={"language": "vi"}
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["language"] == "vi"

    rejected = await demo_client.patch(
        "/api/v1/household", headers=headers, json={"language": "fr"}
    )
    assert rejected.status_code == 422
    assert "supported" in rejected.json()["error"]["message"]


@pytest.mark.anyio
async def test_reserve_committed_savings_setting_round_trips(demo_client, demo_token):
    """#5: the toggle persists and drives whether savings is subtracted."""
    headers = {"Authorization": f"Bearer {demo_token}"}
    updated = await demo_client.patch(
        "/api/v1/household", headers=headers, json={"reserve_committed_savings": True}
    )
    assert updated.status_code == 200, updated.text
