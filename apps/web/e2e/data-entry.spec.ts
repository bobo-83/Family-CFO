import { expect, test } from '@playwright/test';

import { login, selectMatOption } from './support';

// M11 data-entry smoke test. Opt-in like onboarding.spec.ts: it requires a
// running API server seeded with the demo fixtures (see README). It exercises
// the login -> create account -> add transaction -> generate report path.

test('data entry: create an account, add a transaction, generate a report', async ({ page }) => {
  await login(page);

  // Create an account.
  await page.goto('/accounts');
  await page.locator('.account-form input[formcontrolname="name"]').fill('E2E Brokerage');
  await selectMatOption(page, 'account-type-select', { testId: 'account-type-option-brokerage' });
  await page.locator('.account-form input[formcontrolname="openingBalance"]').fill('1000');
  await page.locator('.account-form button[type="submit"]').click();
  await expect(page.getByRole('cell', { name: 'E2E Brokerage', exact: true })).toBeVisible();

  // Add a manual transaction against the seeded checking account. The option
  // is addressed by name because an account name is seeded data, not copy.
  await page.goto('/transactions');
  await selectMatOption(page, 'txn-account-select', { name: 'Checking' });
  await page.locator('.txn-form input[formcontrolname="occurredAt"]').fill('2026-07-01');
  await page.locator('.txn-form input[formcontrolname="amount"]').fill('-42.50');
  await page.locator('.txn-form input[formcontrolname="merchant"]').fill('E2E Grocer');
  await page.locator('.txn-form button[type="submit"]').click();
  await expect(page.locator('.txn-table')).toContainText('E2E Grocer');

  // Generate a weekly report.
  await page.goto('/reports');
  await page.getByTestId('generate-weekly-report').click();
  await expect(page.locator('.report-card')).toBeVisible();
});
