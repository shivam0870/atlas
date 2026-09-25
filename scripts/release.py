"""Verify and record immutable release manifests; deploy or roll back a verified image by digest."""

import argparse
import hashlib
import json
import os
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb

from atlas.config import settings

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_CHECKS = {
    "python_lint",
    "python_types",
    "backend_security",
    "frontend_components",
    "frontend_build",
    "frontend_e2e",
    "reviewed_quality",
    "source_unchanged",
}


def source_manifest():
    listing = (
        subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=ROOT,
            check=True,
            capture_output=True,
        )
        .stdout.decode()
        .split("\0")
    )
    # Include build/deployment scripts, dependencies, tests and new source files;
    # exclude private runtime state even if it was accidentally added to git.
    excluded = {".local", ".venv", ".models", ".git", "node_modules"}
    paths = {
        Path(name)
        for name in listing
        if name
        and not (set(Path(name).parts) & excluded)
        and Path(name).name != ".env"
        and not name.startswith("artifacts/private/")
    }
    files = {
        str(path): hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
        for path in sorted(paths)
        if (ROOT / path).is_file()
    }
    digest = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
    return files, digest


def quality_ready():
    baseline = json.loads(Path("datasets/baseline.json").read_text())
    return (
        baseline.get("status") == "accepted"
        and isinstance(baseline.get("recall_at_5"), (float, int))
        and bool(baseline.get("source_run"))
    )


def record(manifest, status):
    # Only metadata and hashes enter tenant-visible operations history.
    public = {
        key: manifest[key]
        for key in (
            "id",
            "name",
            "source_sha256",
            "schema_revision",
            "models",
            "generation",
            "checks",
            "image",
        )
        if key in manifest
    }
    with psycopg.connect(settings.database_admin_url) as conn:
        conn.execute(
            """INSERT INTO atlas.release_records(tenant_id,id,name,status,manifest)
            SELECT id,%s,%s,%s,%s FROM atlas.tenants
            ON CONFLICT(tenant_id,id) DO UPDATE SET status=excluded.status,manifest=excluded.manifest""",
            (manifest["id"], manifest["name"], status, Jsonb(public)),
        )


def verify(name, image):
    files, digest = source_manifest()
    with psycopg.connect(settings.database_admin_url) as conn:
        revision = conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    manifest = {
        "id": str(uuid4()),
        "name": name,
        "created_at": datetime.now(UTC).isoformat(),
        "source_sha256": digest,
        "files": files,
        "schema_revision": revision,
        "models": json.loads(Path("models/manifest.json").read_text()),
        "generation": json.loads(Path("models/generation.json").read_text()),
        "image": image,
        "checks": {},
        "status": "blocked",
    }
    destination = Path(".local/releases") / manifest["id"]
    destination.mkdir(parents=True, mode=0o700)
    checks = {
        "python_lint": [".venv/bin/ruff", "check", "src", "tests", "scripts", "migrations"],
        "python_types": [".venv/bin/mypy", "src"],
        "backend_security": [".venv/bin/python", "scripts/isolated.py", ".venv/bin/pytest", "-q"],
        "frontend_components": ["npm", "--prefix", "web", "run", "test:unit"],
        "frontend_build": [
            "npm",
            "--prefix",
            "web",
            "run",
            "build",
            "--",
            "--outDir",
            "../.local/workbench-e2e/dist",
        ],
        "frontend_e2e": [
            ".venv/bin/python",
            "scripts/isolated.py",
            ".venv/bin/python",
            "scripts/workbench_e2e.py",
            "run",
        ],
    }
    for label, command in checks.items():
        with (destination / (label + ".log")).open("w") as log:
            result = subprocess.run(command, stdout=log, stderr=log)
        manifest["checks"][label] = result.returncode == 0
        if result.returncode:
            break
    manifest["checks"]["reviewed_quality"] = False
    if all(manifest["checks"].get(key) is True for key in checks):
        if quality_ready():
            with (destination / "reviewed_quality.log").open("w") as log:
                result = subprocess.run(
                    [
                        ".venv/bin/python",
                        "scripts/isolated.py",
                        ".venv/bin/python",
                        "scripts/evaluate.py",
                        "--gate",
                    ],
                    stdout=log,
                    stderr=log,
                )
            manifest["checks"]["reviewed_quality"] = result.returncode == 0
    manifest["checks"]["source_unchanged"] = source_manifest()[1] == digest
    if all(manifest["checks"].values()) and len(manifest["checks"]) == len(checks) + 2:
        manifest["status"] = "accepted"
    path = destination / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2) + "\n")
    path.chmod(0o600)
    record(manifest, manifest["status"])
    print(f"Release {manifest['status']}: {path}")
    return manifest["status"] == "accepted"


def deployment_command(manifest, env_file):
    if (
        manifest.get("status") != "accepted"
        or not REQUIRED_CHECKS.issubset(manifest.get("checks", {}))
        or not all(manifest["checks"][key] is True for key in REQUIRED_CHECKS)
    ):
        raise ValueError(
            "Only a release with all quality and security checks passed can be selected"
        )
    if not re.fullmatch(r"[a-zA-Z0-9./:_-]+@sha256:[a-f0-9]{64}", manifest.get("image", "")):
        raise ValueError("A pinned container image digest is required")
    return [
        "docker",
        "compose",
        "--env-file",
        str(env_file),
        "-f",
        "deploy/compose.yaml",
        "up",
        "-d",
        "--no-deps",
        "api",
        "worker",
    ]


def deploy(path, env_file, rollback=False, dry_run=False):
    manifest = json.loads(path.read_text())
    command = deployment_command(manifest, env_file)
    with psycopg.connect(settings.database_admin_url) as conn:
        revision = conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    if revision != manifest["schema_revision"]:
        raise ValueError(
            "Release schema differs; use a verified recovery procedure, never an automatic down-migration"
        )
    if dry_run:
        print(
            json.dumps(
                {
                    "action": "rollback" if rollback else "activate",
                    "image": manifest["image"],
                    "command": command,
                }
            )
        )
        return
    if not env_file.is_file():
        raise ValueError("Operator deployment environment file is required")
    image = json.loads(
        subprocess.run(
            ["docker", "image", "inspect", manifest["image"]],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    )[0]
    if (image.get("Config", {}).get("Labels") or {}).get("io.atlas.source-sha256") != manifest[
        "source_sha256"
    ]:
        raise ValueError("Container source label does not match the verified release manifest")
    subprocess.run(command, env={**os.environ, "ATLAS_IMAGE": manifest["image"]}, check=True)
    # Mark the prior active release only after Docker accepted the selected rollout.
    with psycopg.connect(settings.database_admin_url) as conn:
        conn.execute("UPDATE atlas.release_records SET status='rolled_back' WHERE status='active'")
    record(manifest, "active")
    print(
        "Selected verified image. Check deployment readiness and the operations console before routing traffic."
    )


def main():
    os.chdir(ROOT)
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    check = actions.add_parser("verify")
    check.add_argument("--name", required=True)
    check.add_argument("--image", required=True, help="Repository image pinned with @sha256 digest")
    actions.add_parser("fingerprint")
    for action in ("activate", "rollback"):
        selected = actions.add_parser(action)
        selected.add_argument("manifest", type=Path)
        selected.add_argument("--env-file", type=Path, required=True)
        selected.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        if args.action == "fingerprint":
            print(source_manifest()[1])
        elif args.action == "verify":
            if not verify(args.name, args.image):
                raise SystemExit(1)
        else:
            deploy(args.manifest, args.env_file, args.action == "rollback", args.dry_run)
    except ValueError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
