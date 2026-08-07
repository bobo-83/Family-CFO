

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
