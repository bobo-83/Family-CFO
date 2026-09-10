from __future__ import annotations

from copy import deepcopy

import pytest

from family_cfo_api.openapi_compat import (
    SWIFT_GENERATOR_NULLABLE_ALIASES,
    SWIFT_GENERATOR_NULLABLE_COMPONENTS,
    make_swift_generator_compatible_openapi,
)
from family_cfo_api.tools.openapi import (
    SHARED_OPENAPI,
    build_openapi,
    check_implemented_routes,
    load_shared_openapi,
)


def test_implemented_routes_match_shared_openapi_contract() -> None:
    errors = check_implemented_routes(
        generated_spec=build_openapi(),
        shared_spec=load_shared_openapi(),
    )

    assert errors == []


EXPECTED_NULLABLE_FIELD_COUNTS = {
    "Budget": 3,
    "BudgetSummary": 2,
    "CashOutlookResponse": 11,
    "EmergencyFundSummary": 3,
    "HouseholdContext": 14,
    "IncomeAnalysisResponse": 3,
    "IncomeRollup": 2,
    "IncomeSourceAnalysis": 2,
    "MonthlyCashFlow": 3,
    "PaymentTimelineItem": 5,
    "PaymentTimelineResponse": 1,
    "SafeToSpend": 6,
    "SavingsRate": 6,
    "SpendingInsights": 2,
    "SpendingPlanResponse": 4,
    "YearMonthSummary": 2,
    "YearlyOverview": 3,
}
EXPECTED_REQUIRED_NULLABLE_FIELDS = {
    ("Budget", "percent_used"),
    ("Budget", "remaining"),
    ("Budget", "status"),
    ("BudgetSummary", "over_count"),
    ("BudgetSummary", "warning_count"),
    ("CashOutlookResponse", "due_soon_covered"),
    ("CashOutlookResponse", "ending_cash"),
    ("CashOutlookResponse", "expected_income"),
    ("CashOutlookResponse", "lowest_balance"),
    ("HouseholdContext", "emergency_fund_months"),
    ("IncomeAnalysisResponse", "tax"),
    ("IncomeRollup", "transaction_count"),
    ("IncomeSourceAnalysis", "frequency"),
    ("IncomeSourceAnalysis", "typical_amount"),
    ("MonthlyCashFlow", "net"),
    ("PaymentTimelineItem", "amount"),
    ("PaymentTimelineResponse", "covered"),
    ("SafeToSpend", "committed_total"),
    ("SafeToSpend", "safe_to_spend"),
    ("SpendingPlanResponse", "expected_income"),
    ("SpendingPlanResponse", "income_projected"),
    ("SpendingPlanResponse", "left_to_spend"),
    ("SpendingPlanResponse", "per_day"),
    ("YearMonthSummary", "net"),
    ("YearlyOverview", "top_categories"),
    ("YearlyOverview", "total_net"),
}


def _schema_allows_null(schema: dict, components: dict) -> bool:
    schema_type = schema.get("type")
    if isinstance(schema_type, list) and "null" in schema_type:
        return True
    ref = schema.get("$ref")
    if isinstance(ref, str):
        return _schema_allows_null(components[ref.rsplit("/", maxsplit=1)[-1]], components)
    return False


def _item_2_nullable_fields(spec: dict) -> tuple[dict[str, int], set[tuple[str, str]]]:
    components = spec["components"]["schemas"]
    field_counts: dict[str, int] = {}
    required_nullable: set[tuple[str, str]] = set()
    for component_name in SWIFT_GENERATOR_NULLABLE_COMPONENTS:
        component = components[component_name]
        required = set(component.get("required", []))
        nullable_fields = []
        for field_name, schema in component.get("properties", {}).items():
            standalone_null = any(
                isinstance(option, dict) and option.get("type") == "null"
                for option in schema.get("anyOf", [])
            )
            assert not standalone_null, f"{component_name}.{field_name} uses unsupported anyOf null"

            if _schema_allows_null(schema, components):
                ref = schema.get("$ref")
                if isinstance(ref, str):
                    assert ref.rsplit("/", maxsplit=1)[-1] in set(
                        SWIFT_GENERATOR_NULLABLE_ALIASES.values()
                    )
                    assert "type" not in schema
                else:
                    assert schema["type"][1] == "null"
                    if "enum" in schema:
                        assert None in schema["enum"]
                nullable_fields.append(field_name)
                if field_name in required:
                    required_nullable.add((component_name, field_name))
        if nullable_fields:
            field_counts[component_name] = len(nullable_fields)
    return field_counts, required_nullable


def _assert_nullable_aliases_are_exact_copies(spec: dict) -> None:
    components = spec["components"]["schemas"]
    for target_name, alias_name in SWIFT_GENERATOR_NULLABLE_ALIASES.items():
        target = components[target_name]
        alias = deepcopy(components[alias_name])
        alias_type = alias["type"]
        assert alias_type == [target["type"], "null"]
        assert not _schema_allows_null(target, components)
        assert _schema_allows_null(alias, components)
        alias["type"] = target["type"]
        if "title" in target:
            alias["title"] = target["title"]
        else:
            alias.pop("title")
        if alias.get("enum") and alias["enum"][-1] is None:
            alias["enum"].pop()
        assert alias == target


