/** Real Chrome checks. No API mocks, fixture writes, email or GitHub submissions.
 * node scripts/verify_ui.cjs --showcase http://127.0.0.1:8130 \
 *   --account-file .local/public-demo/journey-account.json \
 *   --journey-file .local/public-demo/journey.json --output .local/ui-review
 * Omit the two fixture files to check only the showcase.
 */
const fs = require("node:fs");
const path = require("node:path");
const { parseArgs } = require("node:util");
const { chromium, expect } = require("../web/node_modules/@playwright/test");
const { values } = parseArgs({
  options: {
    showcase: { type: "string", default: "http://127.0.0.1:8130" },
    "account-file": { type: "string" },
    "journey-file": { type: "string" },
    output: { type: "string", default: ".local/ui-review" },
  },
});
const out = values.output;
fs.mkdirSync(path.join(out, "screenshots"), { recursive: true });
const checks = [],
  errors = [];
function passed(label) {
  checks.push(label);
  console.log("PASS " + label);
}
async function screenshot(page, name) {
  await page.screenshot({
    path: path.join(out, "screenshots", name + ".png"),
    fullPage: true,
    animations: "disabled",
  });
}
async function fits(page) {
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
}
(async () => {
  const browser = await chromium.launch({ channel: "chrome" });
  try {
    const context = await browser.newContext({
      viewport: { width: 1440, height: 1000 },
      permissions: ["clipboard-read", "clipboard-write"],
    });
    const page = await context.newPage();
    page.setDefaultTimeout(20000);
    page.on("pageerror", (e) => errors.push(e.message));
    await page.goto(
      values.showcase + "/?utm_source=resume&token=must-not-store",
      { waitUntil: "networkidle" },
    );
    await page.getByRole("button", { name: "Allow attribution" }).click();
    expect(
      await page.evaluate(() =>
        JSON.parse(sessionStorage.getItem("atlas-campaign")),
      ),
    ).toEqual({ utm_source: "resume" });
    await screenshot(page, "showcase-desktop");
    await page.getByRole("button", { name: "Switch to dark mode" }).click();
    await page.reload();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
    await screenshot(page, "showcase-dark");
    await page.getByRole("button", { name: "Switch to light mode" }).click();
    await page.getByRole("button", { name: "Search this site" }).click();
    await page.getByLabel("Search this project").fill("permission");
    await expect(page.locator("#search-results a")).not.toHaveCount(0);
    await page.getByLabel("Search this project").fill("zzzz no result");
    await expect(page.locator("#search-results")).toContainText(
      "No matching section",
    );
    await page.keyboard.press("Escape");
    await expect(
      page.getByRole("button", { name: "Search this site" }),
    ).toBeFocused();
    passed(
      "Showcase: persisted themes, explicit attribution, search results, empty state and dialog focus restoration",
    );
    await page.getByRole("button", { name: "Contact ↗", exact: true }).click();
    await page.getByRole("button", { name: "Prepare issue" }).click();
    await expect(page.locator("#contact-error")).toContainText(
      "at least 5 characters",
    );
    await page
      .getByLabel("Subject", { exact: true })
      .fill("Synthetic interface review");
    await page
      .getByLabel("What happened?")
      .fill("Verification draft only. Do not send this issue.");
    await page.getByRole("button", { name: "Prepare issue" }).click();
    await expect(page.locator("#contact-success")).toContainText(
      "Nothing has been sent",
    );
    await expect(page.locator("#issue-link")).toHaveAttribute(
      "href",
      /issues\/new\?title=Synthetic/,
    );
    await page.keyboard.press("Escape");
    await page.getByText("How do I try Atlas?", { exact: true }).click();
    await expect(page.locator("#faq details").first()).toHaveAttribute(
      "open",
      "",
    );
    await page.getByRole("button", { name: "Copy demo link" }).click();
    await expect(page.locator("#copy-feedback")).toContainText(
      "Demo link copied",
    );
    expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(
      "https://cavalry-habitable-sureness.ngrok-free.dev",
    );
    await page.getByRole("button", { name: "Privacy preferences" }).click();
    await page.getByRole("button", { name: "Essential only" }).click();
    expect(
      await page.evaluate(() => sessionStorage.getItem("atlas-campaign")),
    ).toBeNull();
    await expect(
      page.getByRole("button", { name: "Back to top" }),
    ).toBeVisible();
    expect(
      await page
        .locator(".scroll-progress")
        .evaluate((el) => el.getBoundingClientRect().width),
    ).toBeGreaterThan(0);
    await page.getByRole("button", { name: "Back to top" }).click();
    await expect.poll(() => page.evaluate(() => scrollY)).toBe(0);
    passed(
      "Showcase: contact error/success, FAQ, real clipboard, opt-out cleanup, scroll progress and back-to-top",
    );
    for (const width of [320, 390, 768, 1440]) {
      await page.setViewportSize({ width, height: 844 });
      await fits(page);
    }
    await page.setViewportSize({ width: 390, height: 844 });
    await page.getByRole("button", { name: "Open menu" }).click();
    await expect(page.getByRole("navigation")).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.getByRole("button", { name: "Open menu" })).toBeFocused();
    await screenshot(page, "showcase-mobile");
    await page.emulateMedia({ media: "print", reducedMotion: "reduce" });
    await expect(page.locator(".site-header")).toBeHidden();
    await expect(page.locator(".floating-tools")).toBeHidden();
    await expect(page.locator("h1")).toBeVisible();
    await page.pdf({
      path: path.join(out, "showcase-print.pdf"),
      format: "A4",
    });
    passed(
      "Showcase: 320/390/768/1440px layouts, mobile menu keyboard access and actual print PDF",
    );
    await page.emulateMedia({
      media: "screen",
      reducedMotion: "no-preference",
    });
    if (values["account-file"] && values["journey-file"]) {
      const account = JSON.parse(fs.readFileSync(values["account-file"]));
      const fixture = JSON.parse(fs.readFileSync(values["journey-file"]));
      const base = fixture.url + "/o/" + fixture.organization;
      await page.setViewportSize({ width: 1440, height: 1000 });
      await page.goto(
        fixture.url +
          "/login?next=" +
          encodeURIComponent("/o/" + fixture.organization),
        { waitUntil: "networkidle" },
      );
      const tunnel = page.getByRole("button", {
        name: "Visit Site",
        exact: true,
      });
      const loginHeading = page.getByRole("heading", {
        name: "Sign in to Atlas",
      });
      await expect(tunnel.or(loginHeading)).toBeVisible();
      if (await tunnel.isVisible()) await tunnel.click();
      await expect(loginHeading).toBeVisible();
      const consent = page.getByRole("button", { name: "Essential only" });
      await consent.click();
      await expect(page.locator(".privacy-banner")).toHaveCount(0);
      await screenshot(page, "login-desktop");
      await page
        .getByLabel("Email address", { exact: true })
        .fill(account.email);
      await page.getByLabel("Password", { exact: true }).fill(account.password);
      await page.getByRole("button", { name: "Sign in", exact: true }).click();
      await expect(page).toHaveURL(base);
      await expect(page.locator(".loading")).toHaveCount(0);
      await expect(
        page.getByText("Database: Ready", { exact: true }),
      ).toBeVisible();
      await screenshot(page, "home-desktop");
      await page.getByRole("button", { name: "Help and contact" }).click();
      await page.getByRole("button", { name: "Privacy preferences" }).click();
      // First-visit choices must not obstruct persistent account navigation.
      for (const width of [768, 1024, 1440]) {
        await page.setViewportSize({ width, height: 1000 });
        const signOut = page.getByRole("button", {
          name: "Sign out",
          exact: true,
        });
        expect(
          await signOut.evaluate((el) => {
            const rect = el.getBoundingClientRect();
            return el.contains(
              document.elementFromPoint(
                rect.x + rect.width / 2,
                rect.y + rect.height / 2,
              ),
            );
          }),
        ).toBe(true);
        await fits(page);
      }
      await page.getByRole("button", { name: "Essential only" }).click();
      await page.getByRole("button", { name: "Switch to dark mode" }).click();
      await page.reload();
      await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
      await expect(page.locator(".loading")).toHaveCount(0);
      await screenshot(page, "home-dark");
      await page.getByRole("button", { name: "Switch to light mode" }).click();
      await page
        .getByRole("button", { name: "Search and navigate Atlas" })
        .click();
      await page.getByLabel("Search Atlas", { exact: true }).fill("Juniper");
      await expect(
        page
          .getByRole("dialog")
          .getByRole("button", { name: /Juniper release runbook/ }),
      ).toBeVisible();
      await page.keyboard.press("Escape");
      await page.evaluate(() =>
        scrollTo(0, document.documentElement.scrollHeight),
      );
      await expect(
        page.getByRole("button", { name: "Back to top" }),
      ).toBeVisible();
      await page.getByRole("button", { name: "Back to top" }).click();
      await expect.poll(() => page.evaluate(() => scrollY)).toBe(0);
      passed(
        "Live app: actual account login, workspace data/health, persisted theme, permission-scoped search and page scrolling",
      );
      await page.goto(base + "/library/" + fixture.pdf.id, {
        waitUntil: "networkidle",
      });
      await page
        .getByRole("button", { name: "Move to trash", exact: true })
        .click();
      const confirmation = page.getByRole("dialog", {
        name: "Move document to trash?",
        exact: true,
      });
      await expect(confirmation).toBeVisible();
      await confirmation
        .getByRole("button", { name: "Cancel", exact: true })
        .click();
      await expect(confirmation).toHaveCount(0);
      await expect(
        page.getByRole("button", { name: "Move to trash", exact: true }),
      ).toBeVisible();
      await page.keyboard.press("Escape");
      await page.goto(fixture.thread_url, { waitUntil: "networkidle" });
      await expect(page.locator(".answer-prose")).toHaveCount(2);
      await page
        .getByRole("button", { name: "Open citation 1", exact: true })
        .first()
        .click();
      await expect(page.getByTestId("source-preview")).toContainText(
        "CEDAR-621",
      );
      await expect(page.getByRole("dialog")).toContainText("Version 1");
      await page.keyboard.press("Escape");
      await page
        .getByRole("button", { name: "Copy answer", exact: true })
        .first()
        .click();
      expect(
        await page.evaluate(() => navigator.clipboard.readText()),
      ).toContain("CEDAR-621");
      await screenshot(page, "conversation-desktop");
      await page.emulateMedia({ media: "print" });
      await expect(page.locator(".composer-area")).toBeHidden();
      await expect(page.locator(".sidebar")).toBeHidden();
      await expect(page.locator(".answer-prose").first()).toBeVisible();
      await page.pdf({
        path: path.join(out, "conversation-print.pdf"),
        format: "A4",
      });
      await page.emulateMedia({ media: "screen" });
      passed(
        "Live app: confirmation cancellation preserves document, historical answers/citations, real answer clipboard and printable conversation",
      );
      await page.setViewportSize({ width: 390, height: 844 });
      await fits(page);
      await screenshot(page, "conversation-mobile");
      const composer = await page.locator(".composer-area").boundingBox();
      const help = await page
        .getByRole("button", { name: "Help and contact" })
        .boundingBox();
      expect(composer.y + composer.height).toBeLessThanOrEqual(help.y);
      await page
        .getByRole("button", { name: "Show evidence panel", exact: true })
        .click();
      await expect(
        page.getByRole("dialog", { name: "Supporting evidence" }),
      ).toBeVisible();
      await page.keyboard.press("Escape");
      await page.getByRole("button", { name: "Open navigation" }).click();
      await expect(
        page.getByRole("dialog", { name: "Workspace navigation" }),
      ).toBeVisible();
      await page.keyboard.press("Escape");
      await expect(
        page.getByRole("button", { name: "Open navigation" }),
      ).toBeFocused();
      await page.goto(base, { waitUntil: "networkidle" });
      await screenshot(page, "home-mobile");
      for (const width of [320, 390, 768, 1440]) {
        await page.setViewportSize({ width, height: 844 });
        await fits(page);
      }
      passed(
        "Live app: mobile conversation composer clearance, evidence drawer, keyboard navigation and 320/390/768/1440px home layouts",
      );
    }
    expect(errors).toEqual([]);
    fs.writeFileSync(
      path.join(out, "results.json"),
      JSON.stringify(
        {
          status: "passed",
          checked_at: new Date().toISOString(),
          checks,
          browser_errors: errors,
        },
        null,
        2,
      ) + "\n",
    );
    console.log("All UI review checks passed.");
  } finally {
    await browser.close();
  }
})().catch((error) => {
  fs.writeFileSync(
    path.join(out, "results.json"),
    JSON.stringify({ status: "failed", checks, error: error.message }, null, 2),
  );
  console.error(error.message);
  process.exit(1);
});
