import { expect, test, type Page } from '@playwright/test';

// M68 chat + vision smoke tests. Opt-in like the other e2e specs: they need a
// running stack seeded with the demo fixtures (E2E_BASE_URL). They are
// runtime-tolerant by design: against a deterministic-only stack they assert
// the deterministic caption; against a full AI stack they assert model
// attribution — either way the HONEST outcome must render.
const DEMO_EMAIL = 'demo@family-cfo.local';
const DEMO_PASSWORD = 'demo-password-123';

// A live 80B answer takes ~30s; the photo path adds a describe round.
const ANSWER_TIMEOUT_MS = 120_000;

async function login(page: Page): Promise<void> {
  await page.goto('/login');
  await page.fill('input[type="email"]', DEMO_EMAIL);
  await page.fill('input[type="password"]', DEMO_PASSWORD);
  await page.click('button[type="submit"]');
  await page.waitForURL('**/overview');
}

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
  await expect(lastSource(page)).toContainText(/Answered by|Deterministic calculation/);
  await expect(page.locator('.chat__confidence').last()).toContainText('Confidence:');
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
  await page.locator('input.chat__file').setInputFiles({
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
  const source = await lastSource(page).textContent();
  const bubble = await page.locator('.chat__bubble').last().textContent();
  const photoRead = /photo read by/.test(source ?? '');
  const notAnalyzed = /not.{0,20}analyz/i.test(bubble ?? '');
  const deterministic = /Deterministic calculation/.test(source ?? '');
  expect(photoRead || notAnalyzed || deterministic).toBe(true);
});
