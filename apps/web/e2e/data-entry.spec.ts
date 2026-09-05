import { expect, test } from '@playwright/test';

// M11 data-entry smoke test. Opt-in like onboarding.spec.ts: it requires a
// running API server seeded with the demo fixtures (see README). It exercises
// the login -> create account -> add transaction -> generate report path.
const DEMO_EMAIL = 'demo@family-cfo.local';
const DEMO_PASSWORD = 'demo-password-123';

async function login(page: import('@playwright/test').Page): Promise<void> {
  await page.goto('/login');
  await page.fill('input[type="email"]', DEMO_EMAIL);
  await page.fill('input[type="password"]', DEMO_PASSWORD);
  await page.click('button[type="submit"]');
  await page.waitForURL('**/overview');
}

test('data entry: create an account, add a transaction, generate a report', async ({ page }) => {
  await login(page);

  // Create an account.
  await page.goto('/accounts');
  await page.locator('.account-form input[formcontrolname="name"]').fill('E2E Brokerage');
  await page.getByRole('combobox', { name: 'Type', exact: true }).click();
  await page.getByRole('option', { name: 'Brokerage', exact: true }).click();
  await page.locator('.account-form input[formcontrolname="openingBalance"]').fill('1000');
  await page.locator('.account-form button[type="submit"]').click();
  await expect(page.getByRole('cell', { name: 'E2E Brokerage', exact: true })).toBeVisible();

  // Add a manual transaction against the seeded checking account.
  await page.goto('/transactions');
  await page.getByRole('combobox', { name: 'Account', exact: true }).click();
  await page.getByRole('option', { name: 'Checking', exact: true }).click();
  await page.locator('.txn-form input[formcontrolname="occurredAt"]').fill('2026-07-01');
  await page.locator('.txn-form input[formcontrolname="amount"]').fill('-42.50');
  await page.locator('.txn-form input[formcontrolname="merchant"]').fill('E2E Grocer');
  await page.locator('.txn-form button[type="submit"]').click();
  await expect(page.locator('.txn-table')).toContainText('E2E Grocer');

  // Generate a weekly report.
  await page.goto('/reports');
  await page.getByRole('button', { name: 'Generate weekly report' }).click();
  await expect(page.locator('.report-card')).toBeVisible();
});
