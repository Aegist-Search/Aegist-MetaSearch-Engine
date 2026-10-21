import { defineConfig, devices } from "@playwright/test";

/**
 * End-to-end suite.
 *
 * The specs run against a real stack: `e2e/global-setup.ts` brings up the
 * Compose services and indexes the Katana fixture, then Playwright boots the
 * Next.js frontend. Set E2E_SKIP_STACK=1 to reuse a stack you started yourself,
 * and E2E_API_URL / E2E_PORT to point the suite somewhere else.
 */
const port = Number(process.env.E2E_PORT ?? 3100);
const baseURL = process.env.E2E_BASE_URL ?? `http://127.0.0.1:${port}`;
const apiURL = process.env.SEARCH_API_URL ?? "http://127.0.0.1:8000";
// `next dev` only serves its dev assets (chunks, the HMR socket) to `localhost`
// and to the hostname it was started with. Started with the default `0.0.0.0`,
// a browser on 127.0.0.1 gets a 403 for those requests: the server-rendered
// HTML still arrives, but the client bundle never runs and the page is inert.
// Bind the dev server to the host the specs actually visit.
const baseHostname = new URL(baseURL).hostname.replace(/^\[(.*)\]$/, "$1");

export default defineConfig({
  testDir: "./e2e",
  globalSetup: "./e2e/global-setup.ts",
  // One worker: the specs share the local index and the dev server.
  fullyParallel: false,
  workers: 1,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? "line" : "list",
  timeout: 60_000,
  expect: { timeout: 15_000 },
  use: {
    baseURL,
    trace: "on-first-retry",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  // A remote E2E_BASE_URL means the frontend is already served elsewhere.
  webServer: process.env.E2E_BASE_URL
    ? undefined
    : {
        command: `npm run dev -- --port ${port} --hostname ${baseHostname}`,
        url: baseURL,
        timeout: 180_000,
        reuseExistingServer: !process.env.CI,
        env: {
          // Server-side only: the browser keeps talking to Next.js.
          ...(process.env as Record<string, string>),
          SEARCH_API_URL: apiURL,
        },
      },
});
