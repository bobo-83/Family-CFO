"""M61: income analysis from checking deposits + annual tax estimate."""

from datetime import date, timedelta

import pytest


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _seed_checking_with_payroll(client, token: str) -> dict[str, str]:
    """A checking account with 6 biweekly ACME payroll deposits + 1 one-off."""
    headers = _headers(token)
    account = await client.post(
        "/api/v1/accounts",
        headers=headers,
        json={"name": "Everyday Checking", "type": "checking", "currency": "USD"},
    )
    account_id = account.json()["id"]
    ids: dict[str, str] = {}
    today = date.today()
    for i in range(6):
        occurred = today - timedelta(days=14 * (6 - i))
        response = await client.post(
            "/api/v1/transactions",
            headers=headers,
            json={
                "account_id": account_id,
                "occurred_at": occurred.isoformat(),
                "amount": {"amount_minor": 461_538, "currency": "USD"},
                "merchant": "ACME CORP PAYROLL",
            },
        )
        ids[f"payroll_{i}"] = response.json()["id"]
    one_off = await client.post(
        "/api/v1/transactions",
        headers=headers,
        json={
            "account_id": account_id,
            "occurred_at": (today - timedelta(days=40)).isoformat(),
            "amount": {"amount_minor": 90_000, "currency": "USD"},
            "merchant": "VENMO CASHOUT",
        },
    )
    ids["one_off"] = one_off.json()["id"]
    return ids


async def _analysis(client, token: str) -> dict:
    response = await client.get("/api/v1/income/analysis", headers=_headers(token))
    assert response.status_code == 200
    return response.json()


@pytest.mark.anyio
async def test_detects_payroll_with_evidence_and_rollup(demo_client, demo_token) -> None:
    await _seed_checking_with_payroll(demo_client, demo_token)

    body = await _analysis(demo_client, demo_token)

    assert len(body["sources"]) == 1
    source = body["sources"][0]
    assert source["source_key"] == "acme corp payroll"
    assert source["frequency"] == "biweekly"
    assert source["typical_amount"]["amount_minor"] == 461_538
    assert len(source["transactions"]) == 6
    assert source["total_amount"]["amount_minor"] == 6 * 461_538

    # The one-off cash-out is offered for manual classification, not counted.
    assert [t["name"] for t in body["other_inflows"]] == ["VENMO CASHOUT"]
    # M62: full evidence details ride along on every transaction.
    other = body["other_inflows"][0]
    assert other["merchant"] == "VENMO CASHOUT"
    assert other["account_name"] == "Everyday Checking"
    assert source["transactions"][0]["account_name"] == "Everyday Checking"
    assert body["rollup"]["annual_income"]["amount_minor"] == 6 * 461_538
    assert body["rollup"]["transaction_count"] == 6


@pytest.mark.anyio
async def test_tax_estimate_grosses_up_net_income_by_default(demo_client, demo_token) -> None:
    await _seed_checking_with_payroll(demo_client, demo_token)

    tax = (await _analysis(demo_client, demo_token))["tax"]

    net = 6 * 461_538
    assert tax["filing_status"] == "married_joint"
    assert tax["income_treated_as_net"] is True
    assert tax["net_income"]["amount_minor"] == net
    # gross = net + tax(gross): strictly larger, and internally consistent.
    assert tax["gross_income"]["amount_minor"] > net
    assert (
        abs(tax["gross_income"]["amount_minor"] - tax["total_tax"]["amount_minor"] - net) <= 2
    )
    assert any("state income tax is NOT included" in a for a in tax["assumptions"])


