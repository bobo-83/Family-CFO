import { expect, test } from '@playwright/test';

// Demo credentials come from apps/api's family_cfo_api.fixtures.seed_demo_household —
// this test requires a running API server seeded with those fixtures (see README).
const DEMO_EMAIL = 'demo@family-cfo.local';
const DEMO_PASSWORD = 'demo-password-123';

test('health endpoint is reachable through the dev server proxy', async ({ request }) => {
  const response = await request.get('/api/v1/health');
  expect(response.ok()).toBe(true);
  const body = await response.json();
  expect(body.status).toBe('ok');
});

test('onboarding: login redirects to overview and renders household data', async ({ page }) => {
  await page.goto('/login');
  await expect(page.locator('h1')).toHaveText('Family CFO');

  await page.fill('input[type="email"]', DEMO_EMAIL);
  await page.fill('input[type="password"]', DEMO_PASSWORD);
  await page.click('button[type="submit"]');

  await page.waitForURL('**/overview');
  await expect(page.locator('h1')).toHaveText('Overview');
  await expect(page.locator('.overview__card')).toHaveCount(3);
});

test('an invalid login shows an error and does not navigate away', async ({ page }) => {
  await page.goto('/login');
  await page.fill('input[type="email"]', DEMO_EMAIL);
  await page.fill('input[type="password"]', 'wrong-password');
  await page.click('button[type="submit"]');

  await expect(page.locator('.login__error--banner')).toBeVisible();
  await expect(page).toHaveURL(/\/login$/);
});
