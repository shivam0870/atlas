import {
  test,
  expect,
  type APIRequestContext,
  type Page,
} from "@playwright/test";
import { createHmac } from "node:crypto";
import { verifiedOwner } from "./helpers";
test.use({ screenshot: "off", trace: "off", actionTimeout: 30000 });
const app = "http://127.0.0.1:8100";
const headers = { "X-Atlas-Client": "console", Origin: app };
const password = "Atlas browser journey private phrase 8472!"; // pragma: allowlist secret -- disposable test account
async function mailLink(
  request: APIRequestContext,
  email: string,
  path: string,
) {
  let link = "";
  await expect
    .poll(
      async () => {
        const list = await request.get(
          "http://127.0.0.1:58025/api/v1/messages",
        );
        expect(list.ok()).toBeTruthy();
        const body = await list.json();
        for (const m of body.messages || []) {
          if (!m.To?.some((to: { Address: string }) => to.Address === email))
            continue;
          const item = await (
            await request.get(`http://127.0.0.1:58025/api/v1/message/${m.ID}`)
          ).json();
          const found = String(item.Text || "").match(
            new RegExp(`http://[^\\s]+${path}[^\\s]*`),
          );
          if (found) {
            link = found[0];
            return true;
          }
        }
        return false;
      },
      { timeout: 20000 },
    )
    .toBe(true);
  return link;
}
async function register(
  request: APIRequestContext,
  email: string,
  name: string,
) {
  const r = await request.post("/api/auth/register", {
    headers,
    data: { email, name, password },
  });
  expect(r.status()).toBe(202);
  const link = await mailLink(request, email, "/verify-email");
  const token = new URL(link).searchParams.get("token");
  expect(
    (await request.post("/api/auth/verify", { headers, data: { token } })).ok(),
  ).toBeTruthy();
}
async function login(page: Page, email: string) {
  await page.goto("/login");
  await page.getByLabel("Email address").fill(email);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page).toHaveURL(/onboarding/);
}
function totp(secret: string) {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"; // pragma: allowlist secret -- public Base32 alphabet
  let bits = "";
  for (const c of secret.replace(/=+$/, ""))
    bits += alphabet.indexOf(c).toString(2).padStart(5, "0");
  const key = Buffer.from(
    (bits.match(/.{8}/g) || []).map((b) => parseInt(b, 2)),
  );
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(Math.floor(Date.now() / 30000)));
  const hash = createHmac("sha1", key).update(counter).digest();
  const offset = hash[19] & 15;
  return ((hash.readUInt32BE(offset) & 0x7fffffff) % 1000000)
    .toString()
    .padStart(6, "0");
}

