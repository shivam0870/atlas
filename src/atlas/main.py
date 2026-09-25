import time
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from redis.asyncio import Redis
from starlette.exceptions import HTTPException as StarletteHTTPException

from atlas import db
from atlas.accounts import router as accounts_router
from atlas.administration import router as administration_router
from atlas.api import router
from atlas.auth import Identity, authenticate
from atlas.config import settings
from atlas.conversations import router as conversations_router
from atlas.db import pool, transaction
from atlas.discovery import router as discovery_router
from atlas.evaluation import router as eval_router
from atlas.knowledge import router as knowledge_router
from atlas.mcp_server import AuthenticatedMCP
from atlas.mcp_server import server as mcp_server
from atlas.operations import router as operations_router
from atlas.organizations import router as organizations_router
from atlas.telemetry import logger, requests, setup, tracer
from atlas.workflows import router as workflows_router

redis = Redis.from_url(settings.redis_url, decode_responses=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup()
    await pool.open(wait=True)
    await db.verify_application_role()
    async with mcp_server.session_manager.run():
        yield
    await pool.close()
    await db.identity_pool.close()
    await db.application_pool.close()
    await redis.aclose()


app = FastAPI(title="Atlas Knowledge API", version="0.1.0", lifespan=lifespan)
app.include_router(router)
app.include_router(eval_router)
app.include_router(accounts_router)
app.include_router(organizations_router)
app.include_router(knowledge_router)
app.include_router(administration_router)
app.include_router(conversations_router)
app.include_router(discovery_router)
app.include_router(workflows_router)
app.include_router(operations_router)
app.mount("/mcp", AuthenticatedMCP())


@app.middleware("http")
async def request_context(request: Request, call_next):
    length = request.headers.get("content-length")
    if length and (not length.isdigit() or int(length) > settings.max_file_bytes + 65536):
        return JSONResponse(
            {"detail": "Request body exceeds the upload size limit"}, status_code=413
        )
    # Browser cookie sessions need same-origin mutation protection; bearer clients do not use cookies.
    if request.method not in {"GET", "HEAD", "OPTIONS"} and not request.headers.get(
        "authorization"
    ):
        origin = request.headers.get("origin")
        expected = settings.app_url.rstrip("/")
        if request.headers.get("x-atlas-client") != "console" or (origin and origin != expected):
            return JSONResponse({"detail": "Same-origin console request required"}, status_code=403)
    request_id = str(uuid4())
    request.state.request_id = request_id
    started = time.perf_counter()
    with tracer.start_as_current_span(f"{request.method} request", end_on_exit=False) as span:
        span.set_attribute("request.id", request_id)
        context_token = db.access_context.set(None)
        try:
            response = await call_next(request)
        except BaseException:
            span.end()
            raise
        finally:
            db.access_context.reset(context_token)
        span.set_attribute("http.response.status_code", response.status_code)
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    original = response.body_iterator

    async def observed_body():
        try:
            async for part in original:
                yield part
        finally:
            duration = (time.perf_counter() - started) * 1000
            span.set_attribute("http.response.duration_ms", duration)
            span.end()
            requests.add(1, {"method": request.method, "status": response.status_code})
            logger.info(
                "request",
                extra={
                    "fields": {
                        "request_id": request_id,
                        "method": request.method,
                        "status": response.status_code,
                        "duration_ms": round(duration, 2),
                    }
                },
            )

    response.body_iterator = observed_body()

    return response


@app.get("/health")
async def health():
    return {"status": "ok", "service": "atlas"}


@app.get("/ready")
async def ready():
    try:
        async with transaction() as conn:
            await conn.execute("SELECT 1")
        await redis.ping()
    except Exception:
        return JSONResponse({"status": "unavailable"}, status_code=503)
    return {"status": "ready"}


@app.get("/api/me")
async def me(identity: Identity = Depends(authenticate)):
    return {"tenant_id": str(identity.tenant_id), "name": identity.name, "scopes": identity.scopes}


class ConsoleFiles(StaticFiles):
    async def get_response(self, path, scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404 or path.startswith(("api/", "mcp/", "assets/")):
                raise
            if Path(path).suffix or scope["method"] not in {"GET", "HEAD"}:
                raise
            return await super().get_response("index.html", scope)


if Path("web/dist").exists():
    app.mount("/", ConsoleFiles(directory="web/dist", html=True), name="console")