@pytest.mark.anyio
async def test_exclude_removes_a_deposit_and_shrinks_the_rollup(
    demo_client, demo_token
) -> None:
    ids = await _seed_checking_with_payroll(demo_client, demo_token)
    headers = _headers(demo_token)

    response = await demo_client.post(
        "/api/v1/income/analysis/overrides",
        headers=headers,
        json={"transaction_id": ids["payroll_0"], "verdict": "exclude"},
    )
    assert response.status_code == 204

    body = await _analysis(demo_client, demo_token)
    assert body["rollup"]["annual_income"]["amount_minor"] == 5 * 461_538
    assert len(body["sources"][0]["transactions"]) == 5
    excluded = [t for t in body["other_inflows"] if t["excluded"]]
    assert [t["transaction_id"] for t in excluded] == [ids["payroll_0"]]

    # "clear" restores the deposit to its detected source.
    await demo_client.post(
        "/api/v1/income/analysis/overrides",
        headers=headers,
        json={"transaction_id": ids["payroll_0"], "verdict": "clear"},
    )
    body = await _analysis(demo_client, demo_token)
    assert body["rollup"]["annual_income"]["amount_minor"] == 6 * 461_538


@pytest.mark.anyio
async def test_include_adds_a_missed_deposit(demo_client, demo_token) -> None:
    ids = await _seed_checking_with_payroll(demo_client, demo_token)

    response = await demo_client.post(
        "/api/v1/income/analysis/overrides",
        headers=_headers(demo_token),
        json={"transaction_id": ids["one_off"], "verdict": "include"},
    )
    assert response.status_code == 204

    body = await _analysis(demo_client, demo_token)
    manual = [s for s in body["sources"] if s["manually_added"]]
    assert len(manual) == 1
    assert manual[0]["name"] == "Added by you"
    assert [t["transaction_id"] for t in manual[0]["transactions"]] == [ids["one_off"]]
    assert body["rollup"]["annual_income"]["amount_minor"] == 6 * 461_538 + 90_000
    assert body["other_inflows"] == []


@pytest.mark.anyio
async def test_settings_switch_filing_status_and_gross_treatment(
    demo_client, demo_token
) -> None:
    await _seed_checking_with_payroll(demo_client, demo_token)

    response = await demo_client.put(
        "/api/v1/income/analysis/settings",
        headers=_headers(demo_token),
        json={"tax_filing_status": "single", "income_treated_as_net": False},
    )
    assert response.status_code == 204

    tax = (await _analysis(demo_client, demo_token))["tax"]
    assert tax["filing_status"] == "single"
    assert tax["income_treated_as_net"] is False
    # Treated as gross: the income IS the gross figure.
    assert tax["gross_income"]["amount_minor"] == 6 * 461_538
    assert tax["net_income"] is None


# --- M65: amount clustering + state tax ---


@pytest.mark.anyio
async def test_paycheck_detected_inside_mixed_amount_transfer_label(
    demo_client, demo_token
) -> None:
    """Biweekly ~$2,830 deposits auto-detect even when big one-offs share the label."""
    headers = _headers(demo_token)
    checking = (
        await demo_client.post(
            "/api/v1/accounts",
            headers=headers,
            json={"name": "My Checking", "type": "checking", "currency": "USD"},
        )
    ).json()["id"]
    today = date.today()
    for i in range(6):
        await demo_client.post(
            "/api/v1/transactions",
            headers=headers,
            json={
                "account_id": checking,
                "occurred_at": (today - timedelta(days=14 * (6 - i))).isoformat(),
                "amount": {"amount_minor": 283_078 + i, "currency": "USD"},
                "merchant": "Online Transfer",
            },
        )
    # Both one-offs sit far outside the paycheck's amount band (>30% gap).
    for amount, days in ((2_312_400, 40), (800_000, 3)):
        await demo_client.post(
            "/api/v1/transactions",
            headers=headers,
            json={
                "account_id": checking,
                "occurred_at": (today - timedelta(days=days)).isoformat(),
                "amount": {"amount_minor": amount, "currency": "USD"},
                "merchant": "Online Transfer",
            },
        )

    body = await _analysis(demo_client, demo_token)

    assert len(body["sources"]) == 1
    source = body["sources"][0]
    assert source["frequency"] == "biweekly"
    assert len(source["transactions"]) == 6
    assert "2,830" in source["name"]  # disambiguated with the typical amount
    # The one-offs stay offered, not silently absorbed.
    assert len(body["other_inflows"]) == 2


