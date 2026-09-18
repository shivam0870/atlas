import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { WorkspaceContext } from "../app/context";
import { ConversationsPage } from "./Conversations";
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
it("loads older authorized messages using a scoped URL cursor and returns to the latest composer", async () => {
  vi.stubGlobal(
    "matchMedia",
    vi.fn(() => ({
      matches: false,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  );
  Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
    configurable: true,
    value: vi.fn(),
  });
  const thread = {
    id: "thread-a",
    title: "Private thread",
    pinned: false,
    archived: false,
    space_ids: [],
    document_ids: [],
    created_at: "2026-09-17T00:00:00Z",
    updated_at: "2026-09-17T00:00:00Z",
  };
  const fetcher = vi.fn(async (input: string) => {
    let data: unknown = [];
    if (input.startsWith("/api/conversations?")) data = { items: [thread] };
    else if (input.startsWith("/api/conversations/thread-a?")) {
      const older = input.includes("before=message-101");
      data = {
        conversation: thread,
        messages: [
          {
            id: older ? "message-1" : "message-101",
            role: "user",
            content: older ? "Earlier question" : "Latest question",
            status: "completed",
            sources: [],
            metadata: {},
            created_at: "2026-09-17T00:00:00Z",
          },
        ],
        pagination: {
          has_older: !older,
          before: older ? "message-1" : "message-101",
        },
      };
    }
    return new Response(JSON.stringify(data), { status: 200 });
  });
  vi.stubGlobal("fetch", fetcher);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  function Page() {
    const location = useLocation();
    return (
      <>
        <span data-testid="current-search">{location.search}</span>
        <ConversationsPage />
      </>
    );
  }
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/o/tenant-a/ask/thread-a"]}>
        <WorkspaceContext.Provider
          value={{
            tenantId: "tenant-a",
            user: {
              id: "user-a",
              name: "Test",
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
          <Routes>
            <Route path="/o/:tenantId/ask/:conversationId" element={<Page />} />
          </Routes>
        </WorkspaceContext.Provider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  expect(await screen.findByText("Latest question")).not.toBeNull();
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Older messages" }));
  expect(await screen.findByText("Earlier question")).not.toBeNull();
  expect(screen.queryByText("Latest question")).toBeNull();
  expect(screen.getByTestId("current-search").textContent).toBe(
    "?before=message-101",
  );
  expect(screen.queryByLabelText("Your question")).toBeNull();
  await user.click(screen.getByRole("button", { name: "Latest messages" }));
  expect(await screen.findByText("Latest question")).not.toBeNull();
  expect(screen.queryByText("Earlier question")).toBeNull();
  expect(screen.getByLabelText("Your question")).not.toBeNull();
  await waitFor(() =>
    expect(
      fetcher.mock.calls.some(([path]) => path.includes("before=message-101")),
    ).toBe(true),
  );
  expect(
    client
      .getQueryCache()
      .getAll()
      .filter((query) => query.queryKey.includes("conversation"))
      .every(
        (query) =>
          query.queryKey[1] === "user-a" &&
          query.queryKey[2] === "tenant-a" &&
          query.queryKey[3] === 4,
      ),
  ).toBe(true);
  client.clear();
});
