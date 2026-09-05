import { expect, test, type Page } from '@playwright/test';

import { login } from './support';

// M68 chat + vision smoke tests. Opt-in like the other e2e specs: they need a
// running stack seeded with the demo fixtures (E2E_BASE_URL). They are
// runtime-tolerant by design: against a deterministic-only stack they assert
// the deterministic caption; against a full AI stack they assert model
// attribution — either way the HONEST outcome must render.

// A live 80B answer takes ~30s; the photo path adds a describe round.
const ANSWER_TIMEOUT_MS = 120_000;

async function sendMessage(page: Page, message: string): Promise<void> {
  await page.locator('.chat__input input[formcontrolname="message"]').fill(message);
  await page.locator('.chat__input button[type="submit"]').click();
}

function lastSource(page: Page) {
  return page.locator('.chat__source').last();
}

test('chat: a question renders a grounded, attributed answer', async ({ page }) => {
  test.setTimeout(ANSWER_TIMEOUT_MS + 60_000);
  await login(page);
  await page.goto('/chat');

  await sendMessage(page, 'How is our household doing overall right now?');

  // The user's bubble appears immediately; the answer arrives when the model
  // (or the deterministic engine) is done.
  await expect(page.locator('.chat__bubble').first()).toBeVisible();
  await expect(lastSource(page)).toBeVisible({ timeout: ANSWER_TIMEOUT_MS });

  // Attribution is asserted through the data attribute rather than the
  // sentence, which is translated; the attribute mirrors the same condition.
  await expect(lastSource(page)).toHaveAttribute('data-answer-source', /^(model|deterministic)$/);
  await expect(page.locator('.chat__confidence').last()).toHaveClass(
    /chat__confidence--(high|medium|low)/,
  );
});

test('chat: an attached photo flows through the vision path', async ({ page }) => {
  test.setTimeout(ANSWER_TIMEOUT_MS + 60_000);

  // Generate the receipt image in-browser: render text, screenshot it. No
  // binary fixtures in the repo.
  await page.goto(
    'data:text/html,<body style="width:400px;font:24px monospace;background:white">' +
      '<h2>SUNNY MART</h2><p>Milk $4.29</p><p>Bread $3.50</p><p>TOTAL $7.79</p></body>',
  );
  const receipt = await page.screenshot();

  await login(page);
  await page.goto('/chat');
  await page.locator('input.chat__file[accept*="image"]:not([disabled])').setInputFiles({
    name: 'receipt.png',
    mimeType: 'image/png',
    buffer: receipt,
  });
  await sendMessage(page, 'What is the total on this receipt?');

  // The stored user turn carries the photo marker.
  await expect(page.locator('.chat__photo-marker').first()).toBeVisible({ timeout: 30_000 });

  // An answer renders, and the attribution is honest either way: a describer
  // reports which model read the photo; without one, the answer carries the
  // not-analyzed warning (rendered inside the recommendation details/warnings).
  await expect(lastSource(page)).toBeVisible({ timeout: ANSWER_TIMEOUT_MS });
  const photoRead = (await lastSource(page).getAttribute('data-photo-read')) === 'true';
  if (!photoRead) {
    // This warning is API-authored text rendered as data, not a web catalog
    // string, so matching on it does not depend on the build's locale.
    const warning = page
      .locator('.chat__bubble')
      .last()
      .locator('.chat__details li')
      .filter({ hasText: /could not be analyzed/i });
    await expect(warning).toBeAttached();
  }
});
