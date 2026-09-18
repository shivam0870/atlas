import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { WorkspaceContext } from "../app/context";
import { Button, Field, Modal } from "./ui";
import { EvidenceDialog } from "./Evidence";
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
describe("accessible shared controls", () => {
  it("labels a failed field and connects its error message", () => {
    render(
      <Field label="Workspace name" error="Enter a name" defaultValue="" />,
    );
    const input = screen.getByLabelText("Workspace name");
    expect(input.getAttribute("aria-invalid")).toBe("true");
    const description = document.getElementById(
      input.getAttribute("aria-describedby")!,
    );
    expect(description?.textContent).toBe("Enter a name");
  });
  it("traps keyboard focus, closes with Escape and restores the external trigger", async () => {
    const user = userEvent.setup();
    function Harness() {
      const [open, setOpen] = useState(false);
      return (
        <>
          <Button onClick={() => setOpen(true)}>Open settings</Button>
          <Modal
            open={open}
            onOpenChange={setOpen}
            title="Settings"
            description="Update your workspace"
          >
            <Field label="Name" />
            <Button onClick={() => setOpen(false)}>Save</Button>
          </Modal>
          <button>Outside dialog</button>
        </>
      );
    }
    render(<Harness />);
    const trigger = screen.getByRole("button", { name: "Open settings" });
    await user.click(trigger);
    const dialog = screen.getByRole("dialog", { name: "Settings" });
    await waitFor(() =>
      expect(dialog.contains(document.activeElement)).toBe(true),
    );
    for (let n = 0; n < 7; n++) {
      await user.tab();
      expect(dialog.contains(document.activeElement)).toBe(true);
    }
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await waitFor(() => expect(document.activeElement).toBe(trigger));
  });
  it("prevents duplicate submissions while an action is pending", async () => {
    const action = vi.fn();
    render(
      <Button busy onClick={action}>
        Save changes
      </Button>,
    );
    await userEvent.click(screen.getByRole("button", { name: "Save changes" }));
    expect(action).not.toHaveBeenCalled();
    expect(
      (
        screen.getByRole("button", {
          name: "Save changes",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(true);
  });
});
it("highlights immutable-version evidence using Unicode codepoint offsets", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          version_content: "A🚀release codeZ",
          start_offset: 2,
          end_offset: 14,
          version_number: 1,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    ),
  );
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const context = {
    tenantId: "tenant-a",
    organization: {
      id: "tenant-a",
      name: "Test company",
      slug: "test",
      role: "viewer" as const,
      auth_revision: 4,
      kind: "company" as const,
    },
    user: {
      id: "user-a",
      name: "Test Person",
      email: "test@example.test",
      email_verified: true,
      mfa_enabled: false,
      theme: "light" as const,
    },
    canManage: false,
    canEdit: false,
  };
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <WorkspaceContext.Provider value={context}>
          <EvidenceDialog
            source={{
              id: "chunk-a",
              document_id: "doc-a",
              title: "Release notes",
              content: "release code",
              start_offset: 2,
              end_offset: 14,
              version_number: 1,
            }}
            onClose={() => {}}
          />
        </WorkspaceContext.Provider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  await waitFor(() =>
    expect(
      screen.getByTestId("source-preview").querySelector("mark")?.textContent,
    ).toBe("release code"),
  );
  expect(screen.getByText("Version 1")).not.toBeNull();
  client.clear();
});

it("does not expose a cached source while renewed access is pending or denied", async () => {
  let finish: ((value: Response) => void) | undefined;
  vi.stubGlobal(
    "fetch",
    vi.fn(
      () =>
        new Promise<Response>((resolve) => {
          finish = resolve;
        }),
    ),
  );
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  client.setQueryData(
    ["workspace", "user-a", "tenant-a", 4, "evidence", "chunk-a", undefined],
    {
      version_content: "Previously accessible secret",
      start_offset: 0,
      end_offset: 28,
    },
  );
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
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
          <EvidenceDialog
            source={{
              id: "chunk-a",
              title: "Source",
              content: "Previously accessible secret",
            }}
            onClose={() => {}}
          />
        </WorkspaceContext.Provider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  expect(screen.queryByTestId("source-preview")).toBeNull();
  expect(screen.getByText("Checking source access…")).not.toBeNull();
  finish!(
    new Response(JSON.stringify({ detail: "Source access revoked" }), {
      status: 404,
    }),
  );
  await waitFor(() =>
    expect(screen.getByRole("alert").textContent).toContain(
      "Source access revoked",
    ),
  );
  expect(screen.queryByText("Previously accessible secret")).toBeNull();
  expect(screen.queryByTestId("source-preview")).toBeNull();
  client.clear();
});
