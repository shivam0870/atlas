"""Add curated workload pages to balance the alphabetically selected starter corpus."""

import asyncio
import hashlib
import json
import re
from pathlib import Path
from uuid import UUID

import httpx

from atlas.db import pool
from atlas.ingestion import create_document, index_document


async def main():
    path = Path("datasets/manifest.json")
    manifest = json.loads(path.read_text())
    tenant = UUID(
        next(
            w["id"]
            for w in json.loads(Path(".local/workspaces.json").read_text())
            if w["slug"] == "acme"
        )
    )
    extras = [
        "workloads/pods/pod-lifecycle.md",
        "workloads/pods/init-containers.md",
        "workloads/controllers/deployment.md",
        "workloads/autoscaling/horizontal-pod-autoscale.md",
        "services-networking/service.md",
        "services-networking/ingress.md",
        "storage/persistent-volumes.md",
        "workloads/pods/sidecar-containers.md",
    ]
    await pool.open(wait=True)
    try:
        async with httpx.AsyncClient(timeout=40, follow_redirects=True) as client:
            for suffix in extras:
                source = "content/en/docs/concepts/" + suffix
                if any(d["path"] == source for d in manifest["documents"]):
                    continue
                response = await client.get(
                    f"https://raw.githubusercontent.com/kubernetes/website/{manifest['revision']}/{source}"
                )
                if response.status_code == 404:
                    print("Pinned corpus has no " + suffix)
                    continue
                response.raise_for_status()
                raw = response.text
                match = re.search(r'^title:\s*["\']?(.+?)["\']?$', raw, re.M)
                title = match.group(1).strip("\"' ") if match else Path(source).stem
                content = re.sub(r"\A---.*?---\s*", "", raw, flags=re.S)
                content = re.sub(r"{{[%<].*?[>%]}}", "", content, flags=re.S).strip()
                filename = hashlib.sha256(source.encode()).hexdigest()[:16] + ".md"
                Path(".local/corpus", filename).write_text(content)
                item = {
                    "title": title,
                    "path": source,
                    "file": filename,
                    "sha256": hashlib.sha256(content.encode()).hexdigest(),
                    "source_url": "https://kubernetes.io/"
                    + source.removeprefix("content/en/").removesuffix(".md")
                    + "/",
                }
                doc = await create_document(
                    tenant,
                    title,
                    content,
                    source,
                    {
                        "source_url": item["source_url"],
                        "category": "Kubernetes documentation",
                        "license": "CC-BY-4.0",
                        "revision": manifest["revision"],
                    },
                )
                result = await index_document(tenant, doc["id"])
                manifest["documents"].append(item)
                path.write_text(json.dumps(manifest, indent=2) + "\n")
                print(title, result["chunks"], flush=True)
    finally:
        await pool.close()


asyncio.run(main())
