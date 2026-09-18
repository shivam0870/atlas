import { afterEach, expect, it, vi } from "vitest";
import {
  cleanup,
  render,
  screen,
  within,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { WorkspaceContext } from "../app/context";
import { BudgetSummary, WorkspaceHealth } from "./WorkspaceMetrics";
import { RequestDiagnostics } from "./RequestDiagnostics";
import { EffectiveAccess } from "./EffectiveAccess";
import { LibraryPage } from "./Library";
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
it("includes active reservations in monthly quota utilization and available tokens", () => {
  render(
    <BudgetSummary
      budget={{
        limits: { monthly_tokens: 1000, requests_per_minute: 45 },
        usage: { period: "2026-09-01", used_tokens: 600, reserved_tokens: 250 },
      }}
    />,
  );
  expect(screen.getByText("85.0% allocated")).not.toBeNull();
  expect(screen.getByText("150 tokens")).not.toBeNull();
  const meter = screen.getByRole("progressbar", {
    name: "Monthly token quota utilization",
  });
  expect(meter.getAttribute("max")).toBe("1000");
  expect(meter.getAttribute("value")).toBe("850");
});
it("reports a zero token limit as blocked rather than unlimited or an invalid percentage", () => {
  render(
    <BudgetSummary
      budget={{
        limits: { monthly_tokens: 0, requests_per_minute: 45 },
        usage: null,
      }}
    />,
  );
  expect(screen.getByText("No tokens allowed")).not.toBeNull();
  expect(screen.queryByText(/Infinity|NaN/)).toBeNull();
});
it("opens accessible diagnostics with recorded retrieval ranks and sanitized failure stage", async () => {
  render(
    <RequestDiagnostics
      message={{
        id: "message-a",
        status: "failed",
        metadata: {
          failure_stage: "generating",
          error_code: "timeout",
          duration_ms: 1234,
          first_token_ms: 250,
          input_tokens: 100,
          output_tokens: 8,
          cache_hit: false,
          steps: [{ tool: "search_documents", error: "ToolTimeout" }],
        },
        sources: [
          {
            id: "chunk-a",
            title: "Release guide",
            content: "Excerpt",
            version_number: 2,
            vector_rank: 3,
            lexical_rank: 1,
            score: 0.024,
            rerank_score: 0.75,
          },
        ],
      }}
    />,
  );
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Request diagnostics" }));
  const dialog = screen.getByRole("dialog", { name: "Request diagnostics" });
  expect(within(dialog).getByText("generating")).not.toBeNull();
  expect(within(dialog).getByText("timeout")).not.toBeNull();
  const row = within(dialog).getByRole("row", { name: /Release guide/ });
  expect(
    within(row)
      .getAllByRole("cell")
      .map((cell) => cell.textContent),
  ).toEqual(["1", "Release guideVersion 2", "3", "1", "0.024", "0.75"]);
  await user.keyboard("{Escape}");
  expect(screen.queryByRole("dialog")).toBeNull();
});
it("does not request company health for a non-administrator", async () => {
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <WorkspaceContext.Provider
          value={{
            tenantId: "tenant-a",
            user: {
              id: "user-a",
              name: "Viewer",
              email: "a@example.test",
              email_verified: true,
              mfa_enabled: false,
              theme: "light",
            },
            organization: {
              id: "tenant-a",
              name: "Company",
              slug: "company",
              role: "viewer",
              auth_revision: 4,
              kind: "company",
            },
            canManage: false,
            canEdit: false,
          }}
        >
          <WorkspaceHealth />
        </WorkspaceContext.Provider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  expect(
    screen.queryByRole("heading", { name: "Workspace health" }),
  ).toBeNull();
  expect(fetcher).not.toHaveBeenCalled();
  client.clear();
});

it("shows server-effective access independently of configured write grants", () => {
  render(
    <EffectiveAccess
      items={[
        {
          subject_type: "user",
          subject_id: "viewer-a",
          name: "Casey Viewer",
          role: "viewer",
          can_read: true,
          can_edit: false,
          reasons: [
            "Write grant is capped by the viewer role",
            "Inherited through Research team",
          ],
        },
        {
          subject_type: "service",
          subject_id: "service-a",
          name: "Research connector",
          role: null,
          can_read: false,
          can_edit: false,
          reasons: ["No active credential with a read scope"],
        },
        {
          subject_type: "user",
          subject_id: "owner-a",
          name: "Company Owner",
          role: "owner",
          can_read: true,
          can_edit: true,
          reasons: ["Company owner override"],
        },
      ]}
    />,
  );
  expect(screen.getByText(/2 readers · 1 editor/)).not.toBeNull();
  const viewer = screen.getByRole("row", { name: /Casey Viewer/ });
  expect(
    within(viewer)
      .getAllByRole("cell")
      .slice(1, 3)
      .map((cell) => cell.textContent),
  ).toEqual(["Allowed", "Denied"]);
  expect(
    within(viewer).getByText("Write grant is capped by the viewer role"),
  ).not.toBeNull();
  const service = screen.getByRole("row", { name: /Research connector/ });
  expect(within(service).getAllByText("Denied")).toHaveLength(2);
  expect(
    within(service).getByText("No active credential with a read scope"),
  ).not.toBeNull();
  expect(
    screen.getByText(/Company owners and administrators retain access/),
  ).not.toBeNull();
});

it("offers readable document owners to viewers without requesting the member directory", async () => {
  const requested: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string | URL | Request) => {
      const path = String(input);
      requested.push(path);
      const body = path.startsWith("/api/library/owners")
        ? { items: [{ user_id: "owner-b", name: "Readable Document Owner" }] }
        : path.startsWith("/api/spaces")
          ? []
          : { items: [], total: 0 };
      return new Response(JSON.stringify(body), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }),
  );
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/library?space=space-a"]}>
        <WorkspaceContext.Provider
          value={{
            tenantId: "tenant-a",
            user: {
              id: "viewer-a",
              name: "Viewer",
              email: "viewer@example.test",
              email_verified: true,
              mfa_enabled: false,
              theme: "light",
            },
            organization: {
              id: "tenant-a",
              name: "Company",
              slug: "company",
              role: "viewer",
              auth_revision: 2,
              kind: "company",
            },
            canEdit: false,
            canManage: false,
          }}
        >
          <LibraryPage />
        </WorkspaceContext.Provider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  await userEvent.click(screen.getByText("More filters"));
  await screen.findByRole("option", { name: "Readable Document Owner" });
  await userEvent.selectOptions(
    screen.getByLabelText("Owner", { exact: true }),
    "owner-b",
  );
  await waitFor(() =>
    expect(
      requested.some((path) => path.includes("owner_user_id=owner-b")),
    ).toBe(true),
  );
  expect(
    requested.some((path) =>
      path.includes("/library/owners?lifecycle=active&space_id=space-a"),
    ),
  ).toBe(true);
  expect(
    requested.some(
      (path) =>
        path.includes("/members") ||
        path.includes("/teams") ||
        path.includes("/integrations"),
    ),
  ).toBe(false);
  client.clear();
});
