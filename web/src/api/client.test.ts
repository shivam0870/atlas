import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiError, json } from "./client";
afterEach(() => vi.unstubAllGlobals());
describe("API authentication and request isolation", () => {
  it("uses cookie sessions and the explicit tenant for each request", async () => {
    const fetcher = vi
      .fn()
      .mockImplementation(
        async () => new Response(JSON.stringify({ ok: true }), { status: 200 }),
      );
    vi.stubGlobal("fetch", fetcher);
    await api("/library", { tenantId: "tenant-a" });
    await api("/library", {
      tenantId: "tenant-b",
      method: "POST",
      body: json({ title: "Sample" }),
    });
    for (const [index, tenant] of ["tenant-a", "tenant-b"].entries()) {
      const request = fetcher.mock.calls[index][1] as RequestInit;
      expect(request.credentials).toBe("include");
      expect(new Headers(request.headers).get("X-Atlas-Tenant")).toBe(tenant);
      expect(new Headers(request.headers).get("X-Atlas-Client")).toBe(
        "console",
      );
      expect(new Headers(request.headers).get("Authorization")).toBeNull();
    }
    expect(
      new Headers(fetcher.mock.calls[1][1].headers).get("Content-Type"),
    ).toBe("application/json");
  });
  it("passes cancellation through to fetch and leaves multipart boundaries to the browser", async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetcher);
    const controller = new AbortController();
    const body = new FormData();
    body.append("file", new Blob(["sample"]), "sample.txt");
    await api("/library/upload", {
      tenantId: "tenant-a",
      body,
      method: "POST",
      signal: controller.signal,
    });
    const options = fetcher.mock.calls[0][1];
    expect(options.signal).toBe(controller.signal);
    expect(new Headers(options.headers).has("Content-Type")).toBe(false);
  });
  it("reports validation problems without treating an unsuccessful response as data", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            detail: [
              { msg: "A name is required" },
              { msg: "Choose a valid space" },
            ],
          }),
          { status: 422 },
        ),
      ),
    );
    await expect(
      api("/library", { tenantId: "tenant-a" }),
    ).rejects.toMatchObject({
      status: 422,
      message: "A name is required; Choose a valid space",
    });
  });
  it("signals session loss for content requests but preserves login error handling", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(
        async () =>
          new Response(JSON.stringify({ detail: "Sign in again" }), {
            status: 401,
          }),
      ),
    );
    const expired = vi.fn();
    window.addEventListener("atlas:session-expired", expired);
    await expect(api("/library")).rejects.toBeInstanceOf(ApiError);
    expect(expired).toHaveBeenCalledTimes(1);
    await expect(api("/auth/login")).rejects.toBeInstanceOf(ApiError);
    expect(expired).toHaveBeenCalledTimes(1);
    window.removeEventListener("atlas:session-expired", expired);
  });
});
