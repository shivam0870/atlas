import { test, expect, type Page } from "@playwright/test";
test.use({ actionTimeout: 20000 });

async function openLogin(page: Page, query = "") {
  await page.goto(`/login${query}`);
  const tunnel = page.getByRole("button", { name: "Visit Site", exact: true });
  await expect(
    tunnel.or(page.getByRole("heading", { name: "Sign in to Atlas" })),
  ).toBeVisible();
  if (await tunnel.isVisible()) await tunnel.click();
  await expect(
    page.getByRole("heading", { name: "Sign in to Atlas" }),
  ).toBeVisible();
}

test("privacy choices, safe attribution, help search and contact draft", async ({
  page,
  context,
}) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await openLogin(
    page,
    "?utm_source=resume&utm_campaign=atlas&token=do-not-store",
  );
  expect(
    await page.evaluate(() => sessionStorage.getItem("atlas-campaign")),
  ).toBeNull();
  await page.getByRole("button", { name: "Allow attribution" }).click();
  expect(
    await page.evaluate(() =>
      JSON.parse(sessionStorage.getItem("atlas-campaign")!),
    ),
  ).toEqual({ utm_source: "resume", utm_campaign: "atlas" });
  const help = page.getByRole("button", { name: "Help and contact" });
  await help.click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await page.getByLabel("Search help").fill("versions");
  await expect(dialog.locator(".faq-list details")).toHaveCount(0);
  await page.getByLabel("Search help").fill("document changes");
  await expect(dialog.locator(".faq-list details")).toHaveCount(1);
  await page
    .getByText("What happens when a document changes?", { exact: true })
    .click();
  await expect(
    dialog.getByText(/Existing citations retain their original version/),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Copy site link", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Site link copied" }),
  ).toBeVisible();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(
    new URL(page.url()).origin,
  );
  await page
    .getByText("Contact the maintainer / report an issue", { exact: true })
    .click();
  await page.getByRole("button", { name: "Prepare issue" }).click();
  await expect(dialog.getByRole("alert")).toContainText(
    "at least 5 characters",
  );
  await page.getByLabel("Issue subject").fill("Interface browser check");
  await page
    .getByLabel("What happened?")
    .fill("Synthetic verification only. No issue should be submitted.");
  await page.getByRole("button", { name: "Prepare issue" }).click();
  await expect(dialog.getByRole("status")).toContainText(
    "Nothing has been sent",
  );
  const issue = page.getByRole("link", { name: "Review and submit on GitHub" });
  await expect(issue).toHaveAttribute(
    "href",
    /https:\/\/github.com\/shivam0870\/atlas\/issues\/new\?title=Interface/,
  );
  await expect(dialog.locator("time")).toHaveAttribute(
    "datetime",
    "2026-09-24",
  );
  await page.getByRole("button", { name: "Privacy preferences" }).click();
  await page.getByRole("button", { name: "Essential only" }).click();
  expect(
    await page.evaluate(() => sessionStorage.getItem("atlas-campaign")),
  ).toBeNull();
  await page.reload();
  await expect(
    page.getByRole("region", { name: "Cookie and privacy preferences" }),
  ).toHaveCount(0);
  expect(
    await page.evaluate(() => sessionStorage.getItem("atlas-campaign")),
  ).toBeNull();
});

test("keyboard access, password visibility, persistent themes and mobile layout", async ({
  page,
}) => {
  await openLogin(page);
  await page.keyboard.press("Tab");
  await expect(
    page.getByRole("link", { name: "Skip to content" }),
  ).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.locator("#main-content")).toBeFocused();
  await page.getByRole("button", { name: "Essential only" }).click();
  const password = page.getByLabel("Password", { exact: true });
  await password.fill("Synthetic visibility check");
  await page
    .getByRole("button", { name: "Show password", exact: true })
    .click();
  await expect(password).toHaveAttribute("type", "text");
  await expect(page).toHaveURL(/\/login/);
  await page
    .getByRole("button", { name: "Hide password", exact: true })
    .click();
  await expect(password).toHaveAttribute("type", "password");
  await page.getByRole("button", { name: "Switch to dark mode" }).click();
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.getByRole("button", { name: "Switch to light mode" }).click();
  for (const width of [320, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: 844 });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    await expect(
      page.getByRole("button", { name: "Sign in", exact: true }),
    ).toBeVisible();
  }
  const help = page.getByRole("button", { name: "Help and contact" });
  await help.click();
  for (let i = 0; i < 15; i++) {
    await page.keyboard.press("Tab");
    expect(
      await page
        .getByRole("dialog")
        .evaluate((el) => el.contains(document.activeElement)),
    ).toBe(true);
  }
  await page.keyboard.press("Escape");
  await expect(help).toBeFocused();
});

test("print styles protect password fields and respect reduced motion", async ({
  page,
}) => {
  await openLogin(page);
  await page.getByRole("button", { name: "Essential only" }).click();
  await page
    .getByLabel("Password", { exact: true })
    .fill("Do not print this field");
  await page
    .getByRole("button", { name: "Show password", exact: true })
    .click();
  await page.emulateMedia({ media: "print", reducedMotion: "reduce" });
  await expect(page.getByLabel("Password", { exact: true })).toBeHidden();
  await expect(
    page.getByRole("button", { name: "Help and contact" }),
  ).toBeHidden();
  await expect(
    page.getByRole("heading", { name: "Sign in to Atlas" }),
  ).toBeVisible();
  expect(
    await page
      .locator("html")
      .evaluate((el) => getComputedStyle(el).scrollBehavior),
  ).toBe("auto");
});
