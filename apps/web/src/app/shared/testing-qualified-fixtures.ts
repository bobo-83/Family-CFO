import type { ComputationAvailability, Money, QualifiedMoney } from '../api-client';

export const completeAvailability: ComputationAvailability = {
  status: 'complete',
  incomplete_count: 0,
};

export function qualified(value: Money, incompleteCount = 0): QualifiedMoney {
  return { value, incomplete_count: incompleteCount };
}

function asQualified(value: unknown): unknown {
  if (!value || typeof value !== 'object') {
    return value;
  }
  const record = value as Record<string, unknown>;
  return 'value' in record && 'incomplete_count' in record
    ? value
    : qualified(value as Money);
}

function qualifySavingsRate(value: Record<string, unknown>): void {
  for (const key of [
    'monthly_income',
    'average_monthly_spending',
    'gross_income',
    'transfers',
    'residual',
    'total_saved',
  ]) {
    if (value[key] != null) {
      value[key] = asQualified(value[key]);
    }
  }
  value['transfer_detection'] ??= completeAvailability;
}

function qualifySafeToSpend(value: Record<string, unknown>): void {
  for (const key of [
    'credit_card_payments',
    'subscription_forecast',
    'committed_total',
    'safe_to_spend',
    'committed_savings',
  ]) {
    if (value[key] != null) {
      value[key] = asQualified(value[key]);
    }
  }
  value['subscription_detection'] ??= completeAvailability;
  value['savings_detection'] ??= completeAvailability;
}

function qualifyHousehold(value: Record<string, unknown>): void {
  value['net_worth'] = asQualified(value['net_worth']);
  const cashFlow = value['monthly_cash_flow'] as Record<string, unknown> | undefined;
  if (cashFlow) {
    for (const key of ['income', 'spending', 'net', 'taxes']) {
      if (cashFlow[key] != null) {
        cashFlow[key] = asQualified(cashFlow[key]);
      }
    }
  }
  const emergency = value['emergency_fund'] as Record<string, unknown> | undefined;
  if (emergency) {
    emergency['monthly_expenses'] = asQualified(emergency['monthly_expenses']);
    if (emergency['gap_to_recommended'] != null) {
      emergency['gap_to_recommended'] = asQualified(emergency['gap_to_recommended']);
    }
  }
  const spending = value['spending_insights'] as Record<string, unknown> | undefined;
  if (spending) {
    spending['this_month'] = asQualified(spending['this_month']);
    spending['last_month'] = asQualified(spending['last_month']);
  }
  const savings = value['savings_rate'] as Record<string, unknown> | undefined;
  if (savings) {
    qualifySavingsRate(savings);
  }
  const contributions = value['savings_contributions'];
  value['savings_contributions'] = Array.isArray(contributions)
    ? { contributions, detection: completeAvailability }
    : contributions ?? { contributions: [], detection: completeAvailability };
  const budgets = value['budget_summary'] as Record<string, unknown> | undefined;
  if (budgets) {
    budgets['total_spent'] = asQualified(budgets['total_spent']);
  }
  const safe = value['safe_to_spend'] as Record<string, unknown> | undefined;
  if (safe) {
    qualifySafeToSpend(safe);
  }
  const category = value['spending_by_category'] as Record<string, unknown> | undefined;
  if (category) {
    for (const key of ['categorized_total', 'uncategorized', 'total']) {
      category[key] = asQualified(category[key]);
    }
    for (const row of (category['categories'] as Array<Record<string, unknown>> | undefined) ?? []) {
      row['amount'] = asQualified(row['amount']);
    }
  }
}

function qualifyOutlook(value: Record<string, unknown>): void {
  for (const key of ['ending_cash', 'lowest_balance', 'expected_income', 'obligations', 'due_soon', 'shortfall']) {
    if (value[key] != null) {
      value[key] = asQualified(value[key]);
    }
  }
  value['income_projection'] ??= completeAvailability;
}

function qualifyPlan(value: Record<string, unknown>): void {
  for (const key of [
    'income_received',
    'income_projected',
    'expected_income',
    'spent',
    'bills_remaining',
    'left_to_spend',
    'per_day',
  ]) {
    if (value[key] != null) {
      value[key] = asQualified(value[key]);
    }
  }
  value['income_projection'] ??= completeAvailability;
}

function qualifyYear(value: Record<string, unknown>): void {
  for (const key of ['total_income', 'total_spending', 'total_net']) {
    if (value[key] != null) {
      value[key] = asQualified(value[key]);
    }
  }
  for (const month of (value['months'] as Array<Record<string, unknown>> | undefined) ?? []) {
    for (const key of ['income', 'spending', 'net', 'net_worth_eom']) {
      if (month[key] != null) {
        month[key] = asQualified(month[key]);
      }
    }
  }
}

function qualifyTimeline(value: Record<string, unknown>): void {
  value['due_total'] = asQualified(value['due_total']);
}

function qualifyIncome(value: Record<string, unknown>): void {
  const rollup = value['rollup'] as Record<string, unknown>;
  rollup['annual_income'] = asQualified(rollup['annual_income']);
  rollup['monthly_average'] = asQualified(rollup['monthly_average']);
  for (const source of (value['sources'] as Array<Record<string, unknown>> | undefined) ?? []) {
    source['total_amount'] = asQualified(source['total_amount']);
    if (source['typical_amount'] != null) {
      source['typical_amount'] = asQualified(source['typical_amount']);
    }
  }
  value['detection'] ??= completeAvailability;
}

/** Upgrade pre-0.159 page fixtures while each test is migrated to generated DTO shapes. */
export function qualifyApiFixture<T>(data: T): T {
  if (!data || typeof data !== 'object') {
    return data;
  }
  const value = data as Record<string, unknown>;
  if ('display_name' in value && 'net_worth' in value) qualifyHousehold(value);
  if ('starting_cash' in value && 'horizon_days' in value) qualifyOutlook(value);
  if ('days_remaining' in value && 'income_received' in value) qualifyPlan(value);
  if ('year' in value && 'months' in value && 'total_income' in value) qualifyYear(value);
  if ('items' in value && 'due_total' in value && 'liquid_balance' in value) qualifyTimeline(value);
  if ('rollup' in value && 'sources' in value && 'tax' in value) qualifyIncome(value);
  return data;
}
