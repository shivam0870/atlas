import pytest_asyncio

from atlas.db import pool


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def database():
    await pool.open(wait=True)
    yield
    await pool.close()
