import { defineConfig, devices } from "@playwright/test";

const baseURL = process.env.E2E_BASE_URL ?? "http://localhost:3000";

/**
 * End-to-end tests run against the real stack, not a mocked API: upload ->
 * processing -> correct a field -> download CSV -> assert the CSV's contents.
 * Bring it up with `docker compose up` before running these.
 */
export default defineConfig({
  testDir: "./tests/e2e",
  // Uploading and OCR-ing a fixture batch is genuinely slow; a short timeout here
  // would fail honest tests.
  timeout: 180_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: process.env.CI ? [["github"], ["html", { open: "never" }]] : [["list"]],
  use: {
    baseURL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
    acceptDownloads: true,
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
  ],
});
