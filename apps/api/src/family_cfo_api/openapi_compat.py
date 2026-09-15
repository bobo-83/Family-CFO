from __future__ import annotations

from copy import deepcopy
from typing import Any

# Item 2 rewrites these response families as one coordinated 0.159 contract.
# Pydantic expresses their nullable properties as `anyOf: [T, {type: null}]`,
# but swift-openapi-generator 1.10.3 rejects the standalone null schema and
# drops the property. OpenAPI 3.1 type unions preserve null while keeping the
# parent object's `required` list authoritative for presence versus omission.
SWIFT_GENERATOR_NULLABLE_COMPONENTS = frozenset(
    {
        "BackupCapacityObservation",
        "BackupConfig",
        "BackupConfigUpdateRequest",
        "BackupDestinationCheckRequest",
        "BackupDestinationCheckResponse",
        "BackupDestinationRecoveryStatus",
        "BackupJob",
        "BackupRecoveryStatus",
        "BackupRetentionPolicy",
        "BackupRetentionPolicyUpdate",
        "Budget",
        "BudgetListResponse",
        "BudgetSummary",
        "CashOutlookResponse",
        "CategorySpend",
        "ComputationAvailability",
        "EmergencyFundSummary",
        "HouseholdContext",
        "IncomeAnalysisResponse",
        "IncomeRollup",
        "IncomeSourceAnalysis",
        "MonthlyCashFlow",
        "PaymentTimelineItem",
        "PaymentTimelineResponse",
        "QualifiedMoney",
        "RemoteBackup",
        "RemoteBackupListResponse",
        "SafeToSpend",
        "SavingsContributionSet",
        "SavingsRate",
        "SpendingByCategory",
        "SpendingInsights",
        "SpendingPlanResponse",
        "YearMonthSummary",
        "YearlyOverview",
    }
)

# JSON Schema 2020-12 treats `$ref` siblings as an intersection, so adding
# `type: [object, null]` beside an object-only reference does not actually allow
# null. These nullable copies are real union schemas. The Swift generator config
# type-aliases them back to the corresponding existing generated Swift types.
SWIFT_GENERATOR_NULLABLE_ALIASES = {
    "BackupJob": "NullableBackupJob",
    "BudgetSummary": "NullableBudgetSummary",
    "EmergencyFundSummary": "NullableEmergencyFundSummary",
    "GoalProgress": "NullableGoalProgress",
    "IncomeProfile": "NullableIncomeProfile",
    "Money": "NullableMoney",
    "MonthlyCashFlow": "NullableMonthlyCashFlow",
    "QualifiedMoney": "NullableQualifiedMoney",
    "ReadyToSellHoldings": "NullableReadyToSellHoldings",
    "SafeToSpend": "NullableSafeToSpend",
    "SavingsRate": "NullableSavingsRate",
    "SpendingByCategory": "NullableSpendingByCategory",
    "SpendingInsights": "NullableSpendingInsights",
    "TaxEstimate": "NullableTaxEstimate",
    "TimelinePaidWith": "NullableTimelinePaidWith",
    "YearlyReview": "NullableYearlyReview",
}


def _nullable_type(schema: dict[str, Any]) -> None:
    concrete_type = schema.get("type")
    if not isinstance(concrete_type, str):
        raise TypeError("nullable schema has no concrete type")
    schema["type"] = [concrete_type, "null"]
    enum = schema.get("enum")
    if isinstance(enum, list) and None not in enum:
        enum.append(None)


def _rewrite_nullable_schema(schema: dict[str, Any]) -> None:
    options = schema.get("anyOf")
    if not isinstance(options, list):
        return

    null_options = [
        option for option in options if isinstance(option, dict) and option.get("type") == "null"
    ]
    non_null_options = [option for option in options if option not in null_options]
    if len(null_options) != 1 or len(non_null_options) != 1:
        return

    non_null = non_null_options[0]
    if not isinstance(non_null, dict):
        return

    rewritten = {key: value for key, value in schema.items() if key != "anyOf"}
    ref = non_null.get("$ref")
    if isinstance(ref, str):
        target_name = ref.rsplit("/", maxsplit=1)[-1]
        alias = SWIFT_GENERATOR_NULLABLE_ALIASES.get(target_name)
        if alias is None:
            raise TypeError(f"nullable reference has no Swift-compatible alias: {target_name}")
        rewritten["$ref"] = f"#/components/schemas/{alias}"
    else:
        rewritten.update(non_null)
        _nullable_type(rewritten)

    schema.clear()
    schema.update(rewritten)


def make_swift_generator_compatible_openapi(spec: dict[str, Any]) -> dict[str, Any]:
    """Rewrite Item 2 nullable responses without changing their wire semantics.

    Presence remains defined solely by each object's `required` array. A
    required nullable property must still be sent and may contain JSON null; an
    optional nullable property may be omitted or contain JSON null.
    """

    components = spec.get("components", {}).get("schemas", {})
    for component_name in SWIFT_GENERATOR_NULLABLE_COMPONENTS:
        component = components.get(component_name)
        if not isinstance(component, dict):
            continue
        properties = component.get("properties", {})
        if not isinstance(properties, dict):
            continue
        for property_schema in properties.values():
            if isinstance(property_schema, dict):
                _rewrite_nullable_schema(property_schema)

    # Build aliases only after their source components have been normalized so
    # nullable fields nested inside an aliased response remain generator-safe.
    for target_name, alias_name in SWIFT_GENERATOR_NULLABLE_ALIASES.items():
        target = components.get(target_name)
        if not isinstance(target, dict):
            raise TypeError(f"nullable alias target is missing: {target_name}")
        alias = deepcopy(target)
        _nullable_type(alias)
        alias["title"] = alias_name
        components[alias_name] = alias
    return spec
