import { test, expect } from "@playwright/test";
import { verifiedOwner } from "./helpers";
const origin = "http://127.0.0.1:8100";
const headers = { "X-Atlas-Client": "console", Origin: origin };
// Upload/generation/citation/company-switch assertions from the old demo console
// now live in upgrade-knowledge.spec.ts against individually authenticated users.
test("retired workspace keys cannot create a browser session", async ({
  request,
  page,
}) => {
  expect((await request.get("/api/local/workspaces")).status()).toBe(410);
  expect(
    (
      await request.post(
        "/api/local/connect/00000000-0000-0000-0000-000000000000",
        { headers },
      )
    ).status(),
  ).toBe(410);
  expect(
    (
      await request.post("/api/session", {
        headers,
        data: {
          api_key: "retired-demo-key", // pragma: allowlist secret -- rejected test-only demo key
        },
      })
    ).status(),
  ).toBe(410);
  await page.goto("/o/00000000-0000-0000-0000-000000000000/library");
  await expect(page).toHaveURL(/\/login\?next=/);
  await expect(
    page.getByRole("heading", { name: "Sign in to Atlas" }),
  ).toBeVisible();
  await expect(page.getByLabel("Email address")).toBeVisible();
  await expect(page.getByLabel(/API key/)).toHaveCount(0);
});
test("create raw text, inspect its immutable version, trash and restore", async ({
  page,
  request,
}) => {
  const owner = await verifiedOwner(request);
  const base = `/o/${owner.organization.id}`;
  const scoped = { ...headers, "X-Atlas-Tenant": owner.organization.id };
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/login");
  await page.getByLabel("Email address").fill(owner.email);
  await page.getByLabel("Password", { exact: true }).fill(owner.password);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page).toHaveURL(/onboarding/);
  await page.goto(`${base}/library`);
  await page
    .getByRole("button", { name: "Add knowledge", exact: true })
    .click();
  await page.getByText("Or write a document", { exact: true }).click();
  const title = `Browser text ${Date.now()}`;
  const content =
    "The synthetic Juniper team owns deployment validation. Its local browser test code is RAW-8721.";
  await page.getByLabel("Document title").fill(title);
  await page.getByLabel("Document text").fill(content);
  await page.getByRole("button", { name: "Save and index" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  let documentId = "";
  await expect
    .poll(
      async () => {
        const data = await (
          await request.get(`/api/library?q=${encodeURIComponent(title)}`, {
            headers: scoped,
          })
        ).json();
        const document = data.items?.find(
          (item: { title: string }) => item.title === title,
        );
        documentId = document?.id || "";
        return document?.status;
      },
      { timeout: 180000 },
    )
    .toBe("ready");
  await page.goto(`${base}/library/${documentId}`);
  await expect(page.getByRole("dialog")).toContainText(content);
  await expect(page.getByRole("dialog")).toContainText("Version");
  await page.reload();
  await expect(page.getByRole("dialog")).toContainText(content);
  await page
    .getByRole("button", { name: "Move to trash", exact: true })
    .click();
  await page
    .getByRole("dialog", { name: "Move document to trash?" })
    .getByRole("button", { name: "Confirm", exact: true })
    .click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  const trashed = await (
    await request.get("/api/library?lifecycle=trashed", { headers: scoped })
  ).json();
  expect(
    trashed.items.some((item: { id: string }) => item.id === documentId),
  ).toBe(true);
  await page.goto(`${base}/library/${documentId}`);
  await page
    .getByRole("button", { name: "Restore to active", exact: true })
    .click();
  await expect
    .poll(async () => {
      const data = await (
        await request.get(`/api/library/${documentId}`, { headers: scoped })
      ).json();
      return data.lifecycle;
    })
    .toBe("active");
  expect(errors).toEqual([]);
});
