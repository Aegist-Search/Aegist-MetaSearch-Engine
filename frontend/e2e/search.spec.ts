import { expect, test } from "@playwright/test";

/**
 * These specs run against the real stack, with the Katana fixture indexed by
 * `global-setup.ts`. Filtering on `domain=e2e.example` keeps the counts stable
 * even when the developer's index has other pages.
 */
const QUERY = "aegisfixture";
const SEARCH_PATH = `/search?q=${QUERY}&engine=local&domain=e2e.example`;

test("local search returns the fixture pages", async ({ page }) => {
  await page.goto(SEARCH_PATH);

  const cards = page.locator("main ol > li");
  await expect(cards).toHaveCount(10);

  const firstLink = cards.first().locator("a[data-result-link]");
  await expect(firstLink).toBeVisible();
  await expect(firstLink).toContainText(/aegisfixture/i);

  // The query is highlighted in the title by React, not injected as HTML.
  await expect(cards.first().locator("mark").first()).toBeVisible();
  await expect(cards.first().locator("a[data-result-link]")).toHaveAttribute(
    "href",
    /^https:\/\/e2e\.example\/aegisfixture\//,
  );
});

test("pagination keeps the query and the domain filter", async ({ page }) => {
  await page.goto(SEARCH_PATH);
  await expect(page.locator("main ol > li")).toHaveCount(10);

  const next = page.locator('nav a[rel="next"]');
  await expect(next).toBeVisible();
  await next.click();

  await expect(page).toHaveURL(/[?&]page=2(&|$)/);
  await expect(page).toHaveURL(/[?&]domain=e2e\.example(&|$)/);
  await expect(page.locator("main ol > li")).toHaveCount(4);
  await expect(page.locator('nav a[rel="prev"]')).toBeVisible();
});

test("the preview panel opens from a result and closes with Escape", async ({ page }) => {
  await page.goto(SEARCH_PATH);

  const card = page.locator("main ol > li").first();
  await expect(card).toBeVisible();
  await expect(page.locator("dialog[open]")).toHaveCount(0);

  // The button needs hydration: retry the click until the dialog opens instead
  // of sleeping for an arbitrary amount of time.
  await expect(async () => {
    if ((await page.locator("dialog[open]").count()) === 0) {
      await card.locator("button").first().click();
    }
    await expect(page.locator("dialog[open]")).toBeVisible({ timeout: 1_000 });
  }).toPass({ timeout: 15_000 });

  const dialog = page.locator("dialog[open]");
  await expect(dialog).toContainText("e2e.example");
  await expect(dialog.getByRole("link")).toBeVisible();

  await page.keyboard.press("Escape");
  await expect(page.locator("dialog[open]")).toHaveCount(0);
});
