"""Generate local-only credentials without writing them to logs or source control."""

import base64
import os
import secrets
from pathlib import Path

root = Path(__file__).resolve().parents[1]
path = root / ".env"
if not path.exists():
    admin, app = secrets.token_hex(24), secrets.token_hex(24)
    values = {
        "POSTGRES_PASSWORD": admin,
        "APP_DB_PASSWORD": app,
        "DATABASE_ADMIN_URL": f"postgresql://atlas_admin:{admin}@127.0.0.1:55432/atlas",
        "DATABASE_URL": f"postgresql://atlas_app:{app}@127.0.0.1:55432/atlas",
        "REDIS_URL": "redis://127.0.0.1:56379/0",
        "LOCAL_CONSOLE": "true",
        "PAID_PROVIDERS_ENABLED": "false",
        "GLOBAL_PAID_SPEND_LIMIT_USD": "0",
        "OTEL_ENABLED": "false",
    }
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as out:
        out.write("\n".join(f"{k}={v}" for k, v in values.items()) + "\n")
    print("Created private local configuration in .env")
else:
    print("Existing .env preserved")
existing = dict(
    line.split("=", 1)
    for line in path.read_text().splitlines()
    if "=" in line and not line.startswith("#")
)
worker = existing.get("WORKER_DB_PASSWORD", secrets.token_hex(24))
identity = existing.get("IDENTITY_DB_PASSWORD", secrets.token_hex(24))
additions = {
    "LOCAL_UID": str(os.getuid()),
    "WORKER_DB_PASSWORD": worker,
    "WORKER_DATABASE_URL": f"postgresql://atlas_worker:{worker}@127.0.0.1:55432/atlas",
    "CACHE_REDIS_URL": "redis://127.0.0.1:56380/0",
    "IDENTITY_DB_PASSWORD": identity,
    "IDENTITY_DATABASE_URL": f"postgresql://atlas_identity:{identity}@127.0.0.1:55432/atlas",
    "MFA_ENCRYPTION_KEY": base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
    "APP_URL": "http://127.0.0.1:8100",
    "SMTP_HOST": "127.0.0.1",
    "SMTP_PORT": "51025",
}
with path.open("a") as out:
    for key, value in additions.items():
        if key not in existing:
            out.write(f"{key}={value}\n")
path.chmod(0o600)

telemetry = root / ".local/telemetry"
telemetry.mkdir(parents=True, exist_ok=True)
telemetry.chmod(0o700)
