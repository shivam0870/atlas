import { test, expect } from "@playwright/test";

test("workspace overview, source preview, navigation and responsive layout", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/");
  await expect(page.getByText("Welcome to your workspace")).toBeVisible();
  await page
    .getByRole("button", { name: "Acme Engineering Local workspace" })
    .click();
  await expect(
    page.getByRole("heading", { name: /A little more clarity/ }),
  ).toBeVisible();
  await expect(page.locator(".recent-docs > button").first()).toBeVisible();
  await page.screenshot({
    path: "../artifacts/atlas-overview.png",
    fullPage: true,
  });
  await page
    .getByRole("button", { name: /Documents/ })
    .first()
    .click();
  await page
    .getByRole("textbox", { name: "Search documents" })
    .fill("Deployment operations");
  await page
    .getByRole("button", {
      name: "Deployment operations handbook Team runbook",
    })
    .click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(page.getByRole("dialog")).toContainText("AMBER-ORCHID");
  await page.getByRole("button", { name: "Close dialog" }).click();
  await page.getByRole("button", { name: "Evaluation", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Evidence over assumptions." }),
  ).toBeVisible();
  await expect(page.locator(".review-list > button")).toHaveCount(50);
  await expect(
    page.getByRole("button", { name: "Run evaluation" }),
  ).toBeDisabled();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await expect(
    page.getByText("Generation model", { exact: true }),
  ).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "Open navigation" }).click();
  await page.getByRole("button", { name: "Overview", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: /A little more clarity/ }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: "../artifacts/atlas-mobile.png",
    fullPage: true,
  });
  expect(errors).toEqual([]);
});

test("live streamed answer includes correct tenant-private evidence", async ({
  page,
}) => {
  await page.goto("/");
  await page
    .getByRole("button", { name: "Acme Engineering Local workspace" })
    .click();
  await page
    .locator("nav")
    .getByRole("button", { name: /Ask Atlas/ })
    .click();
  await page
    .getByRole("textbox", { name: "Your question" })
    .fill("What is the checkout release approval code?");
  await page.getByRole("button", { name: "Send question" }).click();
  await expect(page.locator(".answer-text")).toContainText("AMBER-ORCHID", {
    timeout: 150000,
  });
  await expect(
    page.getByRole("button", { name: "Stop generation" }),
  ).toHaveCount(0, { timeout: 150000 });
  await expect(
    page.locator(".sources-panel .source-card").first(),
  ).toBeVisible();
  await page.screenshot({
    path: "../artifacts/atlas-answer.png",
    fullPage: true,
  });
  await page.locator(".sources-panel .source-card").first().click();
  await expect(page.locator(".highlight-panel")).toContainText("AMBER-ORCHID");
  await page.getByRole("button", { name: "Close dialog" }).click();
  await page.locator(".workspace-switch > button").click();
  await page
    .getByRole("button", { name: "Northstar Labs", exact: true })
    .click();
  await page
    .getByRole("button", { name: /Documents/ })
    .first()
    .click();
  await expect(page.locator("tbody")).not.toContainText("Namespaces");
  await page
    .getByRole("button", {
      name: "Deployment operations handbook Team runbook",
    })
    .click();
  await expect(page.getByRole("dialog")).toContainText("SILVER-MAPLE");
  await expect(page.getByRole("dialog")).not.toContainText("AMBER-ORCHID");
});

test("upload raw text, inspect source and remove it", async ({ page }) => {
  await page.goto("/");
  await page
    .getByRole("button", { name: "Acme Engineering Local workspace" })
    .click();
  await page
    .getByRole("button", { name: "Add knowledge", exact: true })
    .click();
  await page.getByRole("button", { name: "Paste text", exact: true }).click();
  const title = "Browser validation " + Date.now();
  await page.getByRole("textbox", { name: "Document title" }).fill(title);
  await page
    .getByRole("textbox", { name: "Content", exact: true })
    .fill(
      "The browser validation service is called Juniper. Its deployment owner is the Test team. This is synthetic end-to-end test data.",
    );
  await page.getByRole("button", { name: "Add to workspace" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0, { timeout: 120000 });
  await page
    .getByRole("button", { name: /Documents/ })
    .first()
    .click();
  await page.getByRole("textbox", { name: "Search documents" }).fill(title);
  await expect(page.locator("tbody tr")).toHaveCount(1);
  await page.getByRole("button", { name: "Delete " + title }).click();
  await page
    .getByRole("button", { name: "Remove document", exact: true })
    .click();
  await expect(page.locator("tbody tr")).toHaveCount(0);
});

test("file upload is indexed and answers a new question with its source", async ({
  page,
}) => {
  await page.goto("/");
  await page
    .getByRole("button", { name: "Acme Engineering Local workspace" })
    .click();
  await page
    .getByRole("button", { name: "Add knowledge", exact: true })
    .click();
  const title = "Browser validation file " + Date.now() + ".md";
  await page
    .getByLabel("Choose document file")
    .setInputFiles({
      name: title,
      mimeType: "text/markdown",
      buffer: Buffer.from(
        "# Juniper release\n\nThe Juniper validation service release code is LILAC-8472. Its owner is the Test team. This is synthetic browser test data.",
      ),
    });
  await page.getByRole("button", { name: "Add to workspace" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await page
    .locator("nav")
    .getByRole("button", { name: /Documents/ })
    .click();
  await page.getByRole("textbox", { name: "Search documents" }).fill(title);
  await expect(page.locator("tbody tr")).toContainText("ready", {
    timeout: 120000,
  });
  await page
    .locator("nav")
    .getByRole("button", { name: "Ask Atlas", exact: true })
    .click();
  await page
    .getByRole("textbox", { name: "Your question" })
    .fill("What is the Juniper validation service release code?");
  await page.getByRole("button", { name: "Send question" }).click();
  await expect(page.locator(".answer-text")).toContainText("LILAC-8472", {
    timeout: 120000,
  });
  await expect(
    page.getByRole("button", { name: "Stop generation" }),
  ).toHaveCount(0, { timeout: 120000 });
  await page.locator(".answer-text .citation").first().click();
  await expect(page.locator(".highlight-panel")).toContainText("LILAC-8472");
  await page.getByRole("button", { name: "Close dialog" }).click();
  await page
    .locator("nav")
    .getByRole("button", { name: /Documents/ })
    .click();
  await page.getByRole("button", { name: "Delete " + title }).click();
  await page
    .getByRole("button", { name: "Remove document", exact: true })
    .click();
  await expect(page.locator("tbody tr")).toHaveCount(0);
});

test("switching workspaces cancels an active answer and clears its state", async ({
  page,
}) => {
  await page.goto("/");
  await page
    .getByRole("button", { name: "Acme Engineering Local workspace" })
    .click();
  await page
    .locator("nav")
    .getByRole("button", { name: "Ask Atlas", exact: true })
    .click();
  await page
    .getByRole("textbox", { name: "Your question" })
    .fill("Explain Kubernetes namespaces and how teams should use them.");
  await page.getByRole("button", { name: "Send question" }).click();
  await expect(
    page.getByRole("button", { name: "Stop generation" }),
  ).toBeVisible();
  await page.locator(".workspace-switch > button").click();
  await page
    .getByRole("button", { name: "Northstar Labs", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: /A little more clarity/ }),
  ).toBeVisible();
  await page
    .locator("nav")
    .getByRole("button", { name: "Ask Atlas", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Stop generation" }),
  ).toHaveCount(0);
  await expect(page.locator(".answer-text")).toHaveCount(0);
  await expect(page.locator(".sources-panel .source-card")).toHaveCount(0);
});
