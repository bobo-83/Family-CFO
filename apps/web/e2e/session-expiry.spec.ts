import { expect, test, type Page } from '@playwright/test';

const now = new Date('2030-01-01T12:00:00Z');
const expiryMessage = 'Your session has expired. Please sign in again.';
const member = { household_id: 'synthetic-household', user_id: 'synthetic-user', role: 'owner' };

async function prepare(page: Page, firstLifetime = 60_000): Promise<void> {
  await page.clock.install({ time: now });
  let successfulLogins = 0;
  await page.route('**/api/v1/**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/v1/auth/sessions') {
      if (route.request().postDataJSON().password === 'wrong-password') {
        await route.fulfill({ status: 401, json: { error: { message: 'Invalid email or password' } } });
        return;
      }
      successfulLogins++;
      await route.fulfill({
        status: 201,
        json: {
          ...member,
          access_token: `synthetic-bearer-${successfulLogins}`,
          expires_at: new Date(+now + (successfulLogins === 1 ? firstLifetime : 120_000)).toISOString(),
        },
      });
      return;
    }
    if (path === '/api/v1/auth/session') {
      expect(route.request().headers()['authorization']).toMatch(/^Bearer synthetic-bearer-/);
      await route.fulfill({ json: { ...member, rights: [] } });
      return;
    }
    await route.fulfill({ status: 503, json: { error: { message: 'Synthetic service unavailable' } } });
  });
}

async function signIn(page: Page, password = 'synthetic-password'): Promise<void> {
  await page.locator('input[type="email"]').fill('user@example.test');
  await page.locator('input[type="password"]').fill(password);
  await page.getByRole('button', { name: 'Sign in' }).click();
}

test('idle expiry evicts Overview, retains its reason and returns to Overview after sign-in', async ({ page }) => {
  await prepare(page, 20_000);
  await page.goto('/login');
  await signIn(page);
  await expect(page.getByTestId('overview-title')).toBeVisible();

  await page.clock.fastForward(21_000);
  await expect(page).toHaveURL(/\/login$/);
  await expect(page.getByTestId('overview-title')).toHaveCount(0);
  await expect(page.getByText(expiryMessage)).toBeVisible();

  await signIn(page, 'wrong-password');
  await expect(page.getByText('Invalid email or password')).toBeVisible();
  await expect(page.getByText(expiryMessage)).toBeVisible();

  await signIn(page);
  await expect(page.getByTestId('overview-title')).toBeVisible();
  await expect(page.getByText(expiryMessage)).toHaveCount(0);
});

test('a network failure keeps Overview mounted, and voluntary sign-out has no expiry notice', async ({ page }) => {
  await prepare(page);
  const household = /\/api\/v1\/household(?:\?.*)?$/;
  await page.route(household, (route) => route.abort('internetdisconnected'));
  await page.goto('/login');
  const failure = page.waitForEvent('requestfailed', (request) => household.test(request.url()));
  await signIn(page);
  await failure;
  await expect(page.getByTestId('overview-title')).toBeVisible();

  await page.getByRole('button', { name: 'Log out' }).click();
  await expect(page).toHaveURL(/\/login$/);
  await expect(page.getByTestId('overview-title')).toHaveCount(0);
  await expect(page.getByText(expiryMessage)).toHaveCount(0);
  await page.goto('/accounts');
  await expect(page).toHaveURL(/\/login$/);
});

test('an owned API rejection evicts the mounted page with generic sign-in copy', async ({ page }) => {
  await prepare(page);
  await page.route('**/api/v1/accounts', (route) =>
    route.fulfill({ status: 401, json: { error: { message: 'Invalid or expired session' } } }),
  );
  await page.goto('/login');
  await signIn(page);
  await expect(page.getByTestId('overview-title')).toBeVisible();

  await page.getByRole('link', { name: 'Accounts', exact: true }).click();
  await expect(page).toHaveURL(/\/login$/);
  await expect(page.getByText('Please sign in again to continue.')).toBeVisible();
  await expect(page.getByText(expiryMessage)).toHaveCount(0);
  await expect(page.locator('.shell')).toHaveCount(0);
  await page.goBack();
  await expect(page.locator('.shell')).toHaveCount(0);
});

test('startup expiry redirects a protected deep link before mounting the shell', async ({ page }) => {
  await prepare(page);
  await page.addInitScript((expiry) => {
    const owner = '00000000000000000000000000000164';
    localStorage.setItem('family-cfo-auth-owner', owner);
    localStorage.setItem('family-cfo-auth-session:' + owner, JSON.stringify({
      accessToken: 'synthetic-expired-bearer',
      expiresAt: expiry,
      householdId: 'synthetic-household',
      userId: 'synthetic-user',
      role: 'owner',
    }));
  }, new Date(+now - 1000).toISOString());
  await page.goto('/accounts');

  await expect(page).toHaveURL(/\/login$/);
  await expect(page.locator('.shell')).toHaveCount(0);
  await expect(page.getByText(expiryMessage)).toBeVisible();
});

test('returning to the foreground checks expiry before a background timer runs', async ({ page }) => {
  await prepare(page);
  await page.goto('/login');
  await signIn(page);
  await expect(page.getByTestId('overview-title')).toBeVisible();

  await page.clock.setSystemTime(new Date(+now + 61_000));
  await page.evaluate(() => window.dispatchEvent(new Event('focus')));

  await expect(page).toHaveURL(/\/login$/);
  await expect(page.getByText(expiryMessage)).toBeVisible();
  await expect(page.getByTestId('overview-title')).toHaveCount(0);
});