test("real registration, local email verification, company onboarding, theme and mobile navigation", async ({
  page,
  request,
}) => {
  const id = Date.now();
  const email = `atlas-owner-${id}@example.test`;
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/register");
  await page.getByLabel("Your name").fill("Browser Owner");
  await page.getByLabel("Email address").fill(email);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page
    .getByRole("button", { name: "Create account", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Check your inbox" }),
  ).toBeVisible();
  await page.goto(await mailLink(request, email, "/verify-email"));
  await page.getByRole("button", { name: "Verify email address" }).click();
  await expect(page.getByRole("status")).toContainText("verified");
  await login(page, email);
  await page.getByLabel("Company name").fill("Browser Product Company");
  await page.getByLabel("Workspace address").fill(`browser-product-${id}`);
  await page
    .getByRole("button", { name: "Create company", exact: true })
    .click();
  await expect(page).toHaveURL(/\/o\/[a-f0-9-]+$/);
  const orgURL = page.url();
  await expect(page.locator("main h1")).toBeVisible();
  await expect(page.locator(".loading")).toHaveCount(0);
  await expect(
    page.getByRole("heading", { name: "Workspace health", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("Database: Ready", { exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: "../artifacts/atlas-upgrade-home.png",
    fullPage: true,
    animations: "disabled",
  });
  const accountResponse = await page.goto(`${orgURL}/settings/account`);
  expect(accountResponse?.status(), `Account deep link ${page.url()}`).toBe(
    200,
  );
  await page.getByLabel("Display name").fill("Browser Updated Owner");
  await page
    .getByRole("combobox", { name: "Theme", exact: true })
    .selectOption("dark");
  await page.getByRole("button", { name: "Save profile" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await expect(
    page.getByRole("button", { name: "Save profile" }),
  ).toBeEnabled();
  await page.screenshot({
    path: "../artifacts/atlas-upgrade-account-dark.png",
    fullPage: true,
    animations: "disabled",
  });
  await page.reload();
  await expect(page.getByLabel("Display name")).toHaveValue(
    "Browser Updated Owner",
  );
  await page
    .getByRole("combobox", { name: "Theme", exact: true })
    .selectOption("light");
  await page.getByRole("button", { name: "Save profile" }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await page.getByRole("button", { name: "Open navigation" }).click();
  await expect(
    page.getByRole("dialog", { name: "Workspace navigation" }),
  ).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Open navigation" }),
  ).toBeFocused();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  expect(errors).toEqual([]);
  await page.goto(orgURL);
  await expect(page.locator("main h1")).toBeVisible();
  await expect(page.locator(".loading")).toHaveCount(0);
  await page.screenshot({
    path: "../artifacts/atlas-upgrade-mobile.png",
    fullPage: true,
    animations: "disabled",
  });
});

test("real invitation, password reset session revocation and one-use MFA recovery", async ({
  browser,
  request,
  page,
}) => {
  const id = Date.now();
  const email = `atlas-security-${id}@example.test`;
  await register(request, email, "Security Owner");
  await login(page, email);
  await page.getByLabel("Company name").fill("Security Test Company");
  await page.getByLabel("Workspace address").fill(`security-company-${id}`);
  await page
    .getByRole("button", { name: "Create company", exact: true })
    .click();
  await expect(page).toHaveURL(/\/o\/[a-f0-9-]+$/);
  const orgURL = page.url();
  const tenant = orgURL.split("/").pop()!;
  const invitee = `atlas-invite-${id}@example.test`;
  await page.goto(`${orgURL}/people`);
  await page.getByRole("button", { name: "Invite someone" }).click();
  await page.getByLabel("Email address").fill(invitee);
  await page
    .getByRole("button", { name: "Send invitation", exact: true })
    .click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  const invitation = await mailLink(request, invitee, "/invite");
  await register(request, invitee, "Invited Viewer");
  const colleague = await browser.newContext({ baseURL: app });
  const colleaguePage = await colleague.newPage();
  await login(colleaguePage, invitee);
  await colleaguePage.goto(invitation);
  await colleaguePage
    .getByRole("button", { name: "Accept invitation" })
    .click();
  await expect(colleaguePage).toHaveURL(new RegExp(tenant));
  await expect(
    colleaguePage.getByRole("link", { name: "People & teams", exact: true }),
  ).toHaveCount(0);
  const second = await browser.newContext({ baseURL: app });
  const secondPage = await second.newPage();
  await login(secondPage, email);
  await page.goto(`${orgURL}/settings/account`);
  await page.getByRole("button", { name: "Set up", exact: true }).click();
  await page.getByLabel("Current password", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Confirm", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Connect your authenticator" }),
  ).toBeVisible();
  const secret = (await page.locator(".secret-code").innerText()).trim();
  await page.getByLabel("Six-digit code").fill(totp(secret));
  await page
    .getByRole("button", { name: "Enable two-step authentication" })
    .click();
  await expect(
    page.getByRole("heading", { name: "Save your recovery codes" }),
  ).toBeVisible();
  const recovery = await page
    .locator(".recovery-codes code")
    .first()
    .innerText();
  const recoverySecond = await page
    .locator(".recovery-codes code")
    .nth(1)
    .innerText();
  await page.getByRole("button", { name: "Close dialog" }).click();
  await page.getByRole("button", { name: "Sign out", exact: true }).click();
  await page.getByLabel("Email address").fill(email);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await page.getByLabel("Authentication or recovery code").fill(recovery);
  await page.getByRole("button", { name: "Verify and sign in" }).click();
  await expect(page).toHaveURL(/onboarding/);
  const replay = await request.post("/api/auth/login", {
    headers,
    data: { email, password },
  });
  const challenge = (await replay.json()).challenge;
  expect(challenge).toBeTruthy();
  expect(
    (
      await request.post("/api/auth/mfa/login", {
        headers,
        data: { challenge, code: recovery },
      })
    ).status(),
  ).toBe(401);
  await secondPage.goto("/login");
  await secondPage.getByLabel("Email address").fill(email);
  await secondPage.getByLabel("Password", { exact: true }).fill(password);
  await secondPage
    .getByRole("button", { name: "Sign in", exact: true })
    .click();
  await secondPage
    .getByLabel("Authentication or recovery code")
    .fill(recoverySecond);
  await secondPage.getByRole("button", { name: "Verify and sign in" }).click();
  await expect(secondPage).toHaveURL(/onboarding/);
  expect((await second.request.get("/api/auth/me")).status()).toBe(200);
  await page.goto("/forgot-password");
  await page.getByLabel("Email address").fill(email);
  await page
    .getByRole("button", { name: "Send recovery instructions" })
    .click();
  await page.goto(await mailLink(request, email, "/reset-password"));
  await page
    .getByLabel("New password", { exact: true })
    .fill(`${password} changed`);
  await page
    .getByRole("button", { name: "Reset password", exact: true })
    .click();
  await expect(page.getByRole("status")).toContainText("Password reset");
  const oldSession = await second.request.get("/api/auth/me");
  expect(oldSession.status()).toBe(401);
  await second.close();
  await colleague.close();
});

test("verified invitees can join from their pending company invitation", async ({
  page,
  request,
}) => {
  const owner = await verifiedOwner(request);
  const email = `pending-invite-${Date.now()}@example.test`;
  expect(
    (
      await request.post(
        `/api/organizations/${owner.organization.id}/invitations`,
        { headers, data: { email, role: "viewer" } },
      )
    ).ok(),
  ).toBeTruthy();
  await register(request, email, "Pending Invitee");
  await login(page, email);
  await page
    .getByRole("button", {
      name: `Join ${owner.organization.name}`,
      exact: true,
    })
    .click();
  await expect(page).toHaveURL(new RegExp(`/o/${owner.organization.id}$`));
  await expect(
    page.getByRole("heading", { name: "Welcome back, Pending" }),
  ).toBeVisible();
  await expect(
    page.getByRole("link", { name: "Administration", exact: true }),
  ).toHaveCount(0);
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "Welcome back, Pending" }),
  ).toBeVisible();
});
