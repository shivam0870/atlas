import { expect, type APIRequestContext } from "@playwright/test";
const origin = "http://127.0.0.1:8100";
const headers = { "X-Atlas-Client": "console", Origin: origin };
export async function verifiedOwner(request: APIRequestContext) {
  const id = Date.now();
  const email = `atlas-admin-${id}@example.test`;
  const password = "Atlas administration browser phrase 9472!"; // pragma: allowlist secret -- disposable test account
  expect(
    (
      await request.post("/api/auth/register", {
        headers,
        data: { name: "Administration Owner", email, password },
      })
    ).status(),
  ).toBe(202);
  let token = "";
  await expect
    .poll(async () => {
      const list = await (
        await request.get("http://127.0.0.1:58025/api/v1/messages")
      ).json();
      for (const message of list.messages || []) {
        if (
          !message.To?.some(
            (recipient: { Address: string }) => recipient.Address === email,
          )
        )
          continue;
        const detail = await (
          await request.get(
            `http://127.0.0.1:58025/api/v1/message/${message.ID}`,
          )
        ).json();
        const link = String(detail.Text || "").match(
          /http:\/\/[^\s]+\/verify-email[^\s]*/,
        )?.[0];
        if (link) {
          token = new URL(link).searchParams.get("token") || "";
          return !!token;
        }
      }
      return false;
    })
    .toBe(true);
  expect(
    (await request.post("/api/auth/verify", { headers, data: { token } })).ok(),
  ).toBeTruthy();
  expect(
    (
      await request.post("/api/auth/login", {
        headers,
        data: { email, password },
      })
    ).ok(),
  ).toBeTruthy();
  const created = await request.post("/api/organizations", {
    headers,
    data: {
      name: "Administration Browser Company",
      slug: `admin-browser-${id}`,
    },
  });
  expect(created.ok()).toBeTruthy();
  const organization = (await created.json()).organization;
  return { email, password, organization };
}
