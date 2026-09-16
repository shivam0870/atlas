"""Exercise the real MCP SDK over HTTP without logging credentials."""

import asyncio
import json
from pathlib import Path

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def main():
    workspaces = json.loads(Path(".local/workspaces.json").read_text())
    results = []
    for workspace in workspaces:
        async with httpx2.AsyncClient(
            headers={"Authorization": "Bearer " + workspace["key"]}, timeout=60
        ) as http:
            async with streamable_http_client(
                "http://127.0.0.1:8100/mcp/", http_client=http
            ) as streams:
                async with ClientSession(streams[0], streams[1]) as session:
                    await session.initialize()
                    listing = await session.list_tools()
                    assert {t.name for t in listing.tools} == {
                        "search_documents",
                        "query_structured_data",
                        "compare_items",
                    }
                    result = await session.call_tool(
                        "query_structured_data", {"name": "", "kind": "service"}
                    )
                    assert not result.is_error, str(result)
                    data = result.structured_content
                    assert data
                    names = [r["name"] for r in data["records"]]
                    assert ("checkout" in names) == (workspace["slug"] == "acme")
                    search = await session.call_tool(
                        "search_documents", {"question": "release approval code", "top_k": 3}
                    )
                    assert not search.is_error, str(search)
                    results.append(
                        {
                            "workspace": workspace["name"],
                            "tools": len(listing.tools),
                            "records": names,
                            "search_passages": len(search.structured_content["sources"]),
                        }
                    )
    Path("artifacts/mcp-smoke.json").write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
