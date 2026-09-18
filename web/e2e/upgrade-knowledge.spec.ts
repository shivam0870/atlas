import { test, expect, type APIRequestContext } from "@playwright/test";
const origin = "http://127.0.0.1:8100";
const headers = { "X-Atlas-Client": "console", Origin: origin };
const password = "Atlas knowledge browser private phrase 5298!"; // pragma: allowlist secret -- disposable test account
test.use({ screenshot: "off", trace: "off" });
async function emailURL(
  request: APIRequestContext,
  email: string,
  path: string,
) {
  let link = "";
  await expect
    .poll(
      async () => {
        const list = await (
          await request.get("http://127.0.0.1:58025/api/v1/messages")
        ).json();
        for (const m of list.messages || []) {
          if (!m.To?.some((r: { Address: string }) => r.Address === email))
            continue;
          const message = await (
            await request.get(`http://127.0.0.1:58025/api/v1/message/${m.ID}`)
          ).json();
          const match = String(message.Text || "").match(
            new RegExp(`http://[^\\s]+${path}[^\\s]*`),
          );
          if (match) {
            link = match[0];
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
async function account(
  request: APIRequestContext,
  email: string,
  name: string,
) {
  expect(
    (
      await request.post("/api/auth/register", {
        headers,
        data: { email, name, password },
      })
    ).status(),
  ).toBe(202);
  const url = await emailURL(request, email, "/verify-email");
  expect(
    (
      await request.post("/api/auth/verify", {
        headers,
        data: { token: new URL(url).searchParams.get("token") },
      })
    ).ok(),
  ).toBeTruthy();
  const login = await request.post("/api/auth/login", {
    headers,
    data: { email, password },
  });
  expect(login.ok()).toBeTruthy();
  return (await login.json()).user;
}

test("real restricted upload, cited answer, follow-up, saved history, company switch and access revocation", async ({
  browser,
  page,
  request,
}) => {
  test.setTimeout(420000);
  const stamp = Date.now();
  const ownerEmail = `knowledge-owner-${stamp}@example.test`;
  await account(request, ownerEmail, "Knowledge Owner");
  const created = await request.post("/api/organizations", {
    headers,
    data: {
      name: "Knowledge Browser Company",
      slug: `knowledge-browser-${stamp}`,
    },
  });
  expect(created.ok()).toBeTruthy();
  const organization = (await created.json()).organization;
  const scoped = { ...headers, "X-Atlas-Tenant": organization.id };
  const createdSpace = await request.post("/api/spaces", {
    headers: scoped,
    data: {
      name: "Private operations",
      description: "Restricted test knowledge",
      visibility: "restricted",
      tags: [],
    },
  });
  expect(createdSpace.status()).toBe(201);
  const space = await createdSpace.json();
  await page.goto("/login");
  await page.getByLabel("Email address").fill(ownerEmail);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page).toHaveURL(/onboarding/);
  await page.goto(`/o/${organization.id}/library`);
  await page
    .getByRole("button", { name: "Add knowledge", exact: true })
    .click();
  await page.getByLabel("Destination space").selectOption(space.id);
  await page.getByLabel("Choose documents").setInputFiles({
    name: "juniper-browser-release.md",
    mimeType: "text/markdown",
    buffer: Buffer.from(
      "# Juniper release guide\n\n🚀 This is a synthetic operations fixture for Juniper. The Juniper release approval code is LILAC-8472. The Platform team approves Juniper releases. Juniper production uses three replicas. Juniper staging uses two replicas. Release operators must obtain Platform approval before deploying Juniper.\n",
    ),
  });
  await page.getByRole("button", { name: "Upload 1 files" }).click();
  await expect(
    page.getByText("Indexing queued", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Close dialog" }).click();
  let document: { id: string; status: string } | undefined;
  await expect
    .poll(
      async () => {
        const result = await (
          await request.get(`/api/library?space_id=${space.id}`, {
            headers: scoped,
          })
        ).json();
        document = result.items?.find((d: { title: string }) =>
          d.title.includes("juniper-browser-release"),
        );
        return document?.status;
      },
      { timeout: 180000, intervals: [1000, 2000, 3000] },
    )
    .toBe("ready");
  const viewerContext = await browser.newContext({ baseURL: origin });
  const viewerEmail = `knowledge-viewer-${stamp}@example.test`;
  const viewer = await account(
    viewerContext.request,
    viewerEmail,
    "Knowledge Viewer",
  );
  expect(
    (
      await request.post(`/api/organizations/${organization.id}/invitations`, {
        headers,
        data: { email: viewerEmail, role: "viewer" },
      })
    ).ok(),
  ).toBeTruthy();
  const invite = await emailURL(request, viewerEmail, "/invite");
  expect(
    (
      await viewerContext.request.post(
        "/api/organizations/invitations/accept",
        { headers, data: { token: new URL(invite).searchParams.get("token") } },
      )
    ).ok(),
  ).toBeTruthy();
  expect(
    (
      await request.put(`/api/spaces/${space.id}/grants`, {
        headers: scoped,
        data: {
          grants: [
            {
              subject_type: "user",
              subject_id: viewer.id,
              permission: "write",
            },
          ],
        },
      })
    ).ok(),
  ).toBeTruthy();
  await page.goto(`/o/${organization.id}/library?space=${space.id}`);
  await page
    .getByRole("button", { name: "Manage access", exact: true })
    .click();
  const effectiveViewer = page.getByRole("row", { name: /Knowledge Viewer/ });
  await expect(
    effectiveViewer.getByText("Allowed", { exact: true }),
  ).toHaveCount(1);
  await expect(
    effectiveViewer.getByText("Denied", { exact: true }),
  ).toHaveCount(1);
  await page.getByRole("button", { name: "Close dialog" }).click();
  const otherResponse = await viewerContext.request.post("/api/organizations", {
    headers,
    data: {
      name: "Private personal workspace",
      slug: `viewer-personal-${stamp}`,
      kind: "personal",
    },
  });
  expect(otherResponse.ok()).toBeTruthy();
  const other = (await otherResponse.json()).organization;
  const viewerPage = await viewerContext.newPage();
  const errors: string[] = [];
  viewerPage.on("pageerror", (e) => errors.push(e.message));
  await viewerPage.goto(`/o/${organization.id}/library?space=${space.id}`);
  await expect(
    viewerPage.getByText("Owner: Knowledge Owner", { exact: true }),
  ).toBeVisible();
  await expect(
    viewerPage.getByRole("button", { name: "Add knowledge", exact: true }),
  ).toHaveCount(0);
  await viewerPage.getByText("More filters", { exact: true }).click();
  const ownerFilter = viewerPage.getByRole("combobox", {
    name: "Owner",
    exact: true,
  });
  await expect(ownerFilter).toBeVisible();
  await expect(
    ownerFilter.locator("option", { hasText: "Knowledge Owner" }),
  ).toHaveCount(1);
  await ownerFilter.selectOption(
    { label: "Knowledge Owner" },
    { timeout: 20000 },
  );
  await expect(viewerPage).toHaveURL(/owner=/);
  await expect(
    viewerPage.getByRole("link", { name: /juniper-browser-release/ }),
  ).toBeVisible();
  await viewerPage.goto(`/o/${organization.id}/ask`);
  await viewerPage
    .getByLabel("Your question")
    .fill("What is the Juniper release approval code?");
  await viewerPage
    .getByRole("button", { name: "Send question", exact: true })
    .click();
  await expect(viewerPage).toHaveURL(/\/ask\/[a-f0-9-]+$/);
  await expect(viewerPage.locator(".answer-prose").last()).toContainText(
    "LILAC-8472",
    { timeout: 180000 },
  );
  await expect(
    viewerPage.getByRole("button", { name: "Stop generation", exact: true }),
  ).toHaveCount(0, { timeout: 180000 });
  const threadURL = viewerPage.url();
  await viewerPage.screenshot({
    path: "../artifacts/atlas-upgrade-answer.png",
    fullPage: true,
  });
  await viewerPage
    .getByRole("button", { name: "Open citation 1", exact: true })
    .first()
    .click();
  await expect(viewerPage.getByTestId("source-preview")).toContainText(
    "LILAC-8472",
  );
  await expect(viewerPage.getByRole("dialog")).toContainText("Version 1");
  await viewerPage.screenshot({
    path: "../artifacts/atlas-upgrade-evidence.png",
    fullPage: true,
  });
  await expect(
    viewerPage.getByTestId("source-preview").locator("mark"),
  ).toContainText("Juniper");
  await viewerPage.keyboard.press("Escape");
  await expect(viewerPage.getByRole("dialog")).toHaveCount(0);
  await viewerPage
    .getByRole("button", { name: "Request diagnostics", exact: true })
    .first()
    .click();
  await expect(
    viewerPage.getByRole("dialog", {
      name: "Request diagnostics",
      exact: true,
    }),
  ).toContainText("Retrieval ranks");
  await expect(viewerPage.getByRole("dialog")).toContainText(
    "No failure recorded",
  );
  await expect(
    viewerPage
      .getByRole("dialog")
      .getByRole("row", { name: /juniper-browser-release/ }),
  ).toHaveCount(1);
  await viewerPage.keyboard.press("Escape");
  await viewerPage
    .getByRole("button", { name: "Save answer", exact: true })
    .first()
    .click();
  await expect(
    viewerPage.getByRole("status").filter({ hasText: "Answer saved" }),
  ).toBeVisible();
  await viewerPage.getByLabel("Your question").fill("Which team approves it?");
  await viewerPage
    .getByRole("button", { name: "Send question", exact: true })
    .click();
  await expect(viewerPage.locator(".answer-prose").last()).toContainText(
    "Platform",
    { timeout: 180000 },
  );
  await expect(
    viewerPage.getByRole("button", { name: "Stop generation", exact: true }),
  ).toHaveCount(0, { timeout: 180000 });
  await viewerPage.reload();
  await expect(viewerPage.locator(".answer-prose")).toHaveCount(2);
  await expect(viewerPage.locator(".answer-prose").first()).toContainText(
    "LILAC-8472",
  );
  await viewerPage.goto(`/o/${organization.id}/saved`);
  await viewerPage.getByRole("button", { name: "Read saved answer" }).click();
  await expect(viewerPage.getByRole("dialog")).toContainText("LILAC-8472");
  await viewerPage.getByRole("button", { name: "Close dialog" }).click();
  await viewerPage.goto(threadURL);
  await viewerPage.setViewportSize({ width: 390, height: 844 });
  await viewerPage.getByRole("button", { name: "Show evidence panel" }).click();
  await expect(
    viewerPage.getByRole("dialog", { name: "Supporting evidence" }),
  ).toBeVisible();
  await viewerPage.keyboard.press("Escape");
  await expect(viewerPage.getByRole("dialog")).toHaveCount(0);
  await expect(
    viewerPage.getByRole("button", { name: "Show evidence panel" }),
  ).toBeFocused();
  expect(
    await viewerPage.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await viewerPage.setViewportSize({ width: 1440, height: 960 });
  await viewerPage
    .getByLabel("Your question")
    .fill(
      "Explain the Juniper deployment approval process, ownership and replica settings in detail.",
    );
  await viewerPage
    .getByRole("button", { name: "Send question", exact: true })
    .click();
  await expect(
    viewerPage.getByRole("button", { name: "Stop generation", exact: true }),
  ).toBeVisible();
  await viewerPage.getByLabel("Current workspace").selectOption(other.id);
  await expect(viewerPage).toHaveURL(new RegExp(`/o/${other.id}$`));
  await expect(viewerPage.locator(".answer-prose")).toHaveCount(0);
  await expect(
    viewerPage.getByText("LILAC-8472", { exact: false }),
  ).toHaveCount(0);
  expect(
    (
      await request.put(`/api/spaces/${space.id}/grants`, {
        headers: scoped,
        data: { grants: [] },
      })
    ).ok(),
  ).toBeTruthy();
  await viewerPage.goto(threadURL);
  await expect(
    viewerPage
      .getByText(
        "This answer is unavailable because its supporting knowledge is no longer accessible.",
        { exact: true },
      )
      .first(),
  ).toBeVisible();
  await expect(viewerPage.locator(".answer-prose")).toHaveCount(0);
  await expect(
    viewerPage.getByText("LILAC-8472", { exact: false }),
  ).toHaveCount(0);
  await viewerPage.goto(`/o/${organization.id}/saved`);
  await expect(
    viewerPage.getByRole("heading", { name: "Saved content unavailable" }),
  ).toBeVisible();
  await expect(
    viewerPage.getByRole("button", { name: "Read saved answer" }),
  ).toHaveCount(0);
  const direct = await viewerContext.request.get(
    `/api/library/${document!.id}`,
    { headers: { "X-Atlas-Tenant": organization.id } },
  );
  expect(direct.status()).toBe(404);
  expect(errors).toEqual([]);
  await viewerContext.close();
});
