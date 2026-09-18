"""Run a verification command against the restored upgrade database, never the live database."""

import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit


def main():
    env = dict(os.environ)
    for line in Path(".local/upgrade-test.env").read_text().splitlines():
        key, value = line.split("=", 1)
        if key.endswith("DATABASE_URL") or key == "DATABASE_ADMIN_URL":
            if not urlsplit(value).path.startswith("/atlas_upgrade_verify_"):
                raise SystemExit("Refusing verification against a non-isolated database")
        env[key] = value
    if not sys.argv[1:]:
        raise SystemExit("Usage: python scripts/isolated.py COMMAND [ARGS]")
    raise SystemExit(subprocess.call(sys.argv[1:], env=env))


if __name__ == "__main__":
    main()
