import { verifiedOwner } from "./helpers";
import { test, expect } from "@playwright/test";
const origin = process.env.ATLAS_BROWSER_URL || "http://127.0.0.1:8100";
const headers = { "X-Atlas-Client": "console", Origin: origin };
test.use({ screenshot: "off", trace: "off" });

test("real inventory CRUD, CSV validation, comparison, scoped credentials and management screens", async ({
  page,
  request,
}) => {
  const owner = await verifiedOwner(request);
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/login");
  await page.getByLabel("Email address").fill(owner.email);
  await page.getByLabel("Password", { exact: true }).fill(owner.password);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page).toHaveURL(/onboarding/);
  const base = `/o/${owner.organization.id}`;
  await page.goto(`${base}/inventory`);
  await page.getByRole("button", { name: "Add record", exact: true }).click();
  await page.getByLabel("Record name").fill("checkout-browser");
  await page.getByLabel("Owner team").fill("Platform");
  await page.getByLabel("Replicas", { exact: true }).fill("3");
  await page.getByRole("button", { name: "Save record" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "checkout-browser", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "checkout-browser", exact: true })
    .click();
  await expect(page.getByRole("dialog")).toContainText("Platform");
  await page.getByRole("button", { name: "Close dialog" }).click();
  await page.getByRole("button", { name: "Edit", exact: true }).click();
  await page.getByLabel("Replicas", { exact: true }).fill("4");
  await page.getByRole("button", { name: "Save record" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await page.getByRole("button", { name: "Import CSV", exact: true }).click();
  await page
    .getByLabel("CSV contents")
    .fill(
      "name,kind,owner,environment,replicas,status,description\ninventory-browser,service,Supply,staging,2,active,Browser import fixture",
    );
  await page.getByRole("button", { name: "Validate preview" }).click();
  await expect(page.getByText("1 valid rows of 1")).toBeVisible();
  await page.getByRole("button", { name: "Import 1 records" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await page.getByLabel("Compare checkout-browser").check();
  await page.getByLabel("Compare inventory-browser").check();
  await page.getByRole("button", { name: "Compare 2 records" }).click();
  await expect(
    page.getByRole("dialog", { name: "Compare inventory" }),
  ).toContainText("Platform");
  await expect(page.getByRole("dialog")).toContainText("Supply");
  await page.getByRole("button", { name: "Close dialog" }).click();
  await page.getByRole("button", { name: "Archive inventory-browser" }).click();
  await page
    .getByRole("button", { name: "Archive record", exact: true })
    .click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "inventory-browser", exact: true }),
  ).toHaveCount(0);
  await page.goto(`${base}/integrations`);
  await page
    .getByRole("button", { name: "Create integration", exact: true })
    .first()
    .click();
  await page.getByLabel("Integration name").fill("Browser test client");
  await page.getByRole("checkbox", { name: "General", exact: true }).check();
  await page.getByRole("button", { name: "Create credential" }).click();
  await expect(
    page.getByRole("heading", { name: "Save your integration key" }),
  ).toBeVisible();
  const key = await page.locator(".integration-secret").innerText();
  await page.getByRole("button", { name: "Close dialog" }).click();
  const serviceRead = await request.get("/api/inventory", {
    headers: { Authorization: `Bearer ${key}` },
  });
  expect(serviceRead.status()).toBe(200);
  const serviceData = await serviceRead.json();
  expect(
    serviceData.items.some(
      (item: { name: string }) => item.name === "checkout-browser",
    ),
  ).toBe(true);
  await page.getByRole("button", { name: "Revoke", exact: true }).click();
  await page.getByRole("button", { name: "Revoke key", exact: true }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  expect(
    (
      await request.get("/api/inventory", {
        headers: { Authorization: `Bearer ${key}` },
      })
    ).status(),
  ).toBe(401);
  await page.goto(`${base}/library`);
  await page.getByRole("button", { name: "General", exact: true }).click();
  await page
    .getByRole("button", { name: "Manage access", exact: true })
    .click();
  await expect(page.getByRole("dialog")).toContainText(
    "Browser test client · read",
  );
  await expect(
    page.getByRole("heading", { name: "Effective access after saved grants" }),
  ).toBeVisible();
  const revokedService = page.getByRole("row", { name: /Browser test client/ });
  await expect(revokedService.getByText("Denied", { exact: true })).toHaveCount(
    2,
  );
  await page.getByRole("button", { name: "Close dialog" }).click();
  await page.goto(`${base}/administration`);
  await page.getByLabel("Requests per minute").fill("45");
  await page.getByRole("button", { name: "Save company limits" }).click();
  await expect(page.getByRole("status")).toContainText(
    "Company limits updated",
  );
  await page.reload();
  await expect(page.getByLabel("Requests per minute")).toHaveValue("45");
  await page
    .getByRole("button", { name: "Audit history", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Audit history", exact: true }),
  ).toBeVisible();
  await page.goto(`${base}/insights`);
  await expect(
    page.getByRole("heading", {
      name: "Monthly quota utilization",
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    page.getByRole("progressbar", { name: "Monthly token quota utilization" }),
  ).toBeVisible();
  await expect(
    page.getByText("Average indexing (including queue)", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Runtime readiness" }),
  ).toBeVisible();
  await expect(
    page.getByText("Paid model APIs: Disabled", { exact: false }),
  ).toBeVisible();
  await page.goto(`${base}/quality?tab=runs`);
  await expect(
    page.getByRole("button", { name: "Start evaluation" }),
  ).toBeDisabled();
  await expect(
    page.getByText("Review all current labels", { exact: false }),
  ).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  expect(errors).toEqual([]);
});