@pytest.mark.anyio
async def test_state_setting_changes_the_tax_estimate(demo_client, demo_token) -> None:
    await _seed_checking_with_payroll(demo_client, demo_token)
    headers = _headers(demo_token)

    response = await demo_client.put(
        "/api/v1/income/analysis/settings",
        headers=headers,
        json={"tax_filing_status": "married_joint", "income_treated_as_net": True, "state": "ca"},
    )
    assert response.status_code == 204

    tax = (await _analysis(demo_client, demo_token))["tax"]
    assert tax["state"] == "CA"
    assert tax["state_income_tax"] is not None
    assert any("2025 FTB brackets" in a for a in tax["assumptions"])

    bad = await demo_client.put(
        "/api/v1/income/analysis/settings",
        headers=headers,
        json={"tax_filing_status": "single", "income_treated_as_net": True, "state": "ZZ"},
    )
    assert bad.status_code == 422


# --- M63: internal transfers, reject, coverage ---


@pytest.mark.anyio
async def test_income_categorized_deposit_counts_over_transfer_heuristic(
    demo_client, demo_token
) -> None:
    """ADR 0053: a deposit the user files under Income counts as income even when
    it looks like an internal transfer (a matching outflow) and has no override."""
    headers = _headers(demo_token)
    checking = (
        await demo_client.post(
            "/api/v1/accounts",
            headers=headers,
            json={"name": "Checking", "type": "checking", "currency": "USD"},
        )
    ).json()["id"]
    savings = (
        await demo_client.post(
            "/api/v1/accounts",
            headers=headers,
            json={"name": "Savings", "type": "savings", "currency": "USD"},
        )
    ).json()["id"]
    income_cat = (
        await demo_client.post("/api/v1/categories", headers=headers, json={"name": "Income"})
    ).json()["id"]
    today = date.today()
    # A big inflow WITH a matching outflow — the transfer heuristic would hide it.
    deposit = await demo_client.post(
        "/api/v1/transactions",
        headers=headers,
        json={
            "account_id": checking,
            "occurred_at": (today - timedelta(days=6)).isoformat(),
            "amount": {"amount_minor": 5_000_000, "currency": "USD"},
            "merchant": "Online Transfer",
            "category_id": income_cat,
        },
    )
    await demo_client.post(
        "/api/v1/transactions",
        headers=headers,
        json={
            "account_id": savings,
            "occurred_at": (today - timedelta(days=7)).isoformat(),
            "amount": {"amount_minor": -5_000_000, "currency": "USD"},
            "merchant": "Online Transfer",
        },
    )

    body = await _analysis(demo_client, demo_token)

    assert body["rollup"]["annual_income"]["amount_minor"] >= 5_000_000
    counted_ids = {t["transaction_id"] for s in body["sources"] for t in s["transactions"]}
    assert deposit.json()["id"] in counted_ids

    # An explicit "not income" still wins over the category.
    await demo_client.post(
        "/api/v1/income/analysis/overrides",
        headers=headers,
        json={"transaction_id": deposit.json()["id"], "verdict": "exclude"},
    )
    body = await _analysis(demo_client, demo_token)
    counted_ids = {t["transaction_id"] for s in body["sources"] for t in s["transactions"]}
    assert deposit.json()["id"] not in counted_ids


@pytest.mark.anyio
async def test_brokerage_income_deposit_counts_with_its_bank(demo_client, demo_token) -> None:
    """ADR 0054: an RSU/ESPP deposit filed under Income in a BROKERAGE account
    (not checking) is counted, and carries its bank for the evidence detail."""
    headers = _headers(demo_token)
    brokerage = (
        await demo_client.post(
            "/api/v1/accounts",
            headers=headers,
            json={"name": "Schwab Brokerage", "type": "brokerage", "currency": "USD"},
        )
    ).json()["id"]
    income_cat = (
        await demo_client.post("/api/v1/categories", headers=headers, json={"name": "Income"})
    ).json()["id"]
    deposit = await demo_client.post(
        "/api/v1/transactions",
        headers=headers,
        json={
            "account_id": brokerage,
            "occurred_at": (date.today() - timedelta(days=8)).isoformat(),
            "amount": {"amount_minor": 5_883_886, "currency": "USD"},
            "merchant": "Broadcom Inc",
            "category_id": income_cat,
        },
    )

    body = await _analysis(demo_client, demo_token)

    assert body["rollup"]["annual_income"]["amount_minor"] >= 5_883_886
    evidence = [t for s in body["sources"] for t in s["transactions"] if t["transaction_id"] == deposit.json()["id"]]
    assert evidence, "brokerage income deposit should appear as a source transaction"
    # The bank rides on the evidence (None here since a manual account has no
    # synced institution; populated for real synced accounts).
    assert "institution" in evidence[0]


