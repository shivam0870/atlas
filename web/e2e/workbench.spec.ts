import { test, expect, type APIRequestContext } from "@playwright/test";
import { verifiedOwner } from "./helpers";

const origin = process.env.ATLAS_BROWSER_URL || "http://127.0.0.1:8100";
const headers = { "X-Atlas-Client": "console", Origin: origin };
test.use({ screenshot: "only-on-failure", trace: "off", actionTimeout: 20000 });

async function ready(
  request: APIRequestContext,
  scoped: Record<string, string>,
  documentId: string,
  versionId: string,
) {
  await expect
    .poll(
      async () => {
        const response = await request.get(
          `/api/library/${documentId}/versions/${versionId}`,
          { headers: scoped },
        );
        expect(response.ok()).toBeTruthy();
        return (await response.json()).status;
      },
      { timeout: 180000, intervals: [1000, 2000, 3000] },
    )
    .toBe("ready");
}

test("five workbench tabs use real versioned documents, persist progress, and protect private data", async ({
  page,
  request,
  browser,
}) => {
  test.setTimeout(480000);
  const owner = await verifiedOwner(request);
  const tenant = owner.organization.id;
  const base = `/o/${tenant}`;
  const scoped = { ...headers, "X-Atlas-Tenant": tenant };
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const spaceResponse = await request.post("/api/spaces", {
    headers: scoped,
    data: {
      name: "Workbench operations",
      description: "Synthetic browser fixture",
      visibility: "restricted",
      tags: [],
    },
  });
  expect(spaceResponse.status()).toBe(201);
  const space = await spaceResponse.json();
  const upload = await request.post("/api/library/text", {
    headers: scoped,
    data: {
      title: "Juniper release policy",
      content:
        "# Juniper release policy\n\nThe Juniper approval code is AMBER-3821.\nProduction deployments require one approval from the Platform team.\n",
      space_id: space.id,
    },
  });
  expect(upload.status()).toBe(202);
  const first = await upload.json();
  await ready(request, scoped, first.id, first.version_id);
  const replacement = await request.post("/api/library/text", {
    headers: scoped,
    data: {
      title: "Juniper release policy",
      content:
        "# Juniper release policy\n\nThe Juniper approval code is VIOLET-6942.\nProduction deployments require two approvals from the Platform team.\nRollback verification is required.\n",
      space_id: space.id,
      replace_document_id: first.id,
    },
  });
  expect(replacement.status()).toBe(202);
  const second = await replacement.json();
  await ready(request, scoped, second.id, second.version_id);
  await page.goto("/login");
  await page
    .getByRole("button", { name: "Essential only", exact: true })
    .click();
  await page.getByLabel("Email address").fill(owner.email);
  await page.getByLabel("Password", { exact: true }).fill(owner.password);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page).toHaveURL(/onboarding/);

  await test.step("Sources validates destinations and exposes bounded connector failures", async () => {
    await page.goto(`${base}/sources`);
    await expect(
      page.getByRole("heading", { name: "Sources", exact: true }),
    ).toBeVisible();
    await page
      .getByRole("button", { name: "Connect source", exact: true })
      .first()
      .click();
    await page.getByLabel("Source name").fill("Unavailable public source");
    await page
      .getByLabel("Public repository URL")
      .fill("atlas-synthetic-missing-owner-9471/missing-repository");
    await page.getByLabel("Destination space").selectOption(space.id);
    await page
      .getByRole("button", { name: "Save source", exact: true })
      .click();
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await page
      .getByRole("button", { name: "Preview files", exact: true })
      .click();
    await expect(page.getByRole("dialog").getByRole("alert")).toContainText(
      /unavailable|failed|rate limited/i,
    );
    await page
      .getByRole("button", { name: "Close dialog", exact: true })
      .click();
    await page.getByRole("button", { name: "Sync now", exact: true }).click();
    await expect(page.getByRole("dialog")).toContainText("sync_failed", {
      timeout: 45000,
    });
    await page
      .getByRole("button", { name: "Close dialog", exact: true })
      .click();
  });

  await test.step("Compare shows exact old and new version passages", async () => {
    await page.goto(`${base}/compare`);
    await page.getByLabel("Left document").selectOption(first.id);
    await page.getByLabel("Right document").selectOption(first.id);
    await page.getByLabel("Left version").selectOption(first.version_id);
    await page.getByLabel("Right version").selectOption(second.version_id);
    await page
      .getByRole("button", { name: "Compare sources", exact: true })
      .click();
    const results = page.getByRole("region", { name: "Comparison results" });
    await expect(results).toContainText("AMBER-3821");
    await expect(results).toContainText("VIOLET-6942");
    await expect(results).toContainText("Rollback verification is required.");
  });

  await test.step("Knowledge Map creates an evidence relationship", async () => {
    const auxiliary = await request.post("/api/library/text", {
      headers: scoped,
      data: {
        title: "Juniper rollback guide",
        content:
          "The Juniper rollback guide requires restoring the last known healthy release before traffic is enabled.",
        space_id: space.id,
      },
    });
    expect(auxiliary.status()).toBe(202);
    const guide = await auxiliary.json();
    await ready(request, scoped, guide.id, guide.version_id);
    await page.goto(`${base}/knowledge-map`);
    await page
      .getByRole("button", { name: "Add relationship", exact: true })
      .click();
    await page
      .getByRole("combobox", { name: "From", exact: true })
      .selectOption(`document:${first.id}`);
    await page
      .getByRole("combobox", { name: "To", exact: true })
      .selectOption(`document:${guide.id}`);
    await page
      .getByLabel("Relationship", { exact: true })
      .fill("requires rollback guide");
    await page
      .getByRole("button", { name: "Save relationship", exact: true })
      .click();
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await page
      .getByRole("button", {
        name: "Show relationships for Juniper release policy",
        exact: true,
      })
      .click();
    await expect(
      page.getByRole("region", { name: "Selected node" }),
    ).toContainText("requires rollback guide");
    const graph = await (
      await request.get("/api/workbench/knowledge-map", { headers: scoped })
    ).json();
    expect(
      graph.edges.some(
        (edge: { label: string }) => edge.label === "requires rollback guide",
      ),
    ).toBe(true);
  });

  await test.step("Playbooks publishes a version-pinned checklist and records completion and approval", async () => {
    await page.goto(`${base}/playbooks`);
    await page
      .getByRole("button", { name: "Create playbook", exact: true })
      .first()
      .click();
    await page.getByLabel("Playbook name").fill("Juniper release checklist");
    await page
      .getByRole("combobox", { name: "Knowledge space", exact: true })
      .selectOption(space.id);
    await page.getByLabel("Publication status").selectOption("published");
    await page.getByLabel("Step 1 title").fill("Obtain release approvals");
    await page
      .getByLabel("Instructions", { exact: true })
      .fill("Obtain two Platform approvals and verify rollback.");
    await page
      .getByRole("combobox", { name: "Source document", exact: true })
      .selectOption(first.id);
    await page
      .getByRole("combobox", { name: "Source version", exact: true })
      .selectOption(second.version_id);
    await page.getByLabel("Require administrator approval").check();
    await page
      .getByRole("button", { name: "Save playbook", exact: true })
      .click();
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await page
      .getByRole("button", { name: "Start checklist", exact: true })
      .click();
    await page.getByLabel("Completed", { exact: true }).click();
    await expect(page.getByLabel("Completed", { exact: true })).toBeChecked();
    await page
      .getByRole("button", { name: "Approve step", exact: true })
      .click();
    await expect(page.getByRole("dialog")).toContainText(
      "1 / 1 steps complete",
    );
    await expect(
      page.getByRole("dialog").getByText("completed", { exact: true }),
    ).toBeVisible();
    await page
      .getByRole("button", { name: "Close dialog", exact: true })
      .click();
  });

  await test.step("Briefings saves schedules, renders current evidence, and pauses", async () => {
    await page.goto(`${base}/briefings`);
    await page
      .getByRole("button", { name: "Create briefing", exact: true })
      .first()
      .click();
    await page.getByLabel("Briefing name").fill("Juniper policy changes");
    await page
      .getByRole("combobox", { name: "Frequency", exact: true })
      .selectOption("daily");
    await page
      .getByRole("checkbox", { name: "Juniper release policy", exact: true })
      .check();
    await page
      .getByRole("button", { name: "Save briefing", exact: true })
      .click();
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await page.getByRole("button", { name: "Run now", exact: true }).click();
    await expect(page.getByRole("dialog")).toContainText("VIOLET-6942");
    await expect(
      page
        .getByRole("dialog")
        .getByRole("link", { name: "Juniper release policy · Version 2" }),
    ).toHaveAttribute("href", new RegExp(second.version_id));
    await page
      .getByRole("button", { name: "Close dialog", exact: true })
      .click();
    await page.getByRole("button", { name: "Pause", exact: true }).click();
    await expect(
      page.getByRole("button", { name: "Resume", exact: true }),
    ).toBeVisible();
    await page.reload();
    await expect(
      page.getByRole("button", { name: "Resume", exact: true }),
    ).toBeVisible();
  });

  await test.step("Publication change marks dependent playbooks stale and blocks new runs", async () => {
    await page.goto(`${base}/library/${first.id}?version=${second.version_id}`);
    await page
      .getByRole("button", { name: "Manage publication", exact: true })
      .click();
    await page.getByLabel("Publication status").selectOption("archived");
    await page
      .getByRole("button", { name: "Save publication", exact: true })
      .click();
    await expect(page.getByRole("dialog", { name: /Publication/ })).toHaveCount(
      0,
    );
    await page.goto(`${base}/playbooks`);
    await expect(
      page.getByText("Sources changed · review needed", { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Start checklist", exact: true }),
    ).toBeDisabled();
  });

  await test.step("Operations reports real scanner readiness and hides platform-only controls", async () => {
    await page.goto(`${base}/administration`);
    await page
      .getByRole("button", { name: "Operations & recovery", exact: true })
      .click();
    await expect(
      page.getByRole("heading", { name: "Ingestion jobs", exact: true }),
    ).toBeVisible();
    const status = await request.get("/api/operations", { headers: scoped });
    expect(status.ok()).toBeTruthy();
    const operational = await status.json();
    expect(operational.health.scanner.ready).toBe(true);
    expect(operational.platform_operator).toBe(false);
  });

  await test.step("A second company cannot open workflow evidence or document versions", async () => {
    const outsider = await browser.newContext({ baseURL: origin });
    try {
      const secondOwner = await verifiedOwner(outsider.request);
      const otherScope = {
        ...headers,
        "X-Atlas-Tenant": secondOwner.organization.id,
      };
      const books = await (
        await request.get("/api/workbench/playbooks", { headers: scoped })
      ).json();
      expect(
        (
          await outsider.request.get(
            `/api/workbench/playbooks/${books.items[0].id}`,
            { headers: otherScope },
          )
        ).status(),
      ).toBe(404);
      expect(
        (
          await outsider.request.get(
            `/api/library/${first.id}/versions/${second.version_id}`,
            { headers: otherScope },
          )
        ).status(),
      ).toBe(404);
      const privateBriefings = await (
        await outsider.request.get("/api/workbench/briefings", {
          headers: otherScope,
        })
      ).json();
      expect(privateBriefings.items).toEqual([]);
    } finally {
      await outsider.close();
    }
  });

  for (const tab of [
    "sources",
    "compare",
    "knowledge-map",
    "playbooks",
    "briefings",
  ]) {
    await page.goto(`${base}/${tab}`);
    await page.setViewportSize({ width: 390, height: 844 });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
  }
  expect(errors).toEqual([]);
});
