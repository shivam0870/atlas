import { test, expect } from "@playwright/test";
import { verifiedOwner } from "./helpers";

const origin = process.env.ATLAS_BROWSER_URL || "http://127.0.0.1:8100";

test("real malware scanner rejects the harmless EICAR test signature without creating a document", async ({
  request,
}) => {
  const owner = await verifiedOwner(request);
  const headers = {
    "X-Atlas-Client": "console",
    Origin: origin,
    "X-Atlas-Tenant": owner.organization.id,
  };
  // EICAR is an inert industry scanner test string, never an executable payload.
  const signature = Buffer.from(
    "X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*",
  );
  const before = await (await request.get("/api/library", { headers })).json();
  const rejected = await request.post("/api/library/upload", {
    headers,
    multipart: {
      file: {
        name: "scanner-test.txt",
        mimeType: "text/plain",
        buffer: signature,
      },
    },
  });
  expect(rejected.status()).toBe(422);
  expect((await rejected.json()).detail).toContain("malware scanner rejected");
  const after = await (await request.get("/api/library", { headers })).json();
  expect(after.total).toBe(before.total);
  expect(
    after.items.some(
      (item: { title: string }) => item.title === "scanner-test.txt",
    ),
  ).toBe(false);
  const invalid = await request.post("/api/library/upload", {
    headers,
    multipart: {
      file: {
        name: "untrusted.exe",
        mimeType: "application/octet-stream",
        buffer: Buffer.from("Untrusted executable type"),
      },
    },
  });
  expect([415, 422]).toContain(invalid.status());
});