@pytest.mark.anyio
async def test_liability_account_payment_is_never_income(demo_client, demo_token) -> None:
    """ADR 0055: a positive posting on a loan/lease account is a debt PAYMENT
    credit, not income — even if it was (mis)categorized as Income."""
    headers = _headers(demo_token)
    loan = (
        await demo_client.post(
            "/api/v1/accounts",
            headers=headers,
            json={"name": "Subaru Lease", "type": "auto_loan", "currency": "USD"},
        )
    ).json()["id"]
    income_cat = (
        await demo_client.post("/api/v1/categories", headers=headers, json={"name": "Income"})
    ).json()["id"]
    payment = await demo_client.post(
        "/api/v1/transactions",
        headers=headers,
        json={
            "account_id": loan,
            "occurred_at": (date.today() - timedelta(days=5)).isoformat(),
            "amount": {"amount_minor": 64_973, "currency": "USD"},
            "merchant": "Payment",
            "category_id": income_cat,
        },
    )

    body = await _analysis(demo_client, demo_token)

    counted_ids = {t["transaction_id"] for s in body["sources"] for t in s["transactions"]}
    assert payment.json()["id"] not in counted_ids


@pytest.mark.anyio
async def test_matched_pair_transfer_is_hidden_entirely(demo_client, demo_token) -> None:
    """A deposit whose amount left a sibling account is money movement, not income."""
    headers = _headers(demo_token)
    checking = (
        await demo_client.post(
            "/api/v1/accounts",
            headers=headers,
            json={"name": "My Checking", "type": "checking", "currency": "USD"},
        )
    ).json()["id"]
    savings = (
        await demo_client.post(
            "/api/v1/accounts",
            headers=headers,
            json={"name": "My Savings", "type": "savings", "currency": "USD"},
        )
    ).json()["id"]
    today = date.today()
    inflow = await demo_client.post(
        "/api/v1/transactions",
        headers=headers,
        json={
            "account_id": checking,
            "occurred_at": (today - timedelta(days=10)).isoformat(),
            "amount": {"amount_minor": 500_000, "currency": "USD"},
            "merchant": "Online Transfer",
        },
    )
    await demo_client.post(
        "/api/v1/transactions",
        headers=headers,
        json={
            "account_id": savings,
            "occurred_at": (today - timedelta(days=11)).isoformat(),
            "amount": {"amount_minor": -500_000, "currency": "USD"},
            "merchant": "Online Transfer",
        },
    )

    body = await _analysis(demo_client, demo_token)

    assert body["sources"] == []
    assert body["other_inflows"] == []

    # An explicit include verdict overrides suppression — the user decides.
    await demo_client.post(
        "/api/v1/income/analysis/overrides",
        headers=headers,
        json={"transaction_id": inflow.json()["id"], "verdict": "include"},
    )
    body = await _analysis(demo_client, demo_token)
    assert body["rollup"]["annual_income"]["amount_minor"] == 500_000


@pytest.mark.anyio
async def test_bank_labeled_internal_transfer_is_hidden(demo_client, demo_token) -> None:
    headers = _headers(demo_token)
    checking = (
        await demo_client.post(
            "/api/v1/accounts",
            headers=headers,
            json={"name": "My Checking", "type": "checking", "currency": "USD"},
        )
    ).json()["id"]
    await demo_client.post(
        "/api/v1/transactions",
        headers=headers,
        json={
            "account_id": checking,
            "occurred_at": (date.today() - timedelta(days=5)).isoformat(),
            "amount": {"amount_minor": 700_000, "currency": "USD"},
            "merchant": "Internal Transfer Credit Savings",
            "description": "Internal Transfer Credit: Savings -2061",
        },
    )

    body = await _analysis(demo_client, demo_token)

    assert body["other_inflows"] == []
    assert body["rollup"]["annual_income"]["amount_minor"] == 0


