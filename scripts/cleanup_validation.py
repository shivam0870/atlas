"""Remove only synthetic browser-test documents and unreviewed labels for those documents."""

import asyncio

import psycopg
from psycopg.rows import dict_row

from atlas.config import settings
from atlas.db import pool, transaction


async def main():
    with psycopg.connect(settings.database_admin_url, row_factory=dict_row) as admin:
        tenant = admin.execute("SELECT id FROM atlas.tenants WHERE slug='acme'").fetchone()["id"]
    await pool.open(wait=True)
    try:
        async with transaction(tenant) as conn:
            await conn.execute(
                "DELETE FROM atlas.eval_labels WHERE tenant_id=%s AND reviewed=false AND document_id IN (SELECT id FROM atlas.documents WHERE tenant_id=%s AND title LIKE 'Browser validation %%')",
                (tenant, tenant),
            )
            await conn.execute(
                "UPDATE atlas.documents SET status='deleted' WHERE tenant_id=%s AND title LIKE 'Browser validation %%'",
                (tenant,),
            )
            await conn.execute(
                "UPDATE atlas.collections SET revision=revision+1 WHERE tenant_id=%s", (tenant,)
            )
    finally:
        await pool.close()
    print("Synthetic browser validation documents removed; human reviews preserved.")


asyncio.run(main())
