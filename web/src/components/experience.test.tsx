import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useLayoutEffect } from "react";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { captureCampaign, ThemeToggle } from "./experience";
import { Field } from "./ui";

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  document.documentElement.dataset.theme = "light";
});
afterEach(cleanup);

it("records only approved campaign fields after consent and clears them on opt-out", () => {
  const search =
    "?utm_source=resume&utm_campaign=atlas&token=private-link&email=private@example.test&next=/secret";
  captureCampaign(search, false);
  expect(sessionStorage.getItem("atlas-campaign")).toBeNull();
  captureCampaign(search, true);
  expect(JSON.parse(sessionStorage.getItem("atlas-campaign")!)).toEqual({
    utm_source: "resume",
    utm_campaign: "atlas",
  });
  captureCampaign("?token=another-private-link", true);
  expect(sessionStorage.getItem("atlas-campaign")).not.toContain("private");
  captureCampaign("", false);
  expect(sessionStorage.getItem("atlas-campaign")).toBeNull();
});

it("bounds campaign values and keeps navigation working when storage is blocked", () => {
  captureCampaign(`?utm_medium=%00%0A${"x".repeat(300)}`, true);
  expect(JSON.parse(sessionStorage.getItem("atlas-campaign")!).utm_medium).toBe(
    "x".repeat(120),
  );
  const set = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    throw new Error("Storage blocked");
  });
  expect(() => captureCampaign("?utm_source=resume", true)).not.toThrow();
  set.mockRestore();
});

it("shows and hides a password without submitting the surrounding form", async () => {
  const user = userEvent.setup();
  const submit = vi.fn((event) => event.preventDefault());
  render(
    <form onSubmit={submit}>
      <Field type="password" label="Password" defaultValue="test-only-value" />
      <button type="submit">Sign in</button>
    </form>,
  );
  const field = screen.getByLabelText("Password") as HTMLInputElement;
  await user.click(screen.getByRole("button", { name: "Show password" }));
  expect(field.type).toBe("text");
  expect(field.value).toBe("test-only-value");
  expect(submit).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "Hide password" }));
  expect(field.type).toBe("password");
  await user.click(screen.getByRole("button", { name: "Sign in" }));
  expect(submit).toHaveBeenCalledTimes(1);
});

it("persists theme changes and exposes the next action to assistive technology", async () => {
  const user = userEvent.setup();
  render(<ThemeToggle />);
  await user.click(screen.getByRole("button", { name: "Switch to dark mode" }));
  expect(document.documentElement.dataset.theme).toBe("dark");
  expect(localStorage.getItem("atlas-theme")).toBe("dark");
  await user.click(
    screen.getByRole("button", { name: "Switch to light mode" }),
  );
  expect(document.documentElement.dataset.theme).toBe("light");
  expect(localStorage.getItem("atlas-theme")).toBe("light");
});

it("synchronizes the toggle when the saved theme is applied during mounting", () => {
  function SavedTheme() {
    useLayoutEffect(() => {
      document.documentElement.dataset.theme = "dark";
    }, []);
    return <ThemeToggle />;
  }
  render(<SavedTheme />);
  expect(
    screen.getByRole("button", { name: "Switch to light mode" }),
  ).not.toBeNull();
});