@pytest.mark.anyio
async def test_coverage_warning_for_partial_history(demo_client, demo_token) -> None:
    await _seed_checking_with_payroll(demo_client, demo_token)  # starts ~84 days ago

    body = await _analysis(demo_client, demo_token)

    assert body["coverage_warning"] is not None
    assert "not a full year" in body["coverage_warning"]
    assert 80 <= body["rollup"]["coverage_days"] <= 90
    assert body["rollup"]["coverage_start"] is not None


@pytest.mark.anyio
async def test_no_coverage_warning_with_full_window(demo_client, demo_token) -> None:
    headers = _headers(demo_token)
    checking = (
        await demo_client.post(
            "/api/v1/accounts",
            headers=headers,
            json={"name": "My Checking", "type": "checking", "currency": "USD"},
        )
    ).json()["id"]
    await demo_client.post(
        "/api/v1/transactions",
        headers=headers,
        json={
            "account_id": checking,
            "occurred_at": (date.today() - timedelta(days=364)).isoformat(),
            "amount": {"amount_minor": 10_000, "currency": "USD"},
            "merchant": "Old Deposit",
        },
    )

    body = await _analysis(demo_client, demo_token)

    assert body["coverage_warning"] is None
    assert body["rollup"]["coverage_days"] >= 358


@pytest.mark.anyio
async def test_override_on_foreign_transaction_is_404(demo_client, demo_token) -> None:
    response = await demo_client.post(
        "/api/v1/income/analysis/overrides",
        headers=_headers(demo_token),
        json={"transaction_id": "00000000-0000-0000-0000-000000000000", "verdict": "exclude"},
    )
    assert response.status_code == 404


@pytest.mark.anyio
async def test_viewer_cannot_edit_overrides_or_settings(
    demo_client, demo_viewer_token
) -> None:
    headers = _headers(demo_viewer_token)
    override = await demo_client.post(
        "/api/v1/income/analysis/overrides",
        headers=headers,
        json={"transaction_id": "x", "verdict": "exclude"},
    )
    assert override.status_code == 403
    settings = await demo_client.put(
        "/api/v1/income/analysis/settings",
        headers=headers,
        json={"tax_filing_status": "single", "income_treated_as_net": True},
    )
    assert settings.status_code == 403


# --- M73: compensation profiles + W2 scan ---


