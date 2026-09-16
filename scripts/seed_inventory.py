import asyncio
import json
from pathlib import Path
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from atlas.db import pool, transaction


async def main():
    await pool.open(wait=True)
    try:
        for w in json.loads(Path(".local/workspaces.json").read_text()):
            records = (
                [
                    (
                        "checkout",
                        {
                            "namespace": "commerce",
                            "replicas": 3,
                            "cpu_target_percent": 65,
                            "max_replicas": 12,
                            "owner": "Platform",
                            "synthetic": True,
                        },
                    ),
                    (
                        "inventory",
                        {
                            "namespace": "commerce",
                            "replicas": 2,
                            "cpu_target_percent": 60,
                            "max_replicas": 6,
                            "owner": "Supply",
                            "synthetic": True,
                        },
                    ),
                ]
                if w["slug"] == "acme"
                else [
                    (
                        "catalog",
                        {
                            "namespace": "discovery",
                            "replicas": 4,
                            "cpu_target_percent": 70,
                            "max_replicas": 8,
                            "owner": "Reliability",
                            "synthetic": True,
                        },
                    )
                ]
            )
            async with transaction(UUID(w["id"])) as conn:
                for name, attributes in records:
                    await conn.execute(
                        "INSERT INTO atlas.entities(tenant_id,id,name,kind,attributes) VALUES(%s,%s,%s,'service',%s) ON CONFLICT(tenant_id,name,kind) DO UPDATE SET attributes=excluded.attributes",
                        (UUID(w["id"]), uuid4(), name, Jsonb(attributes)),
                    )
    finally:
        await pool.close()
    print("Seeded isolated synthetic service inventories")


asyncio.run(main())