@pytest.mark.parametrize(
    "spec",
    [
        pytest.param(build_openapi(), id="fastapi"),
        pytest.param(load_shared_openapi(), id="authoritative"),
        pytest.param(
            load_shared_openapi(SHARED_OPENAPI.parent / "compatibility" / "0.159.yaml"),
            id="compatibility-0.159",
        ),
    ],
)
def test_item_2_nullable_fields_use_swift_compatible_type_unions(spec: dict) -> None:
    field_counts, required_nullable = _item_2_nullable_fields(spec)

    assert field_counts == EXPECTED_NULLABLE_FIELD_COUNTS
    assert sum(field_counts.values()) == 72
    assert required_nullable == EXPECTED_REQUIRED_NULLABLE_FIELDS
    _assert_nullable_aliases_are_exact_copies(spec)


def test_swift_compatibility_rewrite_is_idempotent() -> None:
    spec = build_openapi()
    before = deepcopy(spec)

    assert make_swift_generator_compatible_openapi(spec) == before


def test_purchase_impact_409_is_concrete_in_openapi() -> None:
    expected = (
        "A monetary dependency required for purchase impact is unreadable "
        "(sealed_amount_unreadable)"
    )
    for spec, path in (
        (build_openapi(), "/api/v1/advisor/purchase"),
        (load_shared_openapi(), "/advisor/purchase"),
    ):
        response = spec["paths"][path]["post"]["responses"]["409"]
        assert response["description"] == expected
        assert response["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/ErrorResponse"
        }


def test_strict_aggregate_operations_document_concrete_409_responses() -> None:
    operations = (
        (
            "/api/v1/overview/yearly/review",
            "/overview/yearly/review",
            "post",
            (
                "A monetary dependency required for yearly review generation is "
                "unreadable (sealed_amount_unreadable)"
            ),
        ),
        (
            "/api/v1/accounts/card-statements",
            "/accounts/card-statements",
            "post",
            (
                "The existing statement cycle has an unreadable amount "
                "(sealed_amount_unreadable)"
            ),
        ),
        (
            "/api/v1/bills/suggestions",
            "/bills/suggestions",
            "get",
            (
                "A transaction amount required for bill suggestion detection is unreadable "
                "(sealed_amount_unreadable)"
            ),
        ),
        (
            "/api/v1/reports/generate",
            "/reports/generate",
            "post",
            (
                "A transaction amount required for report generation is unreadable "
                "(sealed_amount_unreadable)"
            ),
        ),
    )
    specs = (
        (build_openapi(), True),
        (load_shared_openapi(), False),
        (
            load_shared_openapi(SHARED_OPENAPI.parent / "compatibility" / "0.159.yaml"),
            False,
        ),
    )

    for spec, generated in specs:
        for generated_path, shared_path, method, description in operations:
            response = spec["paths"][generated_path if generated else shared_path][method][
                "responses"
            ]["409"]
            assert response["description"] == description
            assert response["content"]["application/json"]["schema"] == {
                "$ref": "#/components/schemas/ErrorResponse"
            }


def _errors_after_shared_mutation(mutate) -> list[str]:
    generated = build_openapi()
    shared = deepcopy(load_shared_openapi())
    mutate(shared["components"]["schemas"])
    return check_implemented_routes(generated_spec=generated, shared_spec=shared)


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (
            lambda schemas: schemas["CategorySpend"]["properties"]["amount"].update(
                {"$ref": "#/components/schemas/Money"}
            ),
            "expected schema Money, generated QualifiedMoney",
        ),
        (
            lambda schemas: schemas["YearMonthSummary"]["properties"].update(
                {"month": {"type": ["string", "null"]}}
            ),
            "generated schema is missing contract nullability",
        ),
        (
            lambda schemas: schemas["YearMonthSummary"]["properties"].__setitem__(
                "net", {"$ref": "#/components/schemas/QualifiedMoney"}
            ),
            "generated schema adds nullability absent from the contract",
        ),
        (
            lambda schemas: schemas["SpendingByCategory"]["properties"]["categories"][
                "items"
            ].update({"$ref": "#/components/schemas/NamedAmount"}),
            "expected schema NamedAmount, generated CategorySpend",
        ),
        (
            lambda schemas: schemas["PaymentTimelineItem"]["properties"]["status"].update(
                {"enum": ["overdue", "due_soon", "upcoming", "paid", "no_date"]}
            ),
            "expected enum",
        ),
    ],
)
def test_recursive_response_parity_rejects_nested_drift(mutate, expected: str) -> None:
    errors = _errors_after_shared_mutation(mutate)
    assert any(expected in error for error in errors), errors
