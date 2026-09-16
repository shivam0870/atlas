from contextvars import ContextVar

from fastapi import HTTPException
from mcp.server.mcpserver import MCPServer
from starlette.requests import Request
from starlette.responses import JSONResponse

from atlas.auth import Identity, authenticate, require
from atlas.tools import CompareArguments, SearchArguments, StructuredArguments, ToolResult
from atlas.tools import compare_items as compare
from atlas.tools import query_structured_data as lookup
from atlas.tools import search_documents as search

identity_context: ContextVar[Identity] = ContextVar("mcp_identity")
server = MCPServer("Atlas", instructions="Read-only tools scoped to the authenticated workspace.")


@server.tool()
async def search_documents(question: str, top_k: int = 5) -> ToolResult:
    """Search only the authenticated tenant's document passages."""
    identity = identity_context.get()
    require(identity, "query")
    return await search(identity.tenant_id, SearchArguments(question=question, top_k=top_k))


@server.tool()
async def query_structured_data(name: str = "", kind: str = "service") -> ToolResult:
    """Read the tenant's structured service inventory using validated filters."""
    identity = identity_context.get()
    require(identity, "read")
    return await lookup(identity.tenant_id, StructuredArguments(name=name, kind=kind))


@server.tool()
async def compare_items(names: list[str]) -> ToolResult:
    """Compare named inventory items available in the authenticated workspace."""
    identity = identity_context.get()
    require(identity, "read")
    return await compare(identity.tenant_id, CompareArguments(names=names))


http_app = server.streamable_http_app(
    streamable_http_path="/", stateless_http=True, json_response=True
)


class AuthenticatedMCP:
    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await http_app(scope, receive, send)
        request = Request(scope, receive)
        if not request.headers.get("authorization", "").startswith("Bearer "):
            return await JSONResponse(
                {"detail": "Bearer authentication required"}, status_code=401
            )(scope, receive, send)
        try:
            identity = await authenticate(request)
        except HTTPException as exc:
            return await JSONResponse({"detail": exc.detail}, status_code=exc.status_code)(
                scope, receive, send
            )
        token = identity_context.set(identity)
        try:
            await http_app(scope, receive, send)
        finally:
            identity_context.reset(token)
