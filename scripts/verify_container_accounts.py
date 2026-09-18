"""Verify the packaged UI and personal identity paths against a local test container."""

import json
import re
import secrets
import subprocess
from pathlib import Path
from uuid import uuid4

import httpx
from verify_upload_journey import checked, verification_token


def main():
    base = "http://127.0.0.1:8101"
    email = f"container-{uuid4().hex}@example.test"
    password = secrets.token_urlsafe(32)
    with httpx.Client(
        base_url=base, timeout=25, headers={"X-Atlas-Client": "console", "Origin": base}
    ) as client:
        checked(client.get("/health"))
        checked(client.get("/ready"))
        assert client.get("/login").text == Path("web/dist/index.html").read_text()
        bundle = re.search(r'src="([^"]+\.js)"', client.get("/").text)
        assert bundle and client.get(bundle[1]).status_code == 200
        checked(
            client.post(
                "/api/auth/register",
                json={"name": "Container verification", "email": email, "password": password},
            ),
            202,
        )
        checked(client.post("/api/auth/verify", json={"token": verification_token(client, email)}))
        login = client.post("/api/auth/login", json={"email": email, "password": password})
        checked(login)
        assert "HttpOnly" in login.headers["set-cookie"]
        organization = checked(
            client.post(
                "/api/organizations",
                json={"name": "Container verification", "slug": f"container-{uuid4().hex[:12]}"},
            ),
            201,
        )["organization"]
        client.headers["X-Atlas-Tenant"] = organization["id"]
        assert checked(client.get("/api/me"))["tenant_id"] == organization["id"]
        assert checked(client.get("/api/spaces"))[0]["name"] == "General"
        checked(client.post("/api/auth/logout"))
        assert client.get("/api/me").status_code == 401
    container = "atlas-upgrade-verification"
    uid = subprocess.check_output(["docker", "exec", container, "id", "-u"], text=True).strip()
    assert uid == "10001"
    image = subprocess.check_output(
        ["docker", "inspect", container, "--format", "{{.Image}}"], text=True
    ).strip()
    result = {
        "result": "passed",
        "image": image,
        "runtime_uid": int(uid),
        "checks": [
            "health and database/queue readiness",
            "exact final host-built HTML, real JavaScript asset and deep route",
            "registration and local Mailpit verification",
            "personal login with HttpOnly session cookie",
            "company onboarding, tenant context and default knowledge space",
            "logout invalidates authentication",
        ],
        "generation_calls": 0,
        "api_cost_usd": 0,
    }
    Path("artifacts/upgrade/container-accounts.json").write_text(
        json.dumps(result, indent=2) + "\n"
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
