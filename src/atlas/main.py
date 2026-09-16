import time
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from redis.asyncio import Redis

from atlas.api import router
from atlas.auth import Identity, authenticate
from atlas.config import settings
from atlas.db import pool, transaction
from atlas.evaluation import router as eval_router
from atlas.mcp_server import AuthenticatedMCP
from atlas.mcp_server import server as mcp_server
from atlas.telemetry import logger, requests, setup, tracer

redis = Redis.from_url(settings.redis_url, decode_responses=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup()
    await pool.open(wait=True)
    async with mcp_server.session_manager.run():
        yield
    await pool.close()
    await redis.aclose()


app = FastAPI(title="Atlas Knowledge API", version="0.1.0", lifespan=lifespan)
app.include_router(router)
app.include_router(eval_router)
app.mount("/mcp", AuthenticatedMCP())


@app.middleware("http")
async def request_context(request: Request, call_next):
    length = request.headers.get("content-length")
    if length and (not length.isdigit() or int(length) > settings.max_upload_bytes + 65536):
        return JSONResponse(
            {"detail": "Request body exceeds the upload size limit"}, status_code=413
        )
    # Browser cookie sessions need same-origin mutation protection; bearer clients do not use cookies.
    if request.method not in {"GET", "HEAD", "OPTIONS"} and not request.headers.get(
        "authorization"
    ):
        origin = request.headers.get("origin")
        expected = f"{request.url.scheme}://{request.url.netloc}"
        if request.headers.get("x-atlas-client") != "console" or (origin and origin != expected):
            return JSONResponse({"detail": "Same-origin console request required"}, status_code=403)
    request_id = str(uuid4())
    request.state.request_id = request_id
    started = time.perf_counter()
    with tracer.start_as_current_span(f"{request.method} request", end_on_exit=False) as span:
        span.set_attribute("request.id", request_id)
        try:
            response = await call_next(request)
        except BaseException:
            span.end()
            raise
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


if Path("web/dist").exists():
    app.mount("/", StaticFiles(directory="web/dist", html=True), name="console")
