import { afterEach, expect, it, vi } from "vitest";
import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import type { ReactNode } from "react";
import { WorkspaceContext } from "../app/context";
import { SourcesPage } from "./Sources";
import { ComparePage, ComparisonResult, type Comparison } from "./Compare";
import { BriefingsPage } from "./Briefings";
import { OperationsPanel } from "./Operations";
import { KnowledgeMapPage } from "./KnowledgeMap";
import { PlaybooksPage } from "./Playbooks";
const clients: QueryClient[] = [];
afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
  vi.unstubAllGlobals();
});
function mount(node: ReactNode, role: "admin" | "viewer" = "admin") {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  clients.push(client);
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <WorkspaceContext.Provider
          value={{
            tenantId: "tenant-a",
            user: {
              id: "user-a",
              name: "Alex",
              email: "alex@example.test",
              email_verified: true,
              mfa_enabled: true,
              theme: "light",
            },
            organization: {
              id: "tenant-a",
              name: "Company",
              slug: "company",
              role,
              auth_revision: 7,
              kind: "company",
            },
            canEdit: role !== "viewer",
            canManage: role === "admin",
          }}
        >
          {node}
        </WorkspaceContext.Provider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}
const source = {
  id: "source-a",
  name: "Engineering docs",
  kind: "github",
  location: "https://github.com/acme/docs",
  space_id: "space-a",
  include_patterns: ["**/*.md"],
  interval_hours: 24,
  enabled: true,
};
const docs = {
  items: [
    {
      id: "doc-a",
      title: "Release policy",
      current_version_id: "v2",
      space_id: "space-a",
    },
  ],
  total: 1,
};
const versions = [
  {
    id: "v1",
    title: "Release policy",
    number: 1,
    status: "ready",
    publication_status: "superseded",
  },
  {
    id: "v2",
    title: "Release policy",
    number: 2,
    status: "ready",
    publication_status: "published",
  },
];
const comparison: Comparison = {
  left: {
    document_id: "doc-a",
    version_id: "v1",
    title: "Release policy",
    number: 1,
    publication_status: "superseded",
  },
  right: {
    document_id: "doc-a",
    version_id: "v2",
    title: "Release policy",
    number: 2,
    publication_status: "published",
  },
  changes: [
    {
      kind: "replace",
      left_start: 0,
      left_end: 1,
      right_start: 0,
      right_end: 1,
      left_text: "One approval required.",
      right_text: "Two approvals required.",
    },
  ],
  summary: { added_lines: 1, removed_lines: 1, changed_sections: 1 },
  warnings: ["The left source is superseded."],
  truncated: false,
};
function mockApi(resolve: (path: string, init?: RequestInit) => unknown) {
  const fetcher = vi.fn(
    async (path: string, init?: RequestInit) =>
      new Response(JSON.stringify(resolve(path, init)), { status: 200 }),
  );
  vi.stubGlobal("fetch", fetcher);
  return fetcher;
}
it("hides source mutations from viewers while preserving preview and authorized history", async () => {
  const fetcher = mockApi((path) =>
    path === "/api/spaces"
      ? [{ id: "space-a", name: "Engineering" }]
      : { items: [source] },
  );
  mount(<SourcesPage />, "viewer");
  await screen.findByRole("heading", { name: "Engineering docs" });
  expect(screen.queryByRole("button", { name: "Connect source" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Sync now" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Disconnect" })).toBeNull();
  expect(screen.getByRole("button", { name: "Preview files" })).not.toBeNull();
  expect(fetcher.mock.calls.every(([, init]) => !init?.method)).toBe(true);
});
it("shows comparison evidence as text and links the exact versions", () => {
  mount(
    <ComparisonResult
      result={{
        ...comparison,
        changes: [
          {
            ...comparison.changes[0],
            right_text: '<script>alert("untrusted")</script>',
          },
        ],
        truncated: true,
      }}
    />,
  );
  expect(
    screen.getByText('<script>alert("untrusted")</script>'),
  ).not.toBeNull();
  expect(document.querySelector("script")).toBeNull();
  expect(screen.getByText(/comparison is truncated/)).not.toBeNull();
  expect(
    screen
      .getAllByRole("link", { name: "Release policy" })
      .map((link) => link.getAttribute("href")),
  ).toEqual([
    "/o/tenant-a/library/doc-a?version=v1",
    "/o/tenant-a/library/doc-a?version=v2",
  ]);
  expect(screen.getByText("The left source is superseded.")).not.toBeNull();
});
it("sends both selected version IDs to compare and discards stale results on selection changes", async () => {
  const fetcher = mockApi((path) =>
    path.startsWith("/api/library?")
      ? docs
      : path.endsWith("/versions")
        ? versions
        : comparison,
  );
  mount(<ComparePage />);
  const user = userEvent.setup();
  await user.selectOptions(
    await screen.findByRole("combobox", { name: "Left document" }),
    "doc-a",
  );
  await user.selectOptions(
    screen.getByRole("combobox", { name: "Right document" }),
    "doc-a",
  );
  await waitFor(() =>
    expect(
      screen.getAllByRole("option", { name: "Version 1 · superseded" }).length,
    ).toBe(2),
  );
  await user.selectOptions(
    screen.getByRole("combobox", { name: "Left version" }),
    "v1",
  );
  await user.selectOptions(
    screen.getByRole("combobox", { name: "Right version" }),
    "v2",
  );
  await user.click(screen.getByRole("button", { name: "Compare sources" }));
  await screen.findByText("Two approvals required.");
  const call = fetcher.mock.calls.find(
    ([path]) => path === "/api/workbench/compare",
  );
  expect(JSON.parse(String(call?.[1]?.body))).toEqual({
    left_document_id: "doc-a",
    right_document_id: "doc-a",
    left_version_id: "v1",
    right_version_id: "v2",
  });
  expect(new Headers(call?.[1]?.headers).get("X-Atlas-Tenant")).toBe(
    "tenant-a",
  );
  await user.selectOptions(
    screen.getByRole("combobox", { name: "Right version" }),
    "v1",
  );
  expect(screen.queryByText("Two approvals required.")).toBeNull();
});
it("requires at least one authorized source when creating a briefing", async () => {
  const fetcher = mockApi((path) =>
    path === "/api/spaces"
      ? [{ id: "space-a", name: "Engineering" }]
      : path.startsWith("/api/library?")
        ? docs
        : { items: [] },
  );
  mount(<BriefingsPage />, "viewer");
  const user = userEvent.setup();
  await user.click(
    (await screen.findAllByRole("button", { name: "Create briefing" }))[0],
  );
  const dialog = screen.getByRole("dialog", { name: "Create briefing" });
  const save = within(dialog).getByRole("button", { name: "Save briefing" });
  expect((save as HTMLButtonElement).disabled).toBe(true);
  await user.type(
    within(dialog).getByRole("textbox", { name: "Briefing name" }),
    "Weekly policy changes",
  );
  await user.click(
    within(dialog).getByRole("checkbox", { name: "Release policy" }),
  );
  expect((save as HTMLButtonElement).disabled).toBe(false);
  await user.click(save);
  await screen.findByText("Briefing saved.");
  const call = fetcher.mock.calls.find(
    ([path, init]) =>
      path === "/api/workbench/briefings" && init?.method === "POST",
  );
  expect(JSON.parse(String(call?.[1]?.body))).toEqual({
    name: "Weekly policy changes",
    question: "",
    cadence: "weekly",
    enabled: true,
    space_ids: [],
    document_ids: ["doc-a"],
  });
});
it("does not request operations or jobs for a viewer", () => {
  const fetcher = mockApi(() => ({}));
  mount(<OperationsPanel />, "viewer");
  expect(
    screen.getByRole("heading", { name: "Management access required" }),
  ).not.toBeNull();
  expect(fetcher).not.toHaveBeenCalled();
});
it("offers keyboard inspection of map nodes and keeps the complete list available", async () => {
  mockApi(() => ({
    nodes: [
      {
        id: "entity:service-a",
        resource_id: "service-a",
        kind: "entity",
        label: "Checkout",
      },
      {
        id: "document:doc-a",
        resource_id: "doc-a",
        kind: "document",
        label: "Release policy",
      },
    ],
    edges: [
      {
        id: "edge-a",
        source: "entity:service-a",
        target: "document:doc-a",
        label: "documented by",
        editable: false,
      },
    ],
  }));
  mount(<KnowledgeMapPage />, "viewer");
  const user = userEvent.setup();
  const node = await screen.findByRole("button", { name: "Inspect Checkout" });
  node.focus();
  await user.keyboard("{Enter}");
  expect(screen.getByRole("region", { name: "Selected node" })).not.toBeNull();
  expect(screen.getByText("documented by")).not.toBeNull();
  expect(
    screen
      .getByRole("link", { name: "Open inventory record" })
      .getAttribute("href"),
  ).toBe("/o/tenant-a/inventory?record=service-a");
  expect(screen.queryByRole("button", { name: "Add relationship" })).toBeNull();
  expect(
    screen.queryByRole("button", { name: "Remove relationship" }),
  ).toBeNull();
});
it("blocks checklist starts for drafts and playbooks with changed evidence", async () => {
  mockApi((path) =>
    path === "/api/spaces"
      ? []
      : path.startsWith("/api/library?")
        ? docs
        : {
            items: [
              {
                id: "draft",
                name: "Draft checklist",
                description: "",
                space_id: "space-a",
                status: "draft",
                steps: [],
              },
              {
                id: "stale",
                name: "Stale checklist",
                description: "",
                space_id: "space-a",
                status: "published",
                needs_review: true,
                steps: [],
              },
            ],
          },
  );
  mount(<PlaybooksPage />, "viewer");
  await screen.findByRole("heading", { name: "Stale checklist" });
  expect(
    screen
      .getAllByRole("button", { name: "Start checklist" })
      .every((button) => (button as HTMLButtonElement).disabled),
  ).toBe(true);
  expect(screen.queryByRole("button", { name: "Create playbook" })).toBeNull();
});

it("removes previously loaded source details when an authorization refresh fails", async () => {
  let revoked = false;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string) => {
      if (path === "/api/spaces") return new Response(JSON.stringify([]));
      return revoked
        ? new Response(JSON.stringify({ detail: "Access revoked" }), {
            status: 403,
          })
        : new Response(JSON.stringify({ items: [source] }));
    }),
  );
  mount(<SourcesPage />, "viewer");
  await screen.findByRole("heading", { name: "Engineering docs" });
  revoked = true;
  await clients.at(-1)!.invalidateQueries();
  await screen.findByText("Access revoked");
  expect(
    screen.queryByRole("heading", { name: "Engineering docs" }),
  ).toBeNull();
});
