import { expect, type Page } from '@playwright/test';

// Shared plumbing for the browser specs (issue #148 follow-up). Two rules
// live here so every spec inherits them instead of re-deriving them:
//
//  1. Resolve a Material option inside the open overlay, never page-wide.
//     Material renders select options in a page-level CDK overlay, and the
//     accounts/transactions tables render a native <select> editor per row
//     whose <option> children sit in the accessibility tree whether or not
//     that select is open. A page-wide getByRole('option', …) therefore
//     matches once per seeded row and trips Playwright strict mode — which
//     is exactly how the first CI run of this suite failed.
//  2. Locators must not depend on translated copy. The app builds en, lt and
//     vi catalogs from one source, so a label like 'Type' is a translator's
//     string, not a stable handle. Controls the specs drive carry a
//     data-testid; values that come from seeded data (an account's name) stay
//     addressable by their visible text, because that text is data.

export const DEMO_EMAIL = 'demo@family-cfo.local';
export const DEMO_PASSWORD = 'demo-password-123';

export async function login(page: Page): Promise<void> {
  await page.goto('/login');
  await page.fill('input[type="email"]', DEMO_EMAIL);
  await page.fill('input[type="password"]', DEMO_PASSWORD);
  await page.click('button[type="submit"]');
  await page.waitForURL('**/overview');
}

/**
 * Open the mat-select carrying `testId` and choose one of its options.
 *
 * Identify the option by `testId` when it is a fixed part of the UI, or by
 * `name` when it is seeded data (an account name is not translated).
 */
export async function selectMatOption(
  page: Page,
  testId: string,
  option: { testId: string } | { name: string },
): Promise<void> {
  await page.getByTestId(testId).click();

  // Material keeps at most one select panel open, so this is unambiguous —
  // and unlike getByRole('listbox', { name: … }) it does not read a
  // translated form-field label.
  const panel = page.locator('.mat-mdc-select-panel');
  await expect(panel).toBeVisible();

  const choice =
    'testId' in option
      ? panel.getByTestId(option.testId)
      : panel.getByRole('option', { name: option.name, exact: true });
  await choice.click();

  // The overlay animates out. Waiting for it keeps the next click from
  // landing on a panel that is still on its way off screen.
  await expect(panel).toBeHidden();
}
