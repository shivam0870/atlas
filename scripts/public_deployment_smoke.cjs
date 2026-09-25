/* Public deployment verification using existing private synthetic fixtures only.
 * Run from the repository root after deployment: node scripts/public_deployment_smoke.cjs
 * Never registers accounts, sends emails, migrates data, or manages services.
 */
const fs = require("node:fs");
const path = require("node:path");
const { chromium, expect } = require("../web/node_modules/@playwright/test");

const root = path.resolve(__dirname, "..");
const fixtureRoot = path.join(root, ".local/public-demo");
const account = JSON.parse(
  fs.readFileSync(path.join(fixtureRoot, "journey-account.json"), "utf8"),
);
const fixture = JSON.parse(
  fs.readFileSync(path.join(fixtureRoot, "journey.json"), "utf8"),
);
const appLine = fs
  .readFileSync(path.join(fixtureRoot, ".env"), "utf8")
  .split(/\r?\n/)
  .find((line) => line.startsWith("APP_URL="));
const origin = (appLine || "")
  .slice(8)
  .trim()
  .replace(/^['"]|['"]$/g, "")
  .replace(/\/$/, "");
if (
  !origin.startsWith("https://") ||
  !account.email.endsWith(".test") ||
  !fixture.organization ||
  !fixture.other_organization ||
  !fixture.space
) {
  throw new Error(
    "A configured HTTPS demo and existing synthetic journey fixtures are required",
  );
}
const destination = path.join(fixtureRoot, "production-smoke");
fs.mkdirSync(destination, { recursive: true, mode: 0o700 });
const result = {
  status: "running",
  checks: [],
  browser_errors: 0,
  started_at: new Date().toISOString(),
};
let stage = "startup";
function save() {
  fs.writeFileSync(
    path.join(destination, "result.json"),
    JSON.stringify(result, null, 2) + "\n",
    { mode: 0o600 },
  );
}
async function step(name, action) {
  stage = name;
  await action();
  result.checks.push(name);
  save();
}

(async () => {
  let browser;
  try {
    browser = await chromium.launch({ channel: "chrome", headless: true });
    const context = await browser.newContext({
      baseURL: origin,
      viewport: { width: 1440, height: 960 },
      extraHTTPHeaders: { "ngrok-skip-browser-warning": "atlas-verification" },
    });
    const page = await context.newPage();
    page.setDefaultTimeout(25000);
    page.on("pageerror", () => {
      result.browser_errors += 1;
    });
    const scoped = {
      Origin: origin,
      "X-Atlas-Client": "console",
      "X-Atlas-Tenant": fixture.organization,
    };
    const base = `/o/${fixture.organization}`;
    const stamp = Date.now().toString();
    const filename = `production-smoke-${stamp}.md`;
    const code = `SILVER-PINE-${stamp.slice(-5)}`;
    const content = `# Arbor ${stamp} release\n\nThe verification code for the Arbor ${stamp} release is ${code}. The Platform team approves this release. This is synthetic deployment verification content.\n`;
    let uploaded;

    await step("existing_fixture_https_login", async () => {
      await page.goto("/login");
      const interstitial = page.getByRole("button", {
        name: "Visit Site",
        exact: true,
      });
      if (await interstitial.isVisible()) await interstitial.click();
      const privacy = page.getByRole("button", {
        name: "Essential only",
        exact: true,
      });
      if (await privacy.isVisible()) await privacy.click();
      await page
        .getByLabel("Email address", { exact: true })
        .fill(account.email);
      await page.getByLabel("Password", { exact: true }).fill(account.password);
      await page.getByRole("button", { name: "Sign in", exact: true }).click();
      await expect(page).toHaveURL(/\/onboarding/);
      const session = (await context.cookies()).find(
        (cookie) => cookie.name === "atlas_user_session",
      );
      expect(session?.secure).toBe(true);
      expect(session?.httpOnly).toBe(true);
      expect(session?.sameSite).toBe("Strict");
      const spaces = await context.request.get("/api/spaces", {
        headers: scoped,
      });
      expect(spaces.status()).toBe(200);
      expect(
        (await spaces.json()).some((space) => space.id === fixture.space),
      ).toBe(true);
    });

    await step("five_workbench_tabs_and_operations", async () => {
      for (const [route, title] of [
        ["sources", "Sources"],
        ["compare", "Compare"],
        ["knowledge-map", "Knowledge Map"],
        ["playbooks", "Playbooks"],
        ["briefings", "Briefings"],
      ]) {
        await page.goto(`${base}/${route}`);
        await expect(
          page.getByRole("heading", { name: title, exact: true }),
        ).toBeVisible();
        await expect(page.locator(".loading")).toHaveCount(0);
        await expect(page.getByRole("alert")).toHaveCount(0);
      }
      const operations = await context.request.get("/api/operations", {
        headers: scoped,
      });
      expect(operations.status()).toBe(200);
      const health = await operations.json();
      expect(health.health.scanner.ready).toBe(true);
      expect(health.platform_operator).toBe(false);
      await page.goto(`${base}/administration?tab=operations`);
      await expect(
        page.getByRole("heading", {
          name: "Operations & recovery",
          exact: true,
        }),
      ).toBeVisible();
      await expect(page.locator(".loading")).toHaveCount(0);
      await page.screenshot({
        path: path.join(destination, "operations.png"),
        fullPage: true,
      });
    });

    await step("fixture_upload_scanning_and_indexing", async () => {
      await page.goto(`${base}/library?space=${fixture.space}`);
      await page
        .getByRole("button", { name: "Add knowledge", exact: true })
        .click();
      await page.getByLabel("Destination space").selectOption(fixture.space);
      await page.getByLabel("Choose documents").setInputFiles({
        name: filename,
        mimeType: "text/markdown",
        buffer: Buffer.from(content),
      });
      await page
        .getByRole("button", { name: "Upload 1 files", exact: true })
        .click();
      await expect(
        page.getByText("Indexing queued", { exact: true }),
      ).toBeVisible();
      await page
        .getByRole("button", { name: "Close dialog", exact: true })
        .click();
      await expect
        .poll(
          async () => {
            const response = await context.request.get(
              `/api/library?space_id=${fixture.space}&q=${filename}`,
              { headers: scoped },
            );
            expect(response.status()).toBe(200);
            uploaded = (await response.json()).items.find(
              (document) => document.title === filename,
            );
            return uploaded?.status;
          },
          { timeout: 180000, intervals: [1000, 2000, 3000] },
        )
        .toBe("ready");
      result.document_id = uploaded.id;
      result.version_id = uploaded.current_version_id;
    });

    await step("real_generation_and_exact_citation", async () => {
      await page.goto(`${base}/ask`);
      await page
        .getByLabel("Your question")
        .fill(`What is the verification code for the Arbor ${stamp} release?`);
      await page
        .getByRole("button", { name: "Send question", exact: true })
        .click();
      await expect(page.locator(".answer-prose").last()).toContainText(code, {
        timeout: 180000,
      });
      await expect(
        page.getByRole("button", { name: "Stop generation", exact: true }),
      ).toHaveCount(0, { timeout: 180000 });
      await page
        .getByRole("button", { name: "Open citation 1", exact: true })
        .first()
        .click();
      await expect(page.getByTestId("source-preview")).toContainText(code);
      await expect(page.getByRole("dialog")).toContainText("Version 1");
      await page.screenshot({
        path: path.join(destination, "cited-answer.png"),
        fullPage: true,
      });
      await page.keyboard.press("Escape");
    });

    await step("cross_tenant_document_and_version_denial", async () => {
      const other = { ...scoped, "X-Atlas-Tenant": fixture.other_organization };
      for (const route of [
        `/api/library/${uploaded.id}`,
        `/api/library/${uploaded.id}/versions/${uploaded.current_version_id}`,
      ]) {
        const denied = await context.request.get(route, { headers: other });
        expect(denied.status()).toBe(404);
      }
      await page
        .getByLabel("Current workspace", { exact: true })
        .selectOption(fixture.other_organization);
      await expect(page).toHaveURL(
        new RegExp(`/o/${fixture.other_organization}$`),
      );
      await expect(page.locator(".answer-prose")).toHaveCount(0);
      await expect(page.getByText(code, { exact: false })).toHaveCount(0);
      await page.setViewportSize({ width: 390, height: 844 });
      await page.goto(`${base}/briefings`);
      await expect(
        page.getByRole("heading", { name: "Briefings", exact: true }),
      ).toBeVisible();
      await expect(page.locator(".loading")).toHaveCount(0);
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      ).toBe(true);
      await page.screenshot({
        path: path.join(destination, "briefings-mobile.png"),
        fullPage: true,
      });
      expect(result.browser_errors).toBe(0);
    });
    await context.request.post("/api/auth/logout", { headers: scoped });
    result.status = "passed";
    result.completed_at = new Date().toISOString();
    save();
    console.log(
      JSON.stringify({
        status: result.status,
        checks: result.checks,
        browser_errors: result.browser_errors,
      }),
    );
  } catch (error) {
    result.status = "failed";
    result.failed_stage = stage;
    result.error_type = error.name;
    result.completed_at = new Date().toISOString();
    save();
    fs.writeFileSync(
      path.join(destination, "diagnostic.txt"),
      String(error.stack || error),
      { mode: 0o600 },
    );
    console.error(
      JSON.stringify({
        status: "failed",
        stage,
        error_type: error.name,
        diagnostic: ".local/public-demo/production-smoke/diagnostic.txt",
      }),
    );
    process.exitCode = 1;
  } finally {
    if (browser) await browser.close();
  }
})();
