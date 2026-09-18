"""Fetch a pinned documentation corpus, retain attribution, and index it locally."""

import asyncio
import hashlib
import json
import re
from pathlib import Path
from uuid import UUID

import httpx
from maintenance import open_worker_pool

from atlas.db import pool
from atlas.ingestion import create_document, index_document

ROOT = Path(__file__).resolve().parents[1]
REPO = "https://api.github.com/repos/kubernetes/website"


async def main():
    manifest_path = ROOT / "datasets/manifest.json"
    manifest_path.parent.mkdir(exist_ok=True)
    corpus = ROOT / ".local/corpus"
    corpus.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text())
        else:
            result = await client.get(REPO + "/commits/main")
            result.raise_for_status()
            revision = result.json()["sha"]
            tree = await client.get(REPO + f"/git/trees/{revision}?recursive=1")
            tree.raise_for_status()
            candidates = [
                x["path"]
                for x in tree.json()["tree"]
                if x["path"].startswith("content/en/docs/concepts/")
                and x["path"].endswith(".md")
                and not x["path"].endswith("_index.md")
            ]
            manifest = {
                "repository": "kubernetes/website",
                "revision": revision,
                "license": "CC-BY-4.0",
                "documents": [],
                "extraction_version": 1,
            }
            for path in sorted(candidates):
                response = await client.get(
                    f"https://raw.githubusercontent.com/kubernetes/website/{revision}/{path}"
                )
                response.raise_for_status()
                raw = response.text
                if "include file=" in raw or "include_cached" in raw:
                    continue
                title_match = re.search(r'^title:\s*["\']?(.+?)["\']?$', raw, re.M)
                title = (
                    title_match.group(1).strip("\"'")
                    if title_match
                    else Path(path).stem.replace("-", " ").title()
                )
                content = re.sub(r"\A---.*?---\s*", "", raw, flags=re.S)
                content = re.sub(r"{{[%<].*?[>%]}}", "", content, flags=re.S).strip()
                if len(content) < 1000:
                    continue
                filename = hashlib.sha256(path.encode()).hexdigest()[:16] + ".md"
                (corpus / filename).write_text(content)
                manifest["documents"].append(
                    {
                        "title": title,
                        "path": path,
                        "file": filename,
                        "sha256": hashlib.sha256(content.encode()).hexdigest(),
                        "source_url": "https://kubernetes.io/"
                        + path.removeprefix("content/en/").removesuffix(".md")
                        + "/",
                    }
                )
                if len(manifest["documents"]) >= 100:
                    break
            manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
            (ROOT / "datasets/ATTRIBUTION.md").write_text(
                "# Corpus attribution\n\nKubernetes documentation, © The Kubernetes Authors, licensed CC BY 4.0.\nSource: https://github.com/kubernetes/website\nLicense: https://github.com/kubernetes/website/blob/main/LICENSE\n\nExtraction removes frontmatter and presentation shortcodes. Original paths and commit are in manifest.json.\n"
            )
        workspaces = json.loads((ROOT / ".local/workspaces.json").read_text())
        primary = UUID(next(w["id"] for w in workspaces if w["slug"] == "acme"))
        await open_worker_pool()
        try:
            for index, item in enumerate(manifest["documents"], 1):
                path = corpus / item["file"]
                if not path.exists():
                    response = await client.get(
                        f"https://raw.githubusercontent.com/kubernetes/website/{manifest['revision']}/{item['path']}"
                    )
                    response.raise_for_status()
                    content = re.sub(r"\A---.*?---\s*", "", response.text, flags=re.S)
                    content = re.sub(r"{{[%<].*?[>%]}}", "", content, flags=re.S).strip()
                    path.write_text(content)
                content = path.read_text()
                if hashlib.sha256(content.encode()).hexdigest() != item["sha256"]:
                    raise RuntimeError("Corpus checksum mismatch")
                doc = await create_document(
                    primary,
                    item["title"],
                    content,
                    item["path"],
                    {
                        "source_url": item["source_url"],
                        "category": "Kubernetes documentation",
                        "license": "CC-BY-4.0",
                        "revision": manifest["revision"],
                    },
                )
                result = await index_document(primary, doc["id"])
                print(
                    f"Indexed {index}/{len(manifest['documents'])}: {item['title']} ({result['chunks']} chunks)",
                    flush=True,
                )
            for workspace in workspaces:
                tenant = UUID(workspace["id"])
                is_acme = workspace["slug"] == "acme"
                content = (
                    "# Deployment operations handbook\n\nThis is a synthetic demonstration document, not production information.\n\n"
                    + (
                        "The Acme checkout service runs in namespace commerce with 3 replicas. Its CPU target is 65 percent and its autoscaling maximum is 12 replicas.\n\nThe Acme release approval code is AMBER-ORCHID. All checkout releases require approval from the Platform team. Roll back by restoring the previous image digest and confirming readiness probes pass.\n\nThe inventory service runs with 2 replicas. Its owner is the Supply team.\n"
                        if is_acme
                        else "The Northstar catalog service runs in namespace discovery with 4 replicas. Its CPU target is 70 percent and its autoscaling maximum is 8 replicas.\n\nThe Northstar release approval code is SILVER-MAPLE. All catalog releases require approval from the Reliability team.\n"
                    )
                )
                doc = await create_document(
                    tenant,
                    "Deployment operations handbook",
                    content,
                    "demo/operations-handbook",
                    {"category": "Team runbook", "synthetic": True},
                )
                await index_document(tenant, doc["id"])
        finally:
            await pool.close()
    print("Corpus indexed. Manifest and attribution saved; all usage is local.")


if __name__ == "__main__":
    asyncio.run(main())