async def _add_earner(client, token: str, **overrides) -> dict:
    body = {
        "label": "Primary earner",
        "base_salary_minor": 20_000_000,  # $200k base
        "rsu_annual_minor": 16_000_000,  # $160k/yr RSU
        "rsu_frequency": "quarterly",
        "rsu_next_vest_date": (date.today() + timedelta(days=20)).isoformat(),
        "bonus_percent": 25,
        "bonus_month": 12,
        **overrides,
    }
    response = await client.post(
        "/api/v1/income/profile/earners", headers=_headers(token), json=body
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.anyio
async def test_declared_profile_drives_the_tax_estimate(demo_client, demo_token) -> None:
    await _seed_checking_with_payroll(demo_client, demo_token)
    await _add_earner(demo_client, demo_token)

    body = await _analysis(demo_client, demo_token)

    profile = body["profile"]
    # expected gross = 200k base + 160k RSU + 25% * 200k bonus = 410k
    assert profile["expected_annual_gross"]["amount_minor"] == 41_000_000
    tax = body["tax"]
    assert tax["gross_income"]["amount_minor"] == 41_000_000
    assert tax["net_income"] is None  # declared amounts ARE gross — no gross-up
    assert any("DECLARED compensation profile" in a for a in tax["assumptions"])
    assert any("stock price" in a for a in tax["assumptions"])
    # M79: the pre-tax nature of declared amounts is stated, not implied.
    assert any("PRE-TAX" in a and "withheld at vest" in a for a in tax["assumptions"])
    # Declared profile removes the deposit-coverage caveat from the estimate.
    assert body["coverage_warning"] is None
    # Deposit-based rollup stays alongside as observed income.
    assert body["rollup"]["annual_income"]["amount_minor"] == 6 * 461_538


@pytest.mark.anyio
async def test_expected_events_roll_quarterly_vests_and_bonus(demo_client, demo_token) -> None:
    await _add_earner(demo_client, demo_token)

    profile = (await _analysis(demo_client, demo_token))["profile"]

    events = profile["expected_events"]
    vests = [e for e in events if "RSU vest" in e["label"]]
    assert len(vests) == 2
    assert vests[0]["amount"]["amount_minor"] == 4_000_000  # 160k / 4
    first = date.fromisoformat(vests[0]["date"])
    second = date.fromisoformat(vests[1]["date"])
    assert (second.month - first.month) % 12 == 3
    bonus = [e for e in events if "bonus" in e["label"]]
    assert bonus and bonus[0]["amount"]["amount_minor"] == 5_000_000  # 25% of 200k


@pytest.mark.anyio
async def test_w2_actuals_add_a_comparison_line(demo_client, demo_token) -> None:
    await _add_earner(
        demo_client,
        demo_token,
        w2_year=2025,
        w2_wages_minor=38_000_000,
        w2_withheld_minor=7_600_000,
    )

    tax = (await _analysis(demo_client, demo_token))["tax"]

    assert any("W2: wages" in a and "20.0%" in a for a in tax["assumptions"])


@pytest.mark.anyio
async def test_earner_delete_restores_deposit_based_estimate(demo_client, demo_token) -> None:
    await _seed_checking_with_payroll(demo_client, demo_token)
    earner = await _add_earner(demo_client, demo_token)

    deleted = await demo_client.delete(
        f"/api/v1/income/profile/earners/{earner['id']}", headers=_headers(demo_token)
    )
    assert deleted.status_code == 204

    body = await _analysis(demo_client, demo_token)
    assert body["profile"] is None
    assert body["tax"]["net_income"] is not None  # back to gross-up mode


@pytest.mark.anyio
async def test_viewer_cannot_manage_earners(demo_client, demo_viewer_token) -> None:
    response = await demo_client.post(
        "/api/v1/income/profile/earners",
        headers=_headers(demo_viewer_token),
        json={"label": "Nope"},
    )
    assert response.status_code == 403


def test_w2_scan_parse_tolerates_model_output() -> None:
    from family_cfo_api.api.income_analysis import parse_w2_scan

    good = parse_w2_scan(
        '```json\n{"year": 2025, "employer": "ACME Corp", "wages": 380000.25, '
        '"federal_withheld": 76000}\n```'
    )
    assert good.year == 2025
    assert good.employer == "ACME Corp"
    assert good.wages_minor == 38_000_025
    assert good.federal_withheld_minor == 7_600_000
    assert "CONFIRM" in good.note

    garbage = parse_w2_scan("I see a tax document with some numbers.")
    assert garbage.wages_minor is None
    assert "could not be read" in garbage.note


# --- M77: PDF W2 upload ---


class _StubDescriber:
    """Captures what the endpoint sends to the vision model."""

    W2_JSON = (
        '{"year": 2025, "employer": "ACME Corp", "wages": 385412.60, '
        '"federal_withheld": 78903.15}'
    )

    def __init__(self, responses: list[str] | None = None) -> None:
        self.data_urls: list[str] = []
        self._responses = responses or [self.W2_JSON]

    def complete(self, messages, temperature=0.0, max_tokens=0):
        self.data_urls.append(messages[0].image_data_url)
        text_value = self._responses[min(len(self.data_urls) - 1, len(self._responses) - 1)]

        class _Completion:
            text = text_value

        return _Completion()

    def close(self) -> None:
        pass


def _w2_pdf_base64(cover_pages: int = 0) -> str:
    import base64

    from fpdf import FPDF

    pdf = FPDF()
    pdf.set_font("Helvetica", size=14)
    for _ in range(cover_pages):
        pdf.add_page()
        pdf.cell(text="Important tax document enclosed - see the next page")
    pdf.add_page()
    pdf.cell(text="Form W-2 Wage and Tax Statement 2025")
    pdf.ln(10)
    pdf.cell(text="Employer: ACME Corp")
    pdf.ln(10)
    pdf.cell(text="Box 1 wages: 385412.60   Box 2 federal withheld: 78903.15")
    return base64.b64encode(bytes(pdf.output())).decode("ascii")


@pytest.mark.anyio
async def test_w2_scan_accepts_a_pdf(demo_client, demo_token, monkeypatch) -> None:
    """M77: a PDF W2 is rasterized to a PNG page image for the vision model."""
    from family_cfo_api import ai_runtime_selection

    stub = _StubDescriber()
    monkeypatch.setattr(
        ai_runtime_selection, "select_vision_describer", lambda engine, hh: (stub, "test")
    )

    response = await demo_client.post(
        "/api/v1/income/profile/scan-w2",
        headers={"Authorization": f"Bearer {demo_token}"},
        json={"image_base64": _w2_pdf_base64(), "image_media_type": "application/pdf"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["year"] == 2025
    assert body["wages_minor"] == 38_541_260
    # The model got a rendered page image, never raw PDF bytes.
    assert stub.data_urls[0].startswith("data:image/png;base64,")


@pytest.mark.anyio
async def test_w2_scan_walks_pdf_pages(demo_client, demo_token, monkeypatch) -> None:
    """M78: a cover sheet on page 1 is skipped; the W-2 on page 2 is read."""
    from family_cfo_api import ai_runtime_selection

    stub = _StubDescriber(
        responses=["A cover page with no amounts on it.", _StubDescriber.W2_JSON]
    )
    monkeypatch.setattr(
        ai_runtime_selection, "select_vision_describer", lambda engine, hh: (stub, "test")
    )

    response = await demo_client.post(
        "/api/v1/income/profile/scan-w2",
        headers={"Authorization": f"Bearer {demo_token}"},
        json={
            "image_base64": _w2_pdf_base64(cover_pages=1),
            "image_media_type": "application/pdf",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["wages_minor"] == 38_541_260
    assert body["note"].startswith("Read from page 2 of the PDF.")
    assert len(stub.data_urls) == 2  # one model call per page until the hit


@pytest.mark.anyio
async def test_w2_scan_reports_when_no_page_reads_as_a_w2(
    demo_client, demo_token, monkeypatch
) -> None:
    from family_cfo_api import ai_runtime_selection

    stub = _StubDescriber(responses=["Nothing W-2-like on this page."])
    monkeypatch.setattr(
        ai_runtime_selection, "select_vision_describer", lambda engine, hh: (stub, "test")
    )

    response = await demo_client.post(
        "/api/v1/income/profile/scan-w2",
        headers={"Authorization": f"Bearer {demo_token}"},
        json={
            "image_base64": _w2_pdf_base64(cover_pages=2),
            "image_media_type": "application/pdf",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["wages_minor"] is None
    assert "first 3 pages" in body["note"]
    assert len(stub.data_urls) == 3


@pytest.mark.anyio
async def test_w2_scan_rejects_an_unreadable_pdf(demo_client, demo_token, monkeypatch) -> None:
    import base64

    from family_cfo_api import ai_runtime_selection

    monkeypatch.setattr(
        ai_runtime_selection,
        "select_vision_describer",
        lambda engine, hh: (_StubDescriber(), "test"),
    )

    response = await demo_client.post(
        "/api/v1/income/profile/scan-w2",
        headers={"Authorization": f"Bearer {demo_token}"},
        json={
            "image_base64": base64.b64encode(b"this is not a pdf").decode("ascii"),
            "image_media_type": "application/pdf",
        },
    )

    assert response.status_code == 422
    assert "could not be read" in response.json()["error"]["message"]
