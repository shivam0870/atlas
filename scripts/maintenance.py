"""Explicit local operator database context for maintenance scripts, never imported by the API."""

from atlas.config import settings
from atlas.db import pool


async def open_worker_pool():
    if not settings.worker_database_url:
        raise RuntimeError("Local maintenance requires WORKER_DATABASE_URL")
    pool.conninfo = settings.worker_database_url
    await pool.open(wait=True)
