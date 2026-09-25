"""Pure deployment guards: dedicated targets and least-privilege process environments."""

import importlib
from pathlib import Path
from urllib.parse import urlsplit

import pytest

ROLES = {
    "DATABASE_ADMIN_URL": "atlas_admin",
    "DATABASE_URL": "atlas_app",
    "IDENTITY_DATABASE_URL": "atlas_identity",
    "WORKER_DATABASE_URL": "atlas_worker",
}


@pytest.fixture
def deployment_modules(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    return importlib.import_module("public_demo"), importlib.import_module("restore_backup")


def dedicated_config(tmp_path, overrides=None):
    values = {
        key: f"postgresql://{role}@127.0.0.1:55432/atlas_public_demo_review"
        for key, role in ROLES.items()
    }
    values.update(
        REDIS_URL="redis://127.0.0.1:56379/12", CACHE_REDIS_URL="redis://127.0.0.1:56380/12"
    )
    values.update(overrides or {})
    for name in ("migration.env", ".env", "worker.env"):
        (tmp_path / name).write_text("")
    (tmp_path / "migration.env").write_text(
        "\n".join(f"{key}={value}" for key, value in values.items())
    )
    return values


def test_demo_operator_accepts_one_dedicated_instance(tmp_path, deployment_modules):
    public, _ = deployment_modules
    values = dedicated_config(tmp_path)
    selected = public.operator_environment(tmp_path)
    assert all(selected[key] == value for key, value in values.items())


@pytest.mark.parametrize(
    "overrides",
    [
        {"DATABASE_URL": "postgresql://atlas_app@127.0.0.1:55432/atlas"},
        {
            "DATABASE_URL": "postgresql://atlas_app@127.0.0.1:55432/atlas_public_demo_review?dbname=atlas"
        },
        {
            "DATABASE_ADMIN_URL": "postgresql://atlas_admin@remote.test:55432/atlas_public_demo_review"
        },
        {"DATABASE_URL": "postgresql://atlas_admin@127.0.0.1:55432/atlas_public_demo_review"},
        {"WORKER_DATABASE_URL": ""},
        {"REDIS_URL": "redis://127.0.0.1:56379/0"},
        {"CACHE_REDIS_URL": "redis://127.0.0.1:56380/15"},
        {"REDIS_URL": "redis://127.0.0.1:56379/12?db=0"},
    ],
)
def test_demo_rejects_ordinary_targets_and_url_overrides(tmp_path, deployment_modules, overrides):
    public, _ = deployment_modules
    dedicated_config(tmp_path, overrides)
    with pytest.raises(SystemExit, match="dedicated"):
        public.operator_environment(tmp_path)


def test_demo_runtime_strips_inherited_operator_secrets_case_insensitively(
    monkeypatch, deployment_modules
):
    public, _ = deployment_modules
    monkeypatch.setenv("DATABASE_ADMIN_URL", "operator-only")
    monkeypatch.setenv("worker_database_url", "worker-only")
    monkeypatch.setenv("app_db_password", "operator-only")
    runtime = public.runtime_environment({"DATABASE_URL": "runtime-value"})
    assert not any(key.upper() in public.PRIVATE_KEYS for key in runtime)
    assert runtime["DATABASE_URL"] == "runtime-value"
    with pytest.raises(SystemExit, match="Migration credentials"):
        public.runtime_environment({"database_admin_url": "operator-only"})


def test_recovery_retargets_all_roles_from_one_instance(monkeypatch, deployment_modules):
    _, restore = deployment_modules
    for name, role in ROLES.items():
        monkeypatch.setattr(
            restore.settings,
            name.lower(),
            f"postgresql://{role}@127.0.0.1:55432/atlas_public_demo_review",
        )
    selected = restore.recovery_source_urls()
    assert {urlsplit(url).path for url in selected.values()} == {"/atlas_public_demo_review"}
    monkeypatch.setattr(
        restore.settings, "database_url", selected["DATABASE_URL"] + "?dbname=atlas"
    )
    with pytest.raises(ValueError, match="same source instance"):
        restore.recovery_source_urls()


def test_dedicated_scheduler_validates_before_any_backup(tmp_path, monkeypatch, deployment_modules):
    import sys

    public, _ = deployment_modules
    scheduler = importlib.import_module("operations_schedule")
    dedicated_config(tmp_path, {"DATABASE_URL": "postgresql://atlas_app@127.0.0.1:55432/atlas"})
    calls = []
    monkeypatch.setattr(scheduler, "tick", lambda: calls.append("backup"))
    monkeypatch.setattr(
        sys, "argv", ["operations_schedule.py", "--instance-root", str(tmp_path), "--once"]
    )
    with pytest.raises(SystemExit, match="dedicated"):
        scheduler.main()
    assert calls == []
