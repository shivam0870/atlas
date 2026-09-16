import json
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from atlas.db import transaction
from atlas.retrieval import retrieve


class SearchArguments(BaseModel):
    question: str = Field(min_length=2, max_length=2000)
    top_k: int = Field(default=5, ge=1, le=10)


class StructuredArguments(BaseModel):
    name: str = Field(default="", max_length=100)
    kind: str = Field(default="service", pattern="^(service|deployment)$")


class CompareArguments(BaseModel):
    names: list[str] = Field(min_length=2, max_length=5)


class Evidence(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    content: str = Field(min_length=1)
    document_id: str | None = None
    kind: str = "document"
    similarity: float = 0
    start_offset: int = Field(default=0, ge=0)
    end_offset: int = Field(default=0, ge=0)


class EntityRecord(BaseModel):
    id: str
    name: str
    kind: Literal["service", "deployment"]
    attributes: dict


class ToolResult(BaseModel):
    sources: list[Evidence] = Field(default_factory=list)
    records: list[EntityRecord] = Field(default_factory=list)
    message: str = ""


def entity_source(row: dict) -> dict:
    return {
        "id": "entity:" + str(row["id"]),
        "document_id": None,
        "kind": "structured",
        "title": row["name"] + " · service inventory",
        "content": json.dumps(
            {"name": row["name"], "kind": row["kind"], **row["attributes"]}, indent=2
        ),
        "source_key": "inventory/" + str(row["id"]),
        "similarity": 1.0,
        "start_offset": 0,
        "end_offset": 0,
    }


async def search_documents(tenant_id: UUID, arguments: SearchArguments) -> ToolResult:
    rows, _, _, _ = await retrieve(tenant_id, arguments.question, arguments.top_k)
    return ToolResult(sources=rows, message=f"Retrieved {len(rows)} passages")


async def query_structured_data(tenant_id: UUID, arguments: StructuredArguments) -> ToolResult:
    async with transaction(tenant_id) as conn:
        rows = await (
            await conn.execute(
                "SELECT id,name,kind,attributes FROM atlas.entities WHERE tenant_id=%s AND kind=%s AND name ILIKE %s ORDER BY name LIMIT 20",
                (tenant_id, arguments.kind, "%" + arguments.name + "%"),
            )
        ).fetchall()
    records = [{**r, "id": str(r["id"])} for r in rows]
    return ToolResult(
        records=[EntityRecord.model_validate(r) for r in records],
        sources=[Evidence.model_validate(entity_source(r)) for r in rows],
        message=f"Found {len(rows)} inventory records",
    )


async def compare_items(tenant_id: UUID, arguments: CompareArguments) -> ToolResult:
    async with transaction(tenant_id) as conn:
        rows = await (
            await conn.execute(
                "SELECT id,name,kind,attributes FROM atlas.entities WHERE tenant_id=%s AND name=ANY(%s) ORDER BY name",
                (tenant_id, arguments.names),
            )
        ).fetchall()
    records = [{**r, "id": str(r["id"])} for r in rows]
    return ToolResult(
        records=[EntityRecord.model_validate(r) for r in records],
        sources=[Evidence.model_validate(entity_source(r)) for r in rows],
        message="Compare only returned items; missing names are not available in this workspace",
    )


async def execute_tool(tenant_id: UUID, name: str, arguments: dict) -> ToolResult:
    if name == "search_documents":
        return await search_documents(tenant_id, SearchArguments.model_validate(arguments))
    if name == "query_structured_data":
        return await query_structured_data(tenant_id, StructuredArguments.model_validate(arguments))
    if name == "compare_items":
        return await compare_items(tenant_id, CompareArguments.model_validate(arguments))
    raise ValueError("Unknown tool")
