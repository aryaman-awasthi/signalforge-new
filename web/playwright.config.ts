import { defineConfig } from "@playwright/test";

/**
 * The smallest browser setup that can prove the critical cockpit journey.
 *
 * Two deliberate choices, both about keeping it maintainable rather than
 * impressive:
 *
 * - **The system browser.** `channel: "msedge"` uses the Chromium already on
 *   the machine, so `npm install` does not pull a 150 MB browser bundle and CI
 *   does not need `playwright install`. Override with `SF_E2E_CHANNEL=chrome`
 *   or unset it entirely to use a downloaded Chromium.
 * - **No webServer block.** These tests run against a *live* stack - the API,
 *   the web app and the reference harness - set up by `scripts/drill_run.py`,
 *   because the journey under test is an incident, and an incident needs an
 *   application that is actually breaking. Starting Next.js alone would prove
 *   that the login form renders.
 *
 * Run it:
 *
 *     bash run.sh                                   # api :8000, web :3000, harness :8080
 *     python scripts/drill_run.py drills/s4-acceptance.yaml
 *     cd web && npx playwright test
 *
 * Without a live stack the suite skips, loudly, in the same spirit as the
 * reference-harness tests skipping when the jar is not built.
 */
export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: process.env.SF_WEB_URL ?? "http://127.0.0.1:3000",
    channel: process.env.SF_E2E_CHANNEL ?? "msedge",
    headless: true,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
});
