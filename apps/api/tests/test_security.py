"""Consolidated security-control tests (M13).

Complements the per-feature tests: role gating lives with each router's tests
and paired-device revocation lives in test_pairing_api.py; this file asserts the
cross-cutting security properties as a group so a regression is obvious.
"""

import logging
from pathlib import Path

import pytest

from family_cfo_api import fixtures
from family_cfo_api.logging import RedactingFilter

REPO_ROOT = Path(__file__).resolve().parents[3]

# First-party source trees (never third-party .venv/node_modules).
FIRST_PARTY_SOURCE = [
    REPO_ROOT / "apps" / "api" / "src",
    REPO_ROOT / "apps" / "web" / "src" / "app",
    *(REPO_ROOT / "services").glob("*/src"),
]

TELEMETRY_INDICATORS = [
    "segment.io",
    "segment.com",
    "mixpanel",
    "posthog",
    "sentry-sdk",
    "sentry_sdk",
    "google-analytics",
    "googletagmanager",
    "amplitude",
    "datadog",
    "newrelic",
]


async def _adult_token(demo_client, demo_token: str) -> str:
    """Create an adult member via the owner, then log in as them."""
    headers = {"Authorization": f"Bearer {demo_token}"}
    await demo_client.post(
        "/api/v1/household/members",
        headers=headers,
        json={
            "email": "adult-sec@example.com",
            "password": "password-123",
            "display_name": "Adult Sec",
            "role": "adult",
        },
    )
    login = await demo_client.post(
        "/api/v1/auth/sessions",
        json={"email": "adult-sec@example.com", "password": "password-123"},
    )
    return login.json()["access_token"]


@pytest.mark.anyio
async def test_viewer_is_blocked_from_writes_across_resources(
    demo_client, demo_viewer_token
) -> None:
    headers = {"Authorization": f"Bearer {demo_viewer_token}"}
    writes = [
        ("post", "/api/v1/accounts", {"name": "X", "type": "checking", "currency": "USD"}),
        (
            "post",
            "/api/v1/transactions",
            {
                "account_id": fixtures.DEMO_CHECKING_ACCOUNT_ID,
                "occurred_at": "2026-01-01",
                "amount": {"amount_minor": -100, "currency": "USD"},
            },
        ),
        (
            "post",
            "/api/v1/bills",
            {
                "name": "X",
                "amount": {"amount_minor": 100, "currency": "USD"},
                "frequency": "monthly",
            },
        ),
        (
            "post",
            "/api/v1/income",
            {
                "name": "X",
                "amount": {"amount_minor": 100, "currency": "USD"},
                "frequency": "monthly",
            },
        ),
        (
            "post",
            "/api/v1/goals",
            {"name": "X", "type": "other", "target": {"amount_minor": 100, "currency": "USD"}},
        ),
        ("post", "/api/v1/backups", None),
        ("get", "/api/v1/audit", None),
        (
            "post",
            "/api/v1/household/members",
            {
                "email": "z@example.com",
                "password": "password-123",
                "display_name": "Z",
                "role": "adult",
            },
        ),
    ]
    for method, path, body in writes:
        call = getattr(demo_client, method)
        response = await (
            call(path, headers=headers, json=body)
            if body is not None
            else call(path, headers=headers)
        )
        assert response.status_code == 403, (
            f"{method.upper()} {path} should be 403 for a viewer, got {response.status_code}"
        )


@pytest.mark.anyio
async def test_adult_can_write_household_data_but_not_owner_only_actions(
    demo_client, demo_token
) -> None:
    token = await _adult_token(demo_client, demo_token)
    headers = {"Authorization": f"Bearer {token}"}

    # Allowed: money-editing writes (ADR 0034 User preset — bills, budgets...).
    created = await demo_client.post(
        "/api/v1/bills",
        headers=headers,
        json={
            "name": "Adult Bill", "amount": {"amount_minor": 1000, "currency": "USD"},
            "frequency": "monthly", "next_due_date": "2026-08-01",
        },
    )
    assert created.status_code == 201

    # Blocked (ADR 0034): a User does NOT manage the balance sheet.
    assert (
        await demo_client.post(
            "/api/v1/accounts",
            headers=headers,
            json={"name": "Adult Acct", "type": "savings", "currency": "USD"},
        )
    ).status_code == 403

    # Blocked: admin-only actions.
    assert (await demo_client.post("/api/v1/backups", headers=headers)).status_code == 403
    assert (await demo_client.get("/api/v1/audit", headers=headers)).status_code == 403
    assert (
        await demo_client.post(
            "/api/v1/household/members",
            headers=headers,
            json={
                "email": "q@example.com",
                "password": "password-123",
                "display_name": "Q",
                "role": "viewer",
            },
        )
    ).status_code == 403


def test_redacting_filter_scrubs_sensitive_values_through_the_handler(caplog) -> None:
    logger = logging.getLogger("family_cfo_api.test_security_redaction")
    logger.addFilter(RedactingFilter())
    with caplog.at_level(logging.INFO):
        logger.info("login attempt password=hunter2 token=abc123def")

    assert "hunter2" not in caplog.text
    assert "abc123def" not in caplog.text
    assert "[REDACTED]" in caplog.text


def test_no_telemetry_or_analytics_sdk_in_first_party_source() -> None:
    offenders: list[str] = []
    for root in FIRST_PARTY_SOURCE:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix not in {".py", ".ts", ".html", ".json"}:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore").lower()
            for indicator in TELEMETRY_INDICATORS:
                if indicator in text:
                    offenders.append(f"{path}: {indicator}")

    assert offenders == [], f"Unexpected telemetry/analytics references: {offenders}"
