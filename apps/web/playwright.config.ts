import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  use: {
    baseURL: process.env['E2E_BASE_URL'] ?? 'http://localhost:4200',
    // LAN deployments serve a self-signed certificate (ADR 0008).
    ignoreHTTPSErrors: true,
  },
});
